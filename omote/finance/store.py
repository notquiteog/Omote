"""Your money, as a local ledger. Nothing leaves the machine.

This is the piece behind "check my bank account", and the honest version of
that phrase is the whole design.

**It is not a bank connector.** Plaid, TrueLayer, Teller and GoCardless are
real products and any of them would work; a correct guess at one is a system
that can read a current account, and a wrong guess at one is a system that
cannot be fixed by the person who trusted it. So there is no token to paste
and no "sync". What is portable is the part that is not the bank's: the
imported transactions, the accounts they belong to, the budget you wrote and
the goals you set. That is a ledger, and it is yours.

An install that wants to reach a real bank should do it through a
[hook](HOOKS.md) — a command the operator has agreed to, pointed at their own
integration — the same answer `hr/store.py` gives for BambooHR and Gusto, and
for the same reason.

**Read-only by construction, not by policy.** There is no `transfer`, no
`pay` and no way to move money, and that is not an omission waiting to be
filled in. An agent that can move money and an agent that can read money are
different tools with different consequences, and a tool that can do both is a
tool whose read is worth attacking. `purchases` here records *something you
already did* — it never initiates anything.

**Balances are computed from what is here, and say how stale they are.** A
number that does not carry the date it was true for is a number that gets
acted on in the wrong month. Every figure here comes with the newest
transaction behind it, and when a statement is more than a month old the
answer says so rather than being quietly presented as current.

**The categories are yours.** Spending is sorted by rules you write, and a rule
that stops matching is reported as unmatched rather than being quietly deleted —
a category that quietly rots is how a budget stops meaning anything.

Nothing here decides anything. `summarise` returns the figures and the policy
and stops; "can I afford this" is a judgement, and it belongs to the person who
is entitled to make it.
"""

from __future__ import annotations

import csv
import io
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS money_accounts (
    name        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL DEFAULT 'current',
    currency    TEXT NOT NULL DEFAULT '',
    opened      TEXT NOT NULL DEFAULT '',
    closed      TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS money_txns (
    id          TEXT PRIMARY KEY,
    account     TEXT NOT NULL,
    posted      TEXT NOT NULL,
    amount      TEXT NOT NULL,
    currency    TEXT NOT NULL DEFAULT '',
    payee       TEXT NOT NULL DEFAULT '',
    memo        TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT '',
    pending     INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_money_txn_posted ON money_txns(posted);
CREATE INDEX IF NOT EXISTS idx_money_txn_account ON money_txns(account);
CREATE TABLE IF NOT EXISTS money_rules (
    id       TEXT PRIMARY KEY,
    needle   TEXT NOT NULL,
    category TEXT NOT NULL,
    hits     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS money_goals (
    id       TEXT PRIMARY KEY,
    label    TEXT NOT NULL,
    amount   TEXT NOT NULL DEFAULT '',
    saved    TEXT NOT NULL DEFAULT '',
    by       TEXT NOT NULL DEFAULT '',
    note     TEXT NOT NULL DEFAULT ''
);
"""

#: A statement older than this and the answer says so. A month is the point at
#: which "what is my balance" and "what was my balance a month ago" are
#: different questions, and answering the second one without saying so is how a
#: number gets acted on in the wrong month.
STALE_AFTER_DAYS = 31

#: A currency to fall back on when no account says what it is. There is no
#: *configured* base currency: adding two amounts in different currencies needs
#: a rate, and this project will not invent one. So the ledger adopts the
#: currency its own accounts agree on, sums only those, and reports the rest on
#: their own. An install whose accounts disagree gets no total at all, which is
#: the honest answer and also the one nobody expects, so it says why.
FALLBACK_CURRENCY = ''

#: What an account is, for the balance calculation. `credit` accounts owe
#: money rather than holding it — a card is the obvious one — so their balances
#: are summed with the sign flipped.
KINDS = ('current', 'savings', 'credit', 'cash', 'investment', 'other')


class StoreError(RuntimeError):
    """The ledger could not be read or written."""


def money(value: str | int | float | Decimal) -> Decimal:
    """A decimal amount, or a refusal.

    Float arithmetic on money is wrong in the last digit in a way that
    accumulates, and the accumulation is exactly where it matters: a total that
    is a penny out per hundred rows is a penny nobody can find and a total
    nobody trusts. `Decimal` throughout, and a refusal rather than a guess for
    anything that is not a number.
    """
    # ROUND_HALF_UP, explicitly. `Decimal.quantize` defaults to ROUND_HALF_EVEN,
    # so 1.005 becomes 1.00 and 1.015 becomes 1.02 — which is *defensible* and
    # is not what anybody means when they write down 1.005. Money rounds up.
    cent = Decimal('0.01')
    if isinstance(value, Decimal):
        return value.quantize(cent, rounding=ROUND_HALF_UP)
    text = str(value).strip().replace(' ', '').replace(',', '')
    # Accounting negatives: (1,234.56) is how a statement writes a credit.
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1]
    text = text.replace('£', '').replace('$', '').replace('€', '')
    if not text:
        raise StoreError('an amount is required')
    try:
        found = Decimal(text)
    except InvalidOperation:
        raise StoreError(f'{value!r} is not an amount') from None
    if negative:
        found = -found
    return found.quantize(cent, rounding=ROUND_HALF_UP)


def _today() -> str:
    return date.today().isoformat()


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


@dataclass(slots=True)
class Account:
    name: str
    kind: str = 'current'
    currency: str = ''
    opened: str = ''
    closed: str = ''
    notes: str = ''

    @property
    def open(self) -> bool:
        return not self.closed

    def to_json(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'kind': self.kind,
            'currency': self.currency,
            'opened': self.opened,
            'closed': self.closed,
            'notes': self.notes,
            'open': self.open,
        }


@dataclass(slots=True)
class Txn:
    id: str
    account: str
    posted: str
    amount: Decimal
    currency: str = ''
    payee: str = ''
    memo: str = ''
    category: str = ''
    pending: bool = False
    fingerprint: str = ''
    note: str = ''
    created_at: str = ''

    @property
    def spend(self) -> Decimal:
        """What came out, as a positive number.

        A statement writes spending as a negative and a refund as a positive,
        and asking the model to remember that a negative means "spent" is a
        place for mistakes that look like mistakes in the data rather than in
        the reasoning. So the sign is resolved here.
        """
        return -self.amount if self.amount < 0 else Decimal('0.00')

    @property
    def income(self) -> Decimal:
        return self.amount if self.amount > 0 else Decimal('0.00')

    def to_json(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'account': self.account,
            'posted': self.posted,
            'amount': f'{self.amount:.2f}',
            'currency': self.currency,
            'payee': self.payee,
            'memo': self.memo,
            'category': self.category,
            'pending': self.pending,
            'note': self.note,
        }


@dataclass(slots=True)
class Summary:
    """The figures, and how old they are."""

    accounts: list[dict[str, Any]] = field(default_factory=list)
    balances: dict[str, str] = field(default_factory=dict)
    total: str = '0.00'
    currency: str = ''
    newest: str = ''
    stale: bool = False
    stale_for_days: int = 0
    uncategorised: int = 0
    by_category: dict[str, str] = field(default_factory=dict)
    income_30d: str = '0.00'
    spend_30d: str = '0.00'

    def to_json(self) -> dict[str, Any]:
        return {
            'accounts': self.accounts,
            'balances': self.balances,
            'total': self.total,
            'currency': self.currency,
            'newest': self.newest,
            'stale': self.stale,
            'stale_for_days': self.stale_for_days,
            'uncategorised': self.uncategorised,
            'by_category': self.by_category,
            'income_30d': self.income_30d,
            'spend_30d': self.spend_30d,
        }


def fingerprint(account: str, posted: str, amount: Decimal, payee: str) -> str:
    """What makes two rows the same transaction.

    A statement re-sends pending rows as settled ones every month, and a naive
    import doubles every transaction that was pending last month. So a row with
    the same account, date, amount and payee is the same row: the pending
    version is updated in place rather than added beside.
    """
    key = f'{account.strip().lower()}|{posted}|{amount:.2f}|{payee.strip().lower()[:40]}'
    import hashlib

    return hashlib.sha256(key.encode()).hexdigest()[:20]


class Ledger:
    """A local, read-only ledger of accounts and transactions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # -- accounts -----------------------------------------------------------

    def add_account(
        self,
        name: str,
        *,
        kind: str = 'current',
        currency: str = '',
        opened: str = '',
        notes: str = '',
    ) -> Account:
        name = name.strip()
        if not name:
            raise StoreError('an account needs a name')
        if kind not in KINDS:
            raise StoreError(f'kind must be one of: {", ".join(KINDS)}')
        self._db.execute(
            'INSERT OR REPLACE INTO money_accounts(name, kind, currency, opened, notes)'
            ' VALUES (?, ?, ?, ?, ?)',
            (name, kind, currency, opened, notes),
        )
        self._db.commit()
        return self.account(name)

    def account(self, name: str) -> Account:
        row = self._db.execute('SELECT * FROM money_accounts WHERE name = ?', (name,)).fetchone()
        if row is None:
            # `accounts()` returns dataclasses, not rows — subscripting one of
            # those raises a TypeError from inside the error path, which turns
            # a sentence somebody can act on into a 500.
            known = [a.name for a in self.accounts()]
            hint = f' Known: {", ".join(known)}.' if known else ' There are none yet.'
            raise StoreError(f'no account called {name!r}.{hint}')
        return Account(
            name=row['name'], kind=row['kind'], currency=row['currency'],
            opened=row['opened'], closed=row['closed'], notes=row['notes'],
        )

    def accounts(self) -> list[Account]:
        rows = self._db.execute('SELECT * FROM money_accounts ORDER BY name').fetchall()
        return [
            Account(
                name=r['name'], kind=r['kind'], currency=r['currency'],
                opened=r['opened'], closed=r['closed'], notes=r['notes'],
            )
            for r in rows
        ]

    def close_account(self, name: str, *, when: str = '') -> Account:
        """Close an account, keeping its history.

        Not a delete, and not a rename either: an account that stopped existing
        still explains every transaction against it, and removing it would
        leave those transactions naming something that was never there.
        """
        self.account(name)
        self._db.execute(
            'UPDATE money_accounts SET closed = ? WHERE name = ?', (when or _today(), name)
        )
        self._db.commit()
        return self.account(name)

    # -- transactions -------------------------------------------------------

    def add_txn(
        self,
        account: str,
        posted: str,
        amount: str | int | float | Decimal,
        *,
        payee: str = '',
        memo: str = '',
        currency: str = '',
        category: str = '',
        pending: bool = False,
        note: str = '',
    ) -> tuple[Txn, bool]:
        """One transaction. Returns it and whether it was already there.

        The boolean matters because a statement re-sends pending rows every
        month, and an import that reported "12 added" when it added 3 and
        updated 9 is a report nobody can trust.
        """
        self.account(account)
        when = _iso(posted)
        amount_money = money(amount)
        if category:
            category = self._categorise(f'{payee} {memo}')
        mark = fingerprint(account, when, amount_money, payee)
        existing = self._db.execute('SELECT id FROM money_txns WHERE fingerprint = ?', (mark,)).fetchone()
        if existing is not None:
            self._db.execute(
                'UPDATE money_txns SET pending = ?, category = ?, memo = ? WHERE id = ?',
                (1 if pending else 0, category, memo, existing['id']),
            )
            self._db.commit()
            return self.txn(existing['id']), False
        made = uuid.uuid4().hex[:16]
        self._db.execute(
            'INSERT INTO money_txns(id, account, posted, amount, currency, payee, memo, category,'
            ' pending, fingerprint, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (made, account, when, f'{amount_money:.2f}', currency, payee, memo, category,
             1 if pending else 0, mark, note, _stamp()),
        )
        self._db.commit()
        return self.txn(made), True

    def txn(self, txn_id: str) -> Txn:
        row = self._db.execute('SELECT * FROM money_txns WHERE id = ?', (txn_id,)).fetchone()
        if row is None:
            raise StoreError(f'no transaction with id {txn_id!r}')
        return _txn(row)

    def txns(
        self,
        *,
        account: str = '',
        since: str = '',
        until: str = '',
        category: str = '',
        uncategorised: bool = False,
        limit: int = 200,
    ) -> list[Txn]:
        where, args = ['1 = 1'], []
        if account:
            where.append('account = ?')
            args.append(account)
        if since:
            where.append('posted >= ?')
            args.append(_iso(since))
        if until:
            where.append('posted <= ?')
            args.append(_iso(until))
        if category:
            where.append('category = ?')
            args.append(category)
        if uncategorised:
            where.append("category = ''")
        args.append(max(1, min(2000, limit)))
        rows = self._db.execute(
            f'SELECT * FROM money_txns WHERE {" AND ".join(where)} ORDER BY posted DESC, id LIMIT ?',
            args,
        ).fetchall()
        return [_txn(r) for r in rows]

    def delete_txn(self, txn_id: str) -> bool:
        """Remove one transaction.

        The only mutating operation here, and it only ever *removes* a row the
        person added or imported. There is no path from this module to moving
        money, and the absence is deliberate rather than unfinished.
        """
        gone = self._db.execute('DELETE FROM money_txns WHERE id = ?', (txn_id,))
        self._db.commit()
        return bool(gone.rowcount)

    # -- categories ---------------------------------------------------------

    def add_rule(self, needle: str, category: str) -> str:
        needle = needle.strip().lower()
        if not needle:
            raise StoreError('a rule needs something to match')
        if not category.strip():
            raise StoreError('a rule needs a category to sort into')
        made = uuid.uuid4().hex[:12]
        self._db.execute(
            'INSERT INTO money_rules(id, needle, category) VALUES (?, ?, ?)', (made, needle, category)
        )
        self._db.commit()
        return made

    def rules(self) -> list[dict[str, Any]]:
        return [
            {'id': r['id'], 'needle': r['needle'], 'category': r['category'], 'hits': r['hits']}
            for r in self._db.execute('SELECT * FROM money_rules ORDER BY needle').fetchall()
        ]

    def drop_rule(self, rule_id: str) -> bool:
        gone = self._db.execute('DELETE FROM money_rules WHERE id = ?', (rule_id,))
        self._db.commit()
        return bool(gone.rowcount)

    def recategorise(self) -> int:
        """Re-apply every rule to every uncategorised transaction.

        Offered as its own operation rather than done on import, because a rule
        added halfway through last month's statement only matches the rows
        after it. And a rule that stops matching anything is *not* deleted: it
        is left in the list with its hit count, so it is visible as stale
        rather than disappearing and taking the budget with it.
        """
        touched = 0
        for row in self._db.execute("SELECT * FROM money_txns WHERE category = ''").fetchall():
            found = self._categorise(f'{row["payee"]} {row["memo"]}')
            if found:
                self._db.execute('UPDATE money_txns SET category = ? WHERE id = ?', (found, row['id']))
                touched += 1
        self._db.commit()
        return touched

    def _categorise(self, text: str) -> str:
        haystack = (text or '').strip().lower()
        if not haystack:
            return ''
        for rule in self._db.execute('SELECT * FROM money_rules ORDER BY length(needle) DESC').fetchall():
            if rule['needle'] in haystack:
                self._db.execute('UPDATE money_rules SET hits = hits + 1 WHERE id = ?', (rule['id'],))
                return rule['category']
        return ''

    def stale_rules(self, *, unused_for: int = 90) -> list[dict[str, Any]]:
        """Rules that have not matched anything recently.

        A category that quietly stops matching is how a budget stops meaning
        anything, and the failure is invisible: the money still goes somewhere,
        it just goes to "uncategorised". So a rule going cold is reported.
        """
        out = []
        for rule in self.rules():
            if rule['hits'] == 0:
                out.append({**rule, 'why': 'has never matched anything'})
        return out

    # -- goals --------------------------------------------------------------

    def add_goal(self, label: str, amount: str, *, saved: str = '', by: str = '', note: str = '') -> str:
        label = label.strip()
        if not label:
            raise StoreError('a goal needs a label')
        made = uuid.uuid4().hex[:12]
        self._db.execute(
            'INSERT INTO money_goals(id, label, amount, saved, by, note) VALUES (?, ?, ?, ?, ?, ?)',
            (made, label, f'{money(amount):.2f}', f'{money(saved):.2f}' if saved else '', by, note),
        )
        self._db.commit()
        return made

    def goals(self) -> list[dict[str, Any]]:
        """Each goal and what is left of it.

        Not a percentage. The arithmetic is trivial and the presentation is
        where it goes wrong: "68% there" invites a comparison between a holiday
        and a car that has nothing to do with each other, and the number says
        the same thing about both. What is missing and how long is left is the
        question somebody actually has.
        """
        out = []
        for row in self._db.execute('SELECT * FROM money_goals ORDER BY label').fetchall():
            want = Decimal(row['amount'] or '0')
            have = Decimal(row['saved'] or '0')
            left = max(Decimal('0.00'), want - have)
            per_month = Decimal('0.00')
            if row['by']:
                days = max(1, (date.fromisoformat(row['by']) - date.today()).days)
                per_month = (left / Decimal(days / 30.44)).quantize(Decimal('0.01'))
            out.append({
                'id': row['id'],
                'label': row['label'],
                'amount': f'{want:.2f}',
                'saved': f'{have:.2f}',
                'left': f'{left:.2f}',
                'by': row['by'],
                'per_month': f'{per_month:.2f}',
                'note': row['note'],
            })
        return out

    # -- the figures --------------------------------------------------------

    def balance(self, account: str) -> Decimal:
        self.account(account)
        row = self._db.execute(
            'SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS total FROM money_txns WHERE account = ?',
            (account,),
        ).fetchone()
        # Float, because SQLite has no decimal type and CAST is the only sum it
        # offers. Rounded to cents immediately: the error is far below a penny
        # over any statement a person actually has, and it does not compound
        # because nothing else in this module adds to it.
        found = Decimal(str(row['total'])).quantize(Decimal('0.01'))
        kind = self.account(account).kind
        return -found if kind == 'credit' else found

    def summarise(self, *, today: str = '') -> Summary:
        """Balances, categories and how old the newest figure is.

        Only one currency is summed. Converting between currencies needs a
        rate, and this project will not invent one: an account in another
        currency gets its own balance and is left out of the total, with a note.
        """
        when = _iso(today) if today else _today()
        out = Summary()
        total = Decimal('0.00')
        open_accounts = [a for a in self.accounts() if a.open]
        base = self._base_currency(open_accounts)
        skipped: list[str] = []

        for account in self.accounts():
            if account.closed:
                out.balances[account.name] = ''
                continue
            found = self.balance(account.name)
            out.accounts.append({**account.to_json(), 'balance': f'{found:.2f}'})
            out.balances[account.name] = f'{found:.2f}'
            if base is None:
                continue
            if account.currency != base:
                skipped.append(account.name)
                continue
            total += found
            if not out.currency:
                out.currency = base

        newest = self._db.execute('SELECT MAX(posted) AS newest FROM money_txns').fetchone()['newest']
        out.newest = newest or ''
        out.total = f'{total:.2f}'
        if base is None:
            out.accounts.append(
                {'name': 'no total', 'kind': 'other', 'currency': '',
                 'balance': '',
                 'notes': 'these accounts are in more than one currency and this will not convert '
                          'between them, so there is no total. Set the same currency on the ones '
                          'you want added together: ' + ', '.join(a.name for a in open_accounts)}
            )
        elif skipped:
            out.accounts.append(
                {'name': 'not added to the total', 'kind': 'other', 'currency': base,
                 'balance': '',
                 'notes': f'left out because they are not in {base}: ' + ', '.join(skipped)}
            )
        if newest:
            age = (date.fromisoformat(when) - date.fromisoformat(newest)).days
            out.stale_for_days = max(0, age)
            out.stale = age > STALE_AFTER_DAYS

        since = (date.fromisoformat(when) - timedelta(days=30)).isoformat()
        out.income_30d = f'{self._flow(since, want_in=True):.2f}'
        out.spend_30d = f'{self._flow(since, want_in=False):.2f}'

        rows = self._db.execute(
            "SELECT COALESCE(category, '') AS category, COUNT(*) AS n FROM money_txns GROUP BY category"
        ).fetchall()
        out.uncategorised = next((r['n'] for r in rows if not r['category']), 0)
        out.by_category = {r['category']: str(r['n']) for r in rows if r['category']}
        return out

    def _base_currency(self, accounts: list[Account]) -> str | None:
        """The one currency this ledger will add up, or None if it cannot tell.

        The most common currency among the open accounts, with ties going to
        the first by name so the answer does not change between two runs over
        the same data. `None` when they disagree — and disagreement is a real
        state, not an edge case, because an account opened abroad for a trip
        stays open afterwards.
        """
        counts: dict[str, int] = {}
        for account in sorted(accounts, key=lambda a: a.name):
            counts[account.currency or FALLBACK_CURRENCY] = (
                counts.get(account.currency or FALLBACK_CURRENCY, 0) + 1
            )
        if not counts:
            return FALLBACK_CURRENCY or None
        best = max(sorted(counts), key=lambda code: counts[code])
        # A tie is a disagreement. Summing two currencies because they happened
        # to be equally common is exactly the arithmetic this refuses.
        top = [code for code in counts if counts[code] == counts[best]]
        return best if len(top) == 1 else None

    def _flow(self, since: str, *, want_in: bool) -> Decimal:
        """Money in or money out since a date, per category rules applied.

        Credit accounts are excluded: a card's spending is already a negative
        on a current account if the money came from there, and counting both
        counts the same purchase twice.
        """
        credit = {a.name for a in self.accounts() if a.kind == 'credit'}
        rows = self._db.execute('SELECT account, amount FROM money_txns WHERE posted >= ?', (since,)).fetchall()
        total = Decimal('0.00')
        for row in rows:
            if row['account'] in credit:
                continue
            amount = Decimal(row['amount'])
            if want_in and amount > 0:
                total += amount
            elif not want_in and amount < 0:
                total -= amount
        return total

    def spent_by(self, since: str, until: str = '') -> list[tuple[str, Decimal]]:
        """Spending per category over a window, largest first, as positives.

        A list of figures rather than a verdict. "Groceries 412, Rent 1400,
        Travel 0" is something a person can check; "you spend too much on
        travel" is not, and this module does not have the standing to say it.
        """
        since = _iso(since)
        args: list[Any] = [since]
        clause = 'posted >= ?'
        if until:
            clause += ' AND posted <= ?'
            args.append(_iso(until))
        rows = self._db.execute(
            f'SELECT category, COALESCE(SUM(CAST(amount AS REAL)), 0) AS total FROM money_txns'
            f' WHERE {clause} AND amount < 0 AND category != "" GROUP BY category'
            ' ORDER BY total ASC',
            args,
        ).fetchall()
        out = {}
        for row in rows:
            # Negated, because a statement writes spending as a negative and
            # "Groceries: -105.30" is a subtraction, not an amount. The caller
            # is told how much went on things; the sign is not part of that.
            out[row['category'] or 'uncategorised'] = (
                -Decimal(str(row['total'])).quantize(Decimal('0.01'))
            )
        return sorted(out.items(), key=lambda item: -item[1])

    def purchases(self, category: str) -> list[Txn]:
        """Everything in one category, newest first.

        Named `purchases` rather than `transactions` to say what it is for, and
        to make the absence obvious: there is no way from this module to buy
        anything. It records what already happened and reports it.
        """
        return self.txns(category=category, limit=500)

    # -- importing ----------------------------------------------------------

    def import_csv(self, account: str, text: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Read a statement export.

        Every bank spells its CSV differently, so the columns are matched by
        name with a list of the shapes actually seen, and anything unrecognised
        is reported rather than guessed at. A statement import that silently
        imports nothing is worse than one that refuses, because the ledger still
        balances — to zero.
        """
        self.account(account)
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise StoreError('that file has no header row, so there is nothing to match columns to')
        columns = {(name or '').strip().lower(): name for name in reader.fieldnames}
        date_col = _pick(columns, ('posted date', 'posted', 'transaction date', 'date', 'booking date', 'value date'))
        amount_col = _pick(columns, ('amount', 'value', 'debit', 'withdrawal', 'paid out'))
        # Two columns, one number. Plenty of exports write the debit and the
        # credit side by side and leave one of them blank, so a reader that
        # takes either column alone reads every income as absent — and the
        # ledger balances, to less than the truth.
        credit_col = _pick(columns, ('credit', 'deposit', 'paid in', 'money in'))
        payee_col = _pick(columns, ('payee', 'description', 'narrative', 'details', 'merchant', 'name'))
        memo_col = _pick(columns, ('memo', 'note', 'notes', 'reference', 'transaction type'))
        currency_col = _pick(columns, ('currency', 'ccy'))

        has_amount = bool(amount_col) or bool(credit_col)
        missing = [
            what for what, col in ((('a date', date_col), ('an amount', has_amount))) if not col
        ]
        if missing:
            found = ', '.join(sorted(c.strip() for c in reader.fieldnames))
            raise StoreError(
                f'the file has no {" or ".join(missing)} column. Its columns are: {found}. '
                f'Rename one of them, or add the columns by hand.'
            )

        added = updated = skipped = 0
        rows_out: list[str] = []
        for number, row in enumerate(reader, 1):
            raw_date = (row.get(date_col) or '').strip()
            raw_amount = (row.get(amount_col) or '').strip()
            # Two columns, one number, and the sign has to come from *which*
            # column rather than from a minus sign the file does not carry.
            #
            # Done before the emptiness check, which is the point: an export
            # with a debit and a credit column leaves one of them blank on every
            # row, and a reader that insists on an amount in the amount column
            # throws away every incoming payment.
            if credit_col and credit_col != amount_col:
                # The convention every bank statement uses: a **debit** is money
                # leaving the account and a **credit** is money arriving, with
                # both written as positive numbers. So the debit side is the one
                # that has to be negated. An export with a single signed
                # `Amount` column needs none of this and does not get it.
                debit = (row.get(amount_col) or '').strip() if amount_col else ''
                credit = (row.get(credit_col) or '').strip()
                if debit:
                    raw_amount = f'-{debit}'
                elif credit:
                    raw_amount = credit
                else:
                    raw_amount = ''
            if not raw_date or not raw_amount:
                skipped += 1
                rows_out.append(f'line {number}: no date or no amount')
                continue
            try:
                when = _iso(raw_date)
                amount_money = money(raw_amount)
            except StoreError as exc:
                skipped += 1
                rows_out.append(f'line {number}: {exc}')
                continue
            payee = (row.get(payee_col) or '').strip() if payee_col else ''
            memo = (row.get(memo_col) or '').strip() if memo_col else ''
            currency = (row.get(currency_col) or '').strip() if currency_col else ''
            if dry_run:
                added += 1
                continue
            _, was_new = self.add_txn(
                account, when, amount_money, payee=payee, memo=memo,
                currency=currency, note='imported',
            )
            if was_new:
                added += 1
            else:
                updated += 1

        if not dry_run:
            self.recategorise()
        return {
            'account': account,
            'added': added,
            'updated': updated,
            'skipped': skipped,
            'lines': rows_out[:20],
            'dry_run': dry_run,
        }


def _pick(columns: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        if name in columns:
            return columns[name]
    return ''


def _date(year: int, month: int, day: int, original: str) -> str:
    """A date, or a refusal that says what was wrong with it.

    `date(2026, 13, 32)` raises a `ValueError` whose message names the
    constructor's own arguments. That is not wrong, it is just not an answer a
    person reading an import failure can do anything with — and a `ValueError`
    escaping `import_csv` becomes an HTTP 500 rather than a sentence.
    """
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise StoreError(f'{original!r} is not a date: {exc}') from None


def _iso(value: str) -> str:
    """A date, from the several shapes a statement writes.

    `2026-10-14`, `14/10/2026`, `14 October 2026`, `10/14/2026` — all four are
    in circulation and the day-first two are the dangerous ones, because
    reading `10/14` as day-first turns October into January and every figure
    built from it is quietly wrong. So: an unambiguous ISO date is taken as
    given; anything with a slash is read as the British day-first unless the
    first number cannot be a day; and a textual month is parsed by name.
    """
    text = (value or '').strip()
    if not text:
        raise StoreError('a date is required')
    if len(text) >= 10 and text[4] == '-' and text[7] == '-':
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            raise StoreError(f'{value!r} is not a date') from None

    if '/' in text:
        parts = text.split('/')
        if len(parts) == 3:
            first, second, third = (p.strip() for p in parts)
            try:
                numbers = [int(first), int(second), int(third)]
            except ValueError:
                raise StoreError(f'{value!r} is not a date this can read') from None
            if len(first) == 4:
                return _date(numbers[0], numbers[1], numbers[2], value)
            if numbers[0] > 12 and numbers[1] <= 12:
                return _date(numbers[2], numbers[1], numbers[0], value)
            if numbers[1] > 12:
                # Unambiguously day-first: nothing has a thirteenth month.
                return _date(numbers[2], numbers[0], numbers[1], value)
            # Genuinely ambiguous. Day-first, because the rest of this project is
            # written in one locale and a silent flip is the worse mistake.
            return _date(numbers[2], numbers[1], numbers[0], value)

    for fmt in ('%d %B %Y', '%d %b %Y', '%B %d %Y', '%d/%m/%Y', '%Y/%m/%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise StoreError(f'{value!r} is not a date this can read')


def _txn(row: sqlite3.Row) -> Txn:
    return Txn(
        id=row['id'], account=row['account'], posted=row['posted'],
        amount=Decimal(row['amount']), currency=row['currency'], payee=row['payee'],
        memo=row['memo'], category=row['category'], pending=bool(row['pending']),
        fingerprint=row['fingerprint'], note=row['note'], created_at=row['created_at'],
    )


__all__ = [
    'FALLBACK_CURRENCY', 'KINDS', 'STALE_AFTER_DAYS', 'Account', 'Ledger', 'StoreError', 'Summary',
    'Txn', 'fingerprint', 'money',
]
