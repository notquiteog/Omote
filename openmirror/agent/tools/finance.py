"""Your money, gathered up and handed over. Not judged.

**The division of labour, and it is the same one the `hr` tool uses.** This
tool gathers *figures* — balances, spending by category, what is left of each
goal, how stale the newest figure is — and the model reasons about them. A
tool that answered "you can afford this" would have taken a decision that
belongs to the person, out of a dozen imported rows, and dressed it up as
analysis. So there is no `afford` action and there never will be one: the
question is asked by a person and answered by a person, and the ledger's job is
to make sure they are answering it about the right numbers.

**Read-only by construction, not by policy.** No action here moves money. There
is no transfer, no payment, no standing order and no way from this module to a
bank's API — see `openmirror/finance/store.py` for why that is the design
rather than an omission. Every write here either records something that already
happened or changes how it is labelled.

**It says how stale it is.** A balance with no date is a balance that gets
acted on in the wrong month, so `summarise` carries the newest transaction
behind it and marks the answer stale past a month. A model asked "what is my
balance" gets told "£2,274.70, as of 12 days ago" rather than a bare figure it
will present as current.

**The categories are the person's.** Spending is sorted by rules they wrote, and
`uncategorised` is reported as a number rather than being hidden: a budget that
quietly stops matching is how a budget stops meaning anything.

Nothing is sent anywhere. The ledger is a local SQLite file under the data
directory. An install that wants to reach a real bank should do it through a
[hook](https://github.com/notquiteog/openmirror/blob/main/docs/HOOKS.md) — a
command the operator has agreed to — rather than through code here guessing at
somebody's API.
"""

from __future__ import annotations

from typing import Any

from openmirror.agent.tools.base import Assessment, Output, Tool, ToolContext, ToolError
from openmirror.finance.store import Ledger, StoreError
from openmirror.protocol.agent import Risk

ACTIONS = (
    'accounts', 'add_account', 'close_account', 'transactions', 'add', 'remove',
    'rules', 'add_rule', 'drop_rule', 'recategorise', 'summarise', 'spent',
    'categories', 'goals', 'add_goal', 'import',
)

RISK: dict[str, Risk] = {
    'accounts': Risk.READ,
    'add_account': Risk.WRITE,     # recording an account is a change to the ledger
    'close_account': Risk.WRITE,
    'transactions': Risk.READ,
    'add': Risk.WRITE,
    # Deleting is a write and not a destroy: it removes a row this person
    # added or imported, and there is no path from here to a bank.
    'remove': Risk.WRITE,
    'rules': Risk.READ,
    'add_rule': Risk.WRITE,
    'drop_rule': Risk.WRITE,
    'recategorise': Risk.WRITE,
    'summarise': Risk.READ,
    'spent': Risk.READ,
    'categories': Risk.READ,
    'goals': Risk.READ,
    'add_goal': Risk.WRITE,
    'import': Risk.WRITE,
}

REQUIRED: dict[str, tuple[str, ...]] = {
    'add_account': ('name',),
    'add': ('account', 'date', 'amount'),
    'remove': ('id',),
    'add_rule': ('match', 'category'),
    'drop_rule': ('id',),
    'add_goal': ('label', 'amount'),
    'import': ('account', 'csv'),
    'close_account': ('name',),
}


class FinanceTool(Tool):
    name = 'finance'
    description = (
        'Their money, as a local ledger. Accounts, transactions, spending by category and '
        'savings goals, imported from statements they already have.\n\n'
        'What this cannot do is move money. There is no action here that spends anything, and '
        'that is deliberate — an agent that can read money and an agent that can move money are '
        'different tools with different consequences. If they want to pay something, that is a '
        'browser task with a purchase approval, and you should say so rather than looking for '
        'something here.\n\n'
        'What it gives you is the figures and how old they are. Every answer carries the date of '
        'the newest transaction behind it, because a balance with no date gets acted on in the '
        'wrong month. You do the reasoning: there is deliberately no "can I afford this" here, '
        'because that judgement is theirs. Give them the numbers, the categories, what is left of '
        'each goal, and any spending you cannot place.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': list(ACTIONS)},
            'account': {'type': 'string', 'description': 'Which account.'},
            'name': {'type': 'string', 'description': 'For add_account / close_account.'},
            'kind': {
                'type': 'string',
                'enum': ['current', 'savings', 'credit', 'cash', 'investment', 'other'],
                'description': 'What an account is for. A credit account (a card) has its sign '
                               'flipped, because it owes money rather than holding it.',
            },
            'currency': {'type': 'string', 'description': 'ISO code. Only one currency is summed.'},
            'date': {'type': 'string', 'description': 'YYYY-MM-DD. Several other formats are read.'},
            'amount': {
                'type': 'string',
                'description': 'Negative for money out. Accounting negatives in parentheses are '
                               'understood.',
            },
            'payee': {'type': 'string'},
            'memo': {'type': 'string'},
            'category': {'type': 'string'},
            'id': {'type': 'string', 'description': 'For remove / drop_rule.'},
            'match': {'type': 'string', 'description': 'Substring of payee or memo, lowercased.'},
            'since': {'type': 'string', 'description': 'YYYY-MM-DD.'},
            'until': {'type': 'string', 'description': 'YYYY-MM-DD.'},
            'csv': {
                'type': 'string',
                'description': 'The statement text, header row and all. Columns are matched by '
                               'name; a file with no recognisable date or amount column is '
                               'refused rather than imported as nothing.',
            },
            'dry_run': {
                'type': 'boolean',
                'description': 'For import: read it and report what would happen, change nothing.',
            },
            'saved': {'type': 'string', 'description': 'For add_goal: how much is already put by.'},
            'by': {'type': 'string', 'description': 'For add_goal: the date it is wanted by.'},
            'label': {'type': 'string', 'description': 'For add_goal.'},
        },
        'required': ['action'],
    }

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        action = str(args.get('action') or '').strip()
        if action not in ACTIONS:
            return Assessment(
                risk=Risk.READ, summary='',
                invalid=f"action must be one of: {', '.join(ACTIONS)}",
            )
        for key in REQUIRED.get(action, ()):
            if not str(args.get(key) or '').strip():
                return Assessment(risk=RISK[action], summary='', invalid=f'{key} is required')

        what = args.get('payee') or args.get('name') or args.get('label') or args.get('account') or ''
        said = f'{action} {what}'.strip()
        return Assessment(risk=RISK[action], summary=said[:100])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        action = str(args['action'])
        handler = getattr(self, _HANDLERS[action], None)
        if handler is None:  # pragma: no cover - ACTIONS and _HANDLERS are asserted equal
            raise ToolError(f'{action} is not something this tool knows how to do')
        try:
            return handler(args, ctx)
        except StoreError as exc:
            raise ToolError(str(exc)) from exc

    # -- the actions --------------------------------------------------------

    def _accounts(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        found = self.ledger.accounts()
        lines = []
        for account in found:
            mark = '' if account.open else ' (closed)'
            balance = f'{self.ledger.balance(account.name):.2f}' if account.open else '—'
            lines.append(f'{account.name}{mark} — {account.kind}, {balance} {account.currency}'.rstrip())
        return Output(
            content='\n'.join(lines) if lines else 'There are no accounts yet. add_account to make one.',
            display={'accounts': [a.to_json() for a in found]},
        )

    def _add_account(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        made = self.ledger.add_account(
            str(args['name']),
            kind=str(args.get('kind') or 'current'),
            currency=str(args.get('currency') or ''),
            notes=str(args.get('memo') or ''),
        )
        return Output(content=f'Added {made.name}.', display=made.to_json())

    def _close_account(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        made = self.ledger.close_account(str(args['name']))
        return Output(
            content=(
                f'{made.name} is closed from {made.closed}. Its transactions are kept — an account '
                'that stopped existing still explains every row against it.'
            ),
            display=made.to_json(),
        )

    def _transactions(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        rows = self.ledger.txns(
            account=str(args.get('account') or ''),
            since=str(args.get('since') or ''),
            until=str(args.get('until') or ''),
            category=str(args.get('category') or ''),
            uncategorised=bool(args.get('uncategorised')),
            limit=int(args.get('limit') or 200),
        )
        if not rows:
            return Output(content='No transactions match.', display={'transactions': []})
        lines = [
            f'{t.posted}  {t.amount:>10.2f}  {t.payee or t.memo or "(no payee)"}'
            f'{"  [" + t.category + "]" if t.category else "  [uncategorised]"}'
            for t in rows
        ]
        return Output(
            content='\n'.join(lines), display={'transactions': [t.to_json() for t in rows]}
        )

    def _add(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        made, was_new = self.ledger.add_txn(
            str(args['account']), str(args['date']), str(args['amount']),
            payee=str(args.get('payee') or ''),
            memo=str(args.get('memo') or ''),
            currency=str(args.get('currency') or ''),
            category=str(args.get('category') or ''),
        )
        return Output(
            content=(
                f'Recorded {made.posted} {made.amount:.2f} on {made.account}.'
                if was_new
                else f'Updated the pending row that became {made.posted} {made.amount:.2f}.'
            ),
            display=made.to_json(),
        )

    def _remove(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        gone = self.ledger.delete_txn(str(args['id']))
        return Output(
            content='Removed.' if gone else f'No transaction with id {args["id"]!r}.',
            display={'removed': gone, 'id': args['id']},
        )

    def _rules(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        found = self.ledger.rules()
        if not found:
            return Output(content='No category rules yet.', display={'rules': []})
        cold = self.ledger.stale_rules()
        lines = [f'  "{r["needle"]}" -> {r["category"]} ({r["hits"]} matches)' for r in found]
        note = ''
        if cold:
            # A rule that quietly stops matching is how a budget stops meaning
            # anything, and the failure is invisible: the money still goes
            # somewhere, it just goes to uncategorised.
            note = '\n\nThese have never matched anything: ' + ', '.join(
                f'"{r["needle"]}"' for r in cold
            )
        return Output(content='\n'.join(lines) + note, display={'rules': found, 'cold': cold})

    def _add_rule(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        made = self.ledger.add_rule(str(args['match']), str(args['category']))
        touched = self.ledger.recategorise()
        return Output(
            content=(
                f'Rule added and applied to {touched} uncategorised '
                f'transaction{"s" if touched != 1 else ""}.'
            ),
            display={'id': made, 'recategorised': touched},
        )

    def _drop_rule(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        gone = self.ledger.drop_rule(str(args['id']))
        return Output(content='Removed.' if gone else 'No such rule.', display={'removed': gone})

    def _recategorise(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        touched = self.ledger.recategorise()
        left = self.ledger.summarise().uncategorised
        return Output(
            content=(
                f'Re-applied the rules. {touched} transactions sorted, {left} still uncategorised.'
            ),
            display={'sorted': touched, 'uncategorised': left},
        )

    def _summarise(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        report = self.ledger.summarise()
        if not report.accounts:
            return Output(
                content=(
                    'There is nothing here yet — no accounts, so no figures. Ask them to add one, '
                    'or import a statement.'
                ),
                display=report.to_json(),
            )
        lines = []
        for account in report.accounts:
            if account.get('balance'):
                lines.append(f'{account["name"]}: {account["balance"]} {account.get("currency", "")}'.rstrip())
        lines.append(f'Total: {report.total} {report.currency}'.rstrip())

        # The date, always. A figure with no date beside it gets acted on in
        # the wrong month, and the person reading it has no way to know.
        if report.newest:
            when = (
                f'as of {report.newest}'
                + (f', which is {report.stale_for_days} days old' if report.stale else '')
            )
        else:
            when = 'with no transactions behind it'
        lines.append(f'({when})')

        lines.append(f'Last 30 days: {report.income_30d} in, {report.spend_30d} out.')
        if report.uncategorised:
            many = report.uncategorised != 1
            lines.append(
                f'{report.uncategorised} transaction{"s have" if many else " has"} no category. '
                '`finance rules` lists the rules; `recategorise` re-applies them.'
            )
        if report.stale:
            lines.append(
                'That is old enough that it may not be your balance any more. Say so, or suggest '
                'importing a fresher statement.'
            )
        return Output(content='\n'.join(lines), display=report.to_json())

    def _spent(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        since = str(args.get('since') or '')
        if not since:
            from datetime import date, timedelta

            since = (date.today() - timedelta(days=30)).isoformat()
        found = self.ledger.spent_by(since, str(args.get('until') or ''))
        if not found:
            return Output(
                content=f'Nothing categorised spent since {since}.',
                display={'since': since, 'categories': []},
            )
        lines = [f'  {name}: {amount:.2f}' for name, amount in found]
        total = sum((amount for _, amount in found), start=0)
        lines.append(f'  total: {total:.2f}')
        lines.append('')
        lines.append(
            'These are figures, not a verdict. If they say something to you about a category, '
            'say so and let them decide whether it is a problem.'
        )
        return Output(
            content=f'Categorised spending since {since}:\n' + '\n'.join(lines),
            display={'since': since, 'categories': [{'name': n, 'amount': f'{a:.2f}'} for n, a in found]},
        )

    def _categories(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        since = str(args.get('since') or '')
        if not since:
            from datetime import date, timedelta

            since = (date.today() - timedelta(days=30)).isoformat()
        rows = self.ledger.spent_by(since)
        total = sum((a for _, a in rows), start=0)
        if not total:
            return Output(content='Nothing categorised in that window.', display={'categories': []})
        lines = [
            f'  {name}: {amount:.2f} ({amount / total:.0%})' for name, amount in rows
        ]
        return Output(content='\n'.join(lines), display={'categories': [n for n, _ in rows]})

    def _goals(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        found = self.ledger.goals()
        if not found:
            return Output(content='No goals set.', display={'goals': []})
        lines = [
            f'  {g["label"]}: {g["saved"]} of {g["amount"]}, {g["left"]} to go'
            + (f' — about {g["per_month"]} a month by {g["by"]}' if g['by'] else '')
            for g in found
        ]
        lines.append('')
        lines.append(
            'No percentages: "68% there" invites comparing a holiday with a car, and the same '
            'number says the same thing about both. What is left, and what it would take a month.'
        )
        return Output(content='\n'.join(lines), display={'goals': found})

    def _add_goal(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        made = self.ledger.add_goal(
            str(args['label']), str(args['amount']),
            saved=str(args.get('saved') or ''), by=str(args.get('by') or ''),
            note=str(args.get('memo') or ''),
        )
        return Output(content='Added the goal.', display={'id': made})

    def _import(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        report = self.ledger.import_csv(
            str(args['account']), str(args['csv']), dry_run=bool(args.get('dry_run'))
        )
        head = 'Would import' if report['dry_run'] else 'Imported'
        lines = [
            f'{head}: {report["added"]} added, {report["updated"]} already there, '
            f'{report["skipped"]} skipped.'
        ]
        if report['updated']:
            lines.append(
                f'The {report["updated"]} that were already there are rows this ledger has seen '
                'before — usually a pending transaction settling. They were updated rather than '
                'added, so nothing is counted twice.'
            )
        if report['lines']:
            lines.append('')
            lines.append('Lines it could not read:')
            lines.extend(f'  {line}' for line in report['lines'])
        return Output(content='\n'.join(lines), display=report)


#: Action to method name. Names, not functions.
#:
#: A dict of unbound methods reads better and is a bug: `FinanceTool._accounts`
#: is a plain function at that point, so calling it needs the instance passed
#: by hand and every entry has to remember to. Names looked up on `self` cannot
#: be wired that way — a wrong name raises on the first call rather than at
#: import, which is why `ACTIONS` and this are asserted to be the same set in
#: the tests.
_HANDLERS: dict[str, str] = {
    'accounts': '_accounts',
    'add_account': '_add_account',
    'close_account': '_close_account',
    'transactions': '_transactions',
    'add': '_add',
    'remove': '_remove',
    'rules': '_rules',
    'add_rule': '_add_rule',
    'drop_rule': '_drop_rule',
    'recategorise': '_recategorise',
    'summarise': '_summarise',
    'spent': '_spent',
    'categories': '_categories',
    'goals': '_goals',
    'add_goal': '_add_goal',
    'import': '_import',
}


def finance_tools(ledger: Ledger) -> list[Tool]:
    return [FinanceTool(ledger)]
