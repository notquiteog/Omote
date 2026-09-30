"""Your money, as a ledger. Three things it must never do.

**It must never move money.** Not "not by default" and not "behind a flag" —
there is no action that spends, and there is no path from this module to a
bank's API. An agent that can read money and an agent that can move money are
different tools with different consequences, and a tool that does both is a
tool whose read is worth attacking. The first test below is the one that
matters most, and it is a refusal to add the capability rather than a test of
something already there.

**It must never present a figure without its date.** A balance with no date
beside it gets acted on in the wrong month, and the person reading it has no
way to know. So every answer carries the newest transaction behind it, and
past a month it says so.

**It must never guess a date.** Every bank spells its export differently, and
two of the common spellings are ambiguous in opposite directions. Reading
`10/14/2026` as day-first turns October into January, and every figure built
from it is quietly wrong — which is the failure mode with no symptom at all.
"""

from __future__ import annotations

import pytest

from openmirror.agent.tools.base import ToolContext
from openmirror.agent.tools.finance import _HANDLERS, ACTIONS, RISK, FinanceTool
from openmirror.finance.store import STALE_AFTER_DAYS, Ledger, StoreError, money
from openmirror.protocol.agent import Risk


@pytest.fixture
def ledger(tmp_path):
    made = Ledger(tmp_path / 'finance.db')
    yield made
    made.close()


def ctx(tmp_path):
    return ToolContext(root=tmp_path, cwd=tmp_path, emit=None, ask=None, session_id='s')


# --- what it cannot do -------------------------------------------------------


def test_there_is_no_action_that_spends_money():
    """The whole of the safety argument, as one assertion.

    Every name in the action list is checked against a list of the things that
    would be a way of moving money. This test exists to be edited: somebody
    adding `pay` or `transfer` has to come here and say why, and the reason has
    to be good, because this is the only thing standing between an agent that
    can read a bank balance and an agent that can empty one.
    """
    forbidden = (
        'pay', 'transfer', 'send_money', 'withdraw', 'deposit', 'purchase', 'buy',
        'card', 'card_payment', 'standing_order', 'direct_debit', 'sepa', 'wire',
        'move_money', 'spend', 'charge', 'refund', 'payout', 'invoice', 'subscribe',
    )
    for action in ACTIONS:
        assert not any(word in action for word in forbidden), (
            f'{action!r} sounds like moving money. If it does not, say so here — otherwise '
            f'somebody reading this later cannot tell which is which.'
        )


def test_nothing_in_the_ledger_module_can_reach_a_network():
    """Same argument, at the layer below the tool.

    The tool's action list is a promise about the names. This is the promise
    about the code: a local SQLite file and `csv`, and nothing that could carry
    a transaction to a bank even if a future action tried to.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / 'openmirror' / 'finance' / 'store.py').read_text()
    for reacher in ('aiohttp', 'httpx', 'requests', 'urllib', 'socket', 'http.client'):
        assert reacher not in source, f'{reacher} appeared in the money ledger'


def test_every_action_is_wired_and_every_wiring_is_reachable():
    """A dispatch table and an action list drift apart silently.

    The first version of this tool looked the handlers up as *unbound methods*
    in a dict built from the class body — `FinanceTool._accounts` is a plain
    function at that point, so every call needed `self` passed by hand. It
    looked fine and it failed on the first action a person used. Names cannot
    be wired that way: a wrong one raises when it is called rather than at
    import, which is what this pair of assertions is for.
    """
    assert set(ACTIONS) == set(_HANDLERS), set(ACTIONS) ^ set(_HANDLERS)
    assert set(ACTIONS) == set(RISK), 'an action with no risk grade is one nobody can be asked about'
    tool = FinanceTool(Ledger.__new__(Ledger))
    for name in _HANDLERS.values():
        assert callable(getattr(tool, name, None)), f'{name} is named but not defined'


# --- amounts -----------------------------------------------------------------


@pytest.mark.parametrize(
    'text,expected',
    [
        ('1.25', '1.25'),
        ('  £2,400.50 ', '2400.50'),
        ('-40', '-40.00'),
        ('(120.00)', '-120.00'),          # accounting negative
        ('$18.99', '18.99'),
        ('0', '0.00'),
    ],
)
def test_an_amount_is_read_the_way_a_statement_writes_it(text, expected):
    assert f'{money(text):.2f}' == expected


def test_something_that_is_not_an_amount_is_refused_rather_than_zero():
    """Zero is a number that means something, so a typo must not become one."""
    for bad in ('', '   ', 'twelve pounds', '£'):
        with pytest.raises(StoreError):
            money(bad)


def test_amounts_are_decimals_and_not_floats():
    """Float arithmetic on money is wrong in the last digit in a way that
    accumulates, and the accumulation is exactly where it matters."""
    assert money('0.10') + money('0.20') == money('0.30')
    # ROUND_HALF_UP, not the banker's rounding `Decimal.quantize` defaults to.
    # Half-even turns 1.005 into 1.00 and 1.015 into 1.02, which is defensible
    # and is not what anybody means when they write down 1.005.
    assert money('1.005') == money('1.01')
    assert money('1.015') == money('1.02')
    assert money('2.675') == money('2.68')


# --- dates -------------------------------------------------------------------


@pytest.mark.parametrize(
    'text,expected',
    [
        ('2026-10-14', '2026-10-14'),
        ('2026-10-14T09:12:00Z', '2026-10-14'),
        ('14 October 2026', '2026-10-14'),
        ('14 Oct 2026', '2026-10-14'),
        ('14/10/2026', '2026-10-14'),     # unambiguously day-first
        ('10/14/2026', '2026-10-14'),     # first number cannot be a day
        ('01/02/2026', '2026-02-01'),     # ambiguous: day-first, stated
    ],
)
def test_a_date_is_read_from_the_shapes_statements_actually_use(text, expected):
    from openmirror.finance.store import _iso

    assert _iso(text) == expected


def test_a_date_it_cannot_read_is_refused():
    """Reading `14th` as month 14 raises; reading it as day 14 is a guess.

    A guess here is the worst possible failure, because a transaction dated in
    the wrong month still lands in the ledger and the totals still add up.
    """
    from openmirror.finance.store import _iso

    for bad in ('', '   ', 'sometime last week', '14/13/2026', '32 October 2026'):
        with pytest.raises(StoreError):
            _iso(bad)


# --- importing ---------------------------------------------------------------


def test_a_statement_imports_and_reports_what_it_did(ledger):
    ledger.add_account('Everyday')
    report = ledger.import_csv('Everyday', '''Date,Description,Amount
2026-10-01,Salary Ltd,2500.00
2026-10-02,TESCO STORES,-64.20
2026-10-03,COUNCIL TAX,-120.00
''')
    assert report['added'] == 3
    assert report['skipped'] == 0
    assert len(ledger.txns(account='Everyday')) == 3


def test_the_same_statement_imported_twice_does_not_double_the_money(ledger):
    """A statement re-sends the rows it sent last month.

    A naive import doubles every transaction that was pending at the end of the
    month, and the ledger still balances — to twice the truth. The same account,
    date, amount and payee is the same row, so it is updated in place.
    """
    ledger.add_account('Everyday')
    statement = '''Date,Description,Amount
2026-10-01,Salary Ltd,2500.00
2026-10-02,TESCO STORES,-64.20
'''
    ledger.import_csv('Everyday', statement)
    again = ledger.import_csv('Everyday', statement)

    assert again['added'] == 0
    assert again['updated'] == 2
    assert f'{ledger.balance("Everyday"):.2f}' == '2435.80'
    assert len(ledger.txns(account='Everyday')) == 2


def test_a_file_with_no_recognisable_columns_is_refused_rather_than_imported_as_nothing(ledger):
    """The failure this prevents is worse than an error.

    An import that silently reads nothing leaves the ledger balancing — to
    zero — and the next person to ask gets a confident wrong answer. So a file
    with no date or amount column is refused, and the message names the columns
    it did find.
    """
    ledger.add_account('Everyday')
    with pytest.raises(StoreError) as caught:
        ledger.import_csv('Everyday', 'foo,bar\n1,2\n')
    said = str(caught.value)
    assert 'date' in said and 'amount' in said
    assert 'foo' in said and 'bar' in said, 'the message should say what it did find'


def test_lines_it_cannot_read_are_reported_by_number(ledger):
    ledger.add_account('Everyday')
    report = ledger.import_csv('Everyday', '''Date,Description,Amount
2026-10-01,SALARY,2500.00
,,,
2026-10-03,TESCO,-64.20
not-a-date,TESCO,-10.00
''')
    assert report['added'] == 2
    assert report['skipped'] == 2
    assert any('line 2' in line for line in report['lines'])
    assert any('line 4' in line for line in report['lines'])


def test_a_dry_run_changes_nothing(ledger):
    ledger.add_account('Everyday')
    before = ledger.summarise()
    ledger.import_csv(
        'Everyday',
        'Date,Description,Amount\n2026-10-01,SALARY,2500.00\n',
        dry_run=True,
    )
    assert len(ledger.txns()) == 0
    assert f'{ledger.balance("Everyday"):.2f}' == f'{before.total}'


def test_several_export_shapes_are_read(ledger):
    """Every bank spells its CSV differently, so the columns are matched by
    name rather than by position."""
    ledger.add_account('Everyday')
    report = ledger.import_csv('Everyday', '''Posted Date,Transaction Date,Narrative,Debit,Credit
14/10/2026,14/10/2026,TESCO STORES,64.20,
15/10/2026,15/10/2026,SALARY,,2500.00
''')
    assert report['added'] == 2
    found = ledger.txns(account='Everyday')
    assert {f'{t.amount:.2f}' for t in found} == {'-64.20', '2500.00'}


# --- balances, and their dates ----------------------------------------------


def test_a_credit_account_balance_is_the_other_way_round(ledger):
    """A card owes money rather than holding it, and a balance of -412.00 on a
    card means four hundred and twelve pounds owed — not a negative balance."""
    ledger.add_account('Everyday')
    ledger.add_account('Card', kind='credit')
    ledger.add_txn('Everyday', '2026-10-01', '-100.00')
    ledger.add_txn('Card', '2026-10-01', '412.00')

    assert f'{ledger.balance("Everyday"):.2f}' == '-100.00'
    assert f'{ledger.balance("Card"):.2f}' == '-412.00'


def test_a_figure_comes_with_the_date_it_was_true_for(ledger):
    ledger.add_account('Everyday')
    ledger.add_txn('Everyday', '2026-09-01', '-20.00')

    fresh = ledger.summarise(today='2026-09-20')
    assert fresh.newest == '2026-09-01'
    assert fresh.stale is False

    old = ledger.summarise(today='2026-11-01')
    assert old.stale is True
    assert old.stale_for_days > STALE_AFTER_DAYS


def test_only_one_currency_is_summed(ledger):
    """Adding two currencies needs a rate, and this project will not invent
    one. So an account in another currency is reported on its own and left out
    of the total, with a note saying which."""
    ledger.add_account('Everyday', currency='GBP')
    ledger.add_account('Savings', currency='GBP')
    ledger.add_account('Holiday', currency='EUR')
    ledger.add_txn('Everyday', '2026-09-20', '-100.00')
    ledger.add_txn('Savings', '2026-09-20', '-5.00')
    ledger.add_txn('Holiday', '2026-09-20', '-50.00')

    report = ledger.summarise(today='2026-09-20')
    assert report.currency == 'GBP'
    assert report.total == '-105.00', 'the euro account is left out of the total'
    note = ' '.join(str(a.get('notes', '')) for a in report.accounts)
    assert 'Holiday' in note, 'and it should say which account was left out'


def test_accounts_that_disagree_about_their_currency_get_no_total(ledger):
    """A tie is a disagreement, and summing two currencies because they
    happened to be equally common is exactly the arithmetic this refuses."""
    ledger.add_account('Everyday', currency='GBP')
    ledger.add_account('Holiday', currency='EUR')
    ledger.add_txn('Everyday', '2026-09-20', '-100.00')
    ledger.add_txn('Holiday', '2026-09-20', '-50.00')

    report = ledger.summarise(today='2026-09-20')
    note = ' '.join(str(a.get('notes', '')) for a in report.accounts)
    assert 'no total' in note
    assert 'more than one currency' in note


def test_a_closed_account_keeps_its_history(ledger):
    """An account that stopped existing still explains every row against it."""
    ledger.add_account('Old')
    ledger.add_txn('Old', '2026-01-01', '-5.00')
    ledger.close_account('Old', when='2026-06-01')

    assert ledger.account('Old').open is False
    assert len(ledger.txns(account='Old')) == 1


def test_an_unknown_account_says_which_ones_exist(ledger):
    """A refusal nobody can act on is a refusal that wastes a turn."""
    ledger.add_account('Everyday')
    with pytest.raises(StoreError) as caught:
        ledger.balance('Main')
    said = str(caught.value)
    assert 'Everyday' in said


# --- categories --------------------------------------------------------------


def test_a_rule_sorts_what_it_matches(ledger):
    ledger.add_account('Everyday')
    ledger.add_txn('Everyday', '2026-09-01', '-64.20', payee='TESCO STORES 4471')
    ledger.add_txn('Everyday', '2026-09-02', '-120.00', payee='COUNCIL TAX')

    ledger.add_rule('tesco', 'Groceries')
    ledger.add_rule('council', 'Bills')
    assert ledger.recategorise() == 2

    found = ledger.spent_by('2026-09-01')
    assert {name: f'{amount:.2f}' for name, amount in found} == {
        'Bills': '120.00', 'Groceries': '64.20',
    }


def test_a_rule_added_later_fixes_older_rows(ledger):
    """Which is why `recategorise` exists and is not done on import: a rule
    written halfway through last month's statement only matches the rows after
    it."""
    ledger.add_account('Everyday')
    ledger.add_txn('Everyday', '2026-09-01', '-64.20', payee='TESCO')
    assert ledger.summarise(today='2026-09-20').uncategorised == 1

    ledger.add_rule('tesco', 'Groceries')
    assert ledger.recategorise() == 1
    assert ledger.summarise(today='2026-09-20').uncategorised == 0


def test_a_rule_that_stops_matching_is_reported_and_not_deleted(ledger):
    """A category that quietly rots is how a budget stops meaning anything.

    The failure is invisible: the money still goes somewhere, it just goes to
    uncategorised. So a rule that has never matched is reported rather than
    swept away.
    """
    ledger.add_rule('zzz', 'Nothing')
    assert [r['needle'] for r in ledger.stale_rules()] == ['zzz']
    assert len(ledger.rules()) == 1, 'and it is still there to be seen'


def test_uncategorised_spending_is_counted_rather_than_hidden(ledger):
    ledger.add_account('Everyday')
    ledger.add_txn('Everyday', '2026-09-01', '-64.20', payee='WHO KNOWS')
    assert ledger.summarise(today='2026-09-20').uncategorised == 1


# --- the tool ----------------------------------------------------------------


async def test_summarise_reports_the_date_and_not_just_the_number(ledger, tmp_path):
    ledger.add_account('Everyday', currency='GBP')
    ledger.add_txn('Everyday', '2026-09-01', '-20.00', payee='TESCO')
    ledger.add_rule('tesco', 'Groceries')
    ledger.recategorise()

    out = await FinanceTool(ledger).run({'action': 'summarise'}, ctx(tmp_path))
    assert '-20.00' in out.content
    assert '2026-09-01' in out.content, 'the date travels with the figure'


async def test_a_stale_ledger_says_so_rather_than_presenting_itself_as_current(ledger, tmp_path):
    ledger.add_account('Everyday')
    ledger.add_txn('Everyday', '2026-01-01', '-20.00')

    stale = await FinanceTool(ledger).run(
        {'action': 'summarise'}, ctx(tmp_path)
    )
    # `summarise` uses today, and the row is months old.
    assert stale.display['stale'] is True


async def test_spent_reports_figures_and_says_it_is_not_a_verdict(ledger, tmp_path):
    ledger.add_account('Everyday')
    ledger.add_txn('Everyday', '2026-09-01', '-64.20', payee='TESCO')
    ledger.add_rule('tesco', 'Groceries')
    ledger.recategorise()

    out = await FinanceTool(ledger).run(
        {'action': 'spent', 'since': '2026-09-01'}, ctx(tmp_path)
    )
    assert 'Groceries' in out.content
    assert '64.20' in out.content
    assert 'not a verdict' in out.content


async def test_goals_report_what_is_left_and_not_a_percentage(ledger, tmp_path):
    ledger.add_goal('Deposit', '3000', saved='1200', by='2027-04-01')

    out = await FinanceTool(ledger).run({'action': 'goals'}, ctx(tmp_path))
    assert '1800.00 to go' in out.content
    assert 'a month' in out.content, 'what it would take a month is the useful figure'
    # Only the goal line: the prose underneath explains *why* there is no
    # percentage and quotes one to do it.
    goal_line = out.content.splitlines()[0]
    assert '%' not in goal_line, (
        f'the goal is reported as a percentage: {goal_line}'
    )


async def test_an_unknown_action_is_refused_before_anything_runs(ledger, tmp_path):
    tool = FinanceTool(ledger)
    graded = tool.assess({'action': 'pay_bob'}, None)
    assert graded.invalid and 'action must be one of' in graded.invalid

    # A KeyError rather than a graceful refusal: the action list and the
    # dispatch table are asserted to be the same set two tests above, so this
    # cannot happen unless that pair has been broken.
    with pytest.raises(KeyError):
        await tool.run({'action': 'pay_bob'}, ctx(tmp_path))


@pytest.mark.parametrize(
    'action,missing',
    [
        ('add_account', 'name'),
        ('add', 'account'),
        ('add', 'date'),
        ('add', 'amount'),
        ('remove', 'id'),
        ('add_rule', 'match'),
        ('add_goal', 'label'),
        ('import', 'csv'),
    ],
)
def test_an_action_missing_something_it_needs_is_refused(ledger, action, missing):
    # Everything the action needs *except* the one being tested, because the
    # check reports the first thing missing and a test that omitted three would
    # only ever be testing the first.
    everything = {
        'name': 'Everyday', 'account': 'Everyday', 'date': '2026-01-01', 'amount': '1.00',
        'id': 'x', 'match': 'tesco', 'label': 'Deposit', 'csv': 'Date,Amount\n2026-01-01,1',
    }
    del everything[missing]
    graded = FinanceTool(ledger).assess({'action': action, **everything}, None)
    assert graded.invalid == f'{missing} is required'


def test_reading_is_a_read_and_writing_is_a_write(ledger):
    tool = FinanceTool(ledger)
    for action in ('accounts', 'transactions', 'summarise', 'spent', 'rules', 'goals'):
        assert tool.assess({'action': action}, None).risk is Risk.READ, action
    for action in ('add', 'add_account', 'add_rule', 'recategorise', 'import', 'remove'):
        assert tool.assess({'action': action, 'name': 'x', 'account': 'x', 'date': '2026-01-01',
                            'amount': '1', 'id': 'x', 'match': 'x', 'label': 'x',
                            'csv': 'x', 'category': 'x'}, None).risk is Risk.WRITE, action
