"""Planning a trip, and the four things it must not do.

The gap this fills was never the browser. The browser can already drive a
booking form from the first field to the last — `tests/test_booking_walkthrough.py`
fills in a search, reads three results, opens a room, and stops at the button
that says "pay now". What there was no way to do was *hold the plan*: ask for
the details across several turns, keep them, work out an itinerary, add it up,
and notice it does not fit.

So these are about the four properties that make a held plan worth having, and
the four it must not have:

* **Intake persists.** Six questions across six turns is the normal shape of
  this conversation, and a plan that forgets the answers when the session
  compacts is worse than no plan.
* **A total follows from the legs.** Not restated each time it is discussed.
* **A check worth running.** Dates, budget, calendar — reported, not enforced.
* **Booking is not here.** No action reaches a payment, and the tests below
  exist so that adding one has to be an edit to this file.
"""

from __future__ import annotations

import pytest

from openmirror.agent.tools.base import ToolContext, ToolError
from openmirror.agent.tools.trip import _HANDLERS, ACTIONS, RISK, TripTool
from openmirror.protocol.agent import Risk
from openmirror.trips.plan import Trip, TripError, Trips, amount, conflicts_from_ics, moment


@pytest.fixture
def trips(tmp_path):
    made = Trips(tmp_path / 'trips')
    yield made


def ctx(tmp_path):
    return ToolContext(root=tmp_path, cwd=tmp_path, emit=None, ask=None, session_id='s')


# --- what it must not do -----------------------------------------------------


def test_there_is_no_action_that_books_or_buys():
    """The whole of the safety argument, as one assertion to be edited.

    Booking is a browser task and it stops where it has always stopped — at the
    button that takes money, asked in every mode including the most permissive
    one. That guard is `tests/test_money_and_secrets.py` and
    `tests/test_booking_walkthrough.py`. Somebody adding `book` or `pay` here
    has to come and say why, and "to save a step" is not a reason.
    """
    forbidden = ('book', 'pay', 'purchase', 'buy', 'reserve', 'checkout', 'order',
                 'charge', 'refund', 'cancel_booking', 'confirm_payment')
    for action in ACTIONS:
        assert not any(word in action for word in forbidden), (
            f'{action!r} sounds like booking. Booking is a browser task with a purchase approval; '
            f'if this is not that, say so here.'
        )


def test_the_tool_never_reaches_a_browser_or_a_bank():
    """Same argument one layer down.

    Planning is arithmetic on a file. Anything that could turn a plan into a
    charge — an HTTP client, a socket, a subprocess — would let a bug here
    become a purchase, so none of them is imported.
    """
    from pathlib import Path

    for name in ('openmirror/agent/tools/trip.py', 'openmirror/trips/plan.py'):
        source = (Path(__file__).resolve().parents[1] / name).read_text()
        # Imports and calls, not prose: both modules *say* the word "browser" a
        # dozen times, in the docstrings explaining why booking is not here.
        for reacher in ('import aiohttp', 'import httpx', 'import requests', 'import urllib',
                        'import socket', 'import subprocess', 'browser_click', 'browser_navigate'):
            assert reacher not in source, f'{reacher} appeared in {name}'


def test_reading_is_a_read_and_writing_is_a_write(trips):
    tool = TripTool(trips)
    for action in ('intake', 'budget', 'check', 'summary', 'trips', 'list_legs', 'open'):
        assert tool.assess({'action': action}, None).risk is Risk.READ, action
    for action in ('start', 'set', 'add_leg', 'update_leg', 'remove_leg', 'forget'):
        assert RISK[action] is Risk.WRITE, action


def test_every_action_is_wired_and_every_wiring_is_reachable():
    """Unbound methods in a dict read better and are a bug: they are plain
    functions, so every entry has to pass `self` by hand. Two tools in this
    project had that bug and both failed on the first action a person used.
    """
    assert set(ACTIONS) == set(_HANDLERS), set(ACTIONS) ^ set(_HANDLERS)
    assert set(ACTIONS) == set(RISK)
    tool = TripTool.__new__(TripTool)
    for name in _HANDLERS.values():
        assert callable(getattr(tool, name, None)), f'{name} is named but not defined'


# --- money -------------------------------------------------------------------


@pytest.mark.parametrize(
    'text,minor,currency',
    [
        ('{"amount": 120, "currency": "GEL"}', 12000, 'GEL'),
        ('120 GEL', 12000, 'GEL'),
        ('GEL 120', 12000, 'GEL'),
        ({'amount': 1200.55, 'currency': 'gbp'}, 120055, 'GBP'),
    ],
)
def test_the_four_spellings_a_model_actually_produces(text, minor, currency):
    """The tool's schema says "an object with an amount and a currency" and the
    field is a *string*, so a model hands back a JSON string, or the two words
    the other way round, or one of them on its own."""
    assert amount(text) == (minor, currency)


def test_an_amount_with_no_currency_is_refused():
    """A number with no currency is the one form that cannot be total later."""
    for bad in ('1200', 1200, 12.5):
        with pytest.raises(TripError) as caught:
            amount(bad, what='the budget')
        assert 'currency' in str(caught.value)


def test_money_is_minor_units_and_not_a_float():
    assert amount('0.10 GBP')[0] == 10
    assert amount('0.20 GBP')[0] == 20
    # 0.10 + 0.20 is 0.30000000000000004 in binary floating point, and a budget
    # that is four billionths of a penny out per hundred lines is a budget
    # nobody can reconcile against a statement.
    assert sum(amount(x)[0] for x in ('0.10 GBP', '0.20 GBP')) == 30


def test_a_currency_that_is_not_three_letters_is_refused():
    with pytest.raises(TripError, match='three-letter'):
        amount({'amount': 1, 'currency': 'pounds'})


# --- times -------------------------------------------------------------------


def test_a_time_without_an_offset_is_refused():
    """"Assume local" is how an itinerary ends up a day out.

    The flight that lands at 21:00 and the pick-up booked for 20:00 are in
    different zones, and the one that gets assumed is the one that breaks.
    """
    for bad in ('2026-10-14T09:00', '2026-10-14 09:00'):
        with pytest.raises(TripError) as caught:
            moment(bad)
        assert 'timezone offset' in str(caught.value), bad
    # And something that is not a time at all says so, rather than claiming it
    # had no offset when the real problem is that it is a sentence.
    with pytest.raises(TripError) as caught:
        moment('tomorrow 9')
    assert 'not a time' in str(caught.value)


@pytest.mark.parametrize(
    'text',
    ['2026-10-14T09:00+01:00', '2026-10-14T09:00Z', '2026-10-14 09:00+04:00',
     '2026-10-14T11:00:00+02:00'],
)
def test_the_shapes_of_time_that_are_read(text):
    assert moment(text) is not None


def test_the_trip_window_is_two_dates_and_does_not_need_an_offset():
    """The window is "I am there from the 14th to the 21st", which has no
    timezone in it and must not be made to acquire one."""
    trip = Trip(id='t', where='Tbilisi', arrive='2026-10-14', leave='2026-10-21')
    trip.add_leg('flight', 'Out', start='2026-10-14T09:00+01:00',
                 cost={'amount': 340, 'currency': 'GBP'})
    report = trip.check()
    assert report['ok'] is True, report['problems']


def test_an_early_flight_is_not_reported_as_the_day_before(trips):
    """The comparison is against the leg's own local day, because "the flight on
    the 14th" is on the 14th on the ticket.

    Comparing instants instead puts every morning departure in the previous UTC
    day, which is a false alarm on every single early flight and teaches the
    person to ignore this check.
    """
    trip = Trip(id='t', where='Tbilisi', arrive='2026-10-14', leave='2026-10-21')
    trip.add_leg('flight', 'Out', start='2026-10-14T09:00+01:00', cost={'amount': 340, 'currency': 'GBP'})
    assert not [p for p in trip.check()['problems'] if p['kind'] == 'dates']


# --- the checks --------------------------------------------------------------


def _trip(**kw) -> Trip:
    trip = Trip(
        id=kw.pop('id', 't'),
        where=kw.pop('where', 'Tbilisi'), arrive=kw.pop('arrive', '2026-10-14'),
        leave=kw.pop('leave', '2026-10-21'), budget_currency=kw.pop('currency', 'GBP'),
        travellers=kw.pop('travellers', 2), preferences=kw.pop('preferences', 'quiet'),
        origin='London',
    )
    trip.budget = kw.pop('budget', None)
    return trip


def test_a_leg_outside_the_dates_is_reported_with_its_own_name():
    trip = _trip()
    trip.add_leg('activity', 'Day trip to Kazbegi', start='2026-10-30T08:00+04:00',
                 cost={'amount': 90, 'currency': 'GBP'})
    found = [p for p in trip.check()['problems'] if p['kind'] == 'dates']
    assert found and 'Kazbegi' in found[0]['detail']


def test_a_trip_that_leaves_before_it_arrives_is_reported():
    trip = _trip(arrive='2026-10-21', leave='2026-10-14')
    assert any(p['kind'] == 'dates' for p in trip.check()['problems'])


def test_going_over_budget_says_by_how_much_and_which_legs():
    """Not a refusal. A plan that goes over is information the person asked
    for, and "over by 430, mostly the hotel" is actionable where an error is not."""
    trip = _trip(budget=120000)
    trip.add_leg('stay', 'Hotel', cost={'amount': 980, 'currency': 'GBP'})
    trip.add_leg('flight', 'Out', cost={'amount': 340, 'currency': 'GBP'})
    trip.add_leg('flight', 'Home', cost={'amount': 310, 'currency': 'GBP'})

    report = trip.check()
    over = [p for p in report['problems'] if p['kind'] == 'budget']
    assert over, report
    assert '430.00' in over[0]['detail']
    assert 'Hotel' in over[0]['detail']


def test_under_budget_is_not_a_problem():
    trip = _trip(budget=500000)
    trip.add_leg('stay', 'Hotel', cost={'amount': 980, 'currency': 'GBP'})
    assert trip.check()['ok'] is True


def test_a_leg_in_another_currency_is_refused_rather_than_summed():
    """Converting needs a rate and this project will not invent one."""
    trip = _trip(budget=120000)
    with pytest.raises(TripError) as caught:
        trip.add_leg('meal', 'Dinner', cost={'amount': 40, 'currency': 'GEL'})
    assert 'GEL' in str(caught.value) and 'GBP' in str(caught.value)


def test_a_leg_with_no_cost_is_not_counted_but_is_still_a_leg():
    trip = _trip(budget=500000)
    trip.add_leg('transfer', 'Walk to the station')
    trip.add_leg('stay', 'Hotel', cost={'amount': 100, 'currency': 'GBP'})
    assert trip.check()['legs'] == 2


def test_two_things_overlapping_the_same_hour_are_a_clash():
    trip = _trip()
    trip.add_leg('activity', 'Museum', start='2026-10-16T10:00+04:00',
                 end='2026-10-16T13:00+04:00', cost={'amount': 20, 'currency': 'GBP'})
    busy = [(moment('2026-10-16T11:00+04:00'), moment('2026-10-16T12:00+04:00'), 'a work call')]
    found = [p for p in trip.check(busy)['problems'] if p['kind'] == 'clash']
    assert found and 'work call' in found[0]['detail']


def test_things_that_touch_but_do_not_overlap_are_not_a_clash():
    """End at ten, start at ten. Overlap is not adjacency, and an itinerary
    where everything is "clashing" is one nobody reads."""
    trip = _trip()
    trip.add_leg('activity', 'Museum', start='2026-10-16T10:00+04:00',
                 end='2026-10-16T12:00+04:00', cost={'amount': 20, 'currency': 'GBP'})
    busy = [(moment('2026-10-16T12:00+04:00'), moment('2026-10-16T13:00+04:00'), 'a call')]
    assert not [p for p in trip.check(busy)['problems'] if p['kind'] == 'clash']


def test_a_clash_across_timezones_is_found():
    """Two things that look two hours apart on their own clocks and overlap
    perfectly once both are in UTC."""
    trip = _trip()
    trip.add_leg('activity', 'Dinner', start='2026-10-16T20:00+04:00',
                 end='2026-10-16T22:00+04:00', cost={'amount': 60, 'currency': 'GBP'})
    busy = [(moment('2026-10-16T17:00+01:00'), moment('2026-10-16T18:00+01:00'), 'London call')]
    found = [p for p in trip.check(busy)['problems'] if p['kind'] == 'clash']
    assert found, '16:00Z to 18:00Z overlaps 19:00+04:00 to 21:00+04:00'


def test_a_calendar_is_read_for_the_clash_check():
    """A real `.ics`, because the clash check is only worth anything if the
    thing feeding it is the calendar rather than the model."""
    ics = '\n'.join([
        'BEGIN:VCALENDAR', 'BEGIN:VEVENT', 'DTSTART:20261016T160000Z',
        'DTEND:20261016T180000Z', 'SUMMARY:Standup', 'END:VEVENT', 'END:VCALENDAR',
    ])
    busy = conflicts_from_ics(ics)
    assert len(busy) == 1
    assert busy[0][2] == 'Standup'
    assert busy[0][0].utcoffset().total_seconds() == 0


def test_an_ics_it_cannot_read_is_skipped_rather_than_refused():
    """An alarm twenty events away is not worth losing the trip over."""
    ics = '\n'.join([
        'BEGIN:VEVENT', 'DTSTART:not-a-date', 'SUMMARY:Broken', 'END:VEVENT',
        'BEGIN:VEVENT', 'DTSTART:20261016T160000Z', 'SUMMARY:Standup', 'END:VEVENT',
    ])
    busy = conflicts_from_ics(ics)
    assert [one[2] for one in busy] == ['Standup']


# --- intake ------------------------------------------------------------------


def test_intake_asks_for_what_is_missing_and_not_what_is_already_known(trips, tmp_path):
    tool = TripTool(trips)
    _run(tool, tmp_path, action='start')
    asked = _run(tool, tmp_path, action='intake')
    for field in ('where', 'arrive', 'leave'):
        assert field in asked.display['missing']

    _run(tool, tmp_path, action='set', field='where', value='Tbilisi')
    _run(tool, tmp_path, action='set', field='arrive', value='2026-10-14')
    _run(tool, tmp_path, action='set', field='leave', value='2026-10-21')

    again = _run(tool, tmp_path, action='intake')
    assert 'where' not in again.display['missing']
    assert 'arrive' not in again.display['missing']
    assert 'leave' not in again.display['missing']


def test_intake_asks_for_several_things_at_once(trips, tmp_path):
    """One coherent set rather than one question at a time.

    Six turns of "where are you going" / "when" / "who with" is six round trips
    spent on bookkeeping, and it is the shape of the conversation this tool
    exists to fix.
    """
    tool = TripTool(trips)
    _run(tool, tmp_path, action='start')
    asked = _run(tool, tmp_path, action='intake')
    assert len(asked.display['missing']) > 1
    assert 'as one set rather than one at a time' in asked.content
    # One block per question, and every one of them carries the question itself
    # rather than a name — a list of field names makes the model re-read the
    # schema to find out what to ask.
    for name in asked.display['missing']:
        assert f'  {name}:' in asked.content
        assert f'  {name}: {name}' not in asked.content, f'{name} is named rather than asked'


def test_answers_survive_a_new_session(trips, tmp_path):
    """The point of putting them on disk.

    A turn that compacts between question three and question four is the normal
    shape of this conversation, and losing the answers there is worse than
    having no plan at all — the person would be asked them again.
    """
    tool = TripTool(trips)
    _run(tool, tmp_path, action='start')
    _run(tool, tmp_path, action='set', field='where', value='Tbilisi')
    _run(tool, tmp_path, action='set', field='arrive', value='2026-10-14')
    _run(tool, tmp_path, action='set', field='leave', value='2026-10-21')
    _run(tool, tmp_path, action='set', field='travellers', value='2')

    # A second tool object — a new turn, or a new session entirely.
    again = TripTool(trips)
    assert again.trips.list()[0].where == 'Tbilisi'
    assert again.trips.list()[0].travellers == 2


def test_a_trip_is_not_guessed_between(trips, tmp_path):
    """Guessing between two holidays is how you plan the wrong one."""
    tool = TripTool(trips)
    _run(tool, tmp_path, action='start')
    _run(tool, tmp_path, action='set', field='where', value='Tbilisi', trip_id=trips.list()[0].id)
    _run(tool, tmp_path, action='start')
    _run(tool, tmp_path, action='set', field='where', value='Lisbon', trip_id=trips.list()[1].id)
    assert len(trips.list()) == 2
    with pytest.raises(ToolError) as caught:
        _run(tool, tmp_path, action='summary')
    assert 'trip_id' in str(caught.value)


def test_legs_cannot_be_added_before_the_dates_are_known(trips, tmp_path):
    """A leg outside the window is the most common way a plan goes wrong, and
    there is nothing to check against until the window exists."""
    tool = TripTool(trips)
    started = _run(tool, tmp_path, action='start')
    with pytest.raises(ToolError) as caught:
        _run(tool, tmp_path, action='add_leg', kind='flight', what='Out',
             trip_id=started.display['id'], start='2026-10-14T09:00+01:00')
    assert 'dates' in str(caught.value)


def test_the_total_follows_from_the_legs(trips, tmp_path):
    """Not restated each time the plan is discussed.

    A trip whose cost is a sentence somebody typed drifts, and the drift is
    invisible until somebody compares it with a statement.
    """
    tool = TripTool(trips)
    started = _run(tool, tmp_path, action='start')
    for field, value in (('where', 'Tbilisi'), ('arrive', '2026-10-14'),
                         ('leave', '2026-10-21'), ('budget', '1200 GBP')):
        _run(tool, tmp_path, action='set', field=field, value=value, trip_id=started.display['id'])

    _run(tool, tmp_path, action='add_leg', kind='stay', what='Hotel',
         trip_id=started.display['id'], cost='{"amount": 980, "currency": "GBP"}')
    first = _run(tool, tmp_path, action='budget', trip_id=started.display['id'])
    assert '980.00' in first.content

    _run(tool, tmp_path, action='add_leg', kind='flight', what='Out',
         trip_id=started.display['id'], cost='{"amount": 340, "currency": "GBP"}')
    second = _run(tool, tmp_path, action='budget', trip_id=started.display['id'])
    assert '1320.00' in second.content and 'OVER BY 120.00' in second.content


def test_a_leg_can_be_changed_and_the_total_moves(trips, tmp_path):
    tool = TripTool(trips)
    started = _run(tool, tmp_path, action='start')
    for field, value in (('where', 'Tbilisi'), ('arrive', '2026-10-14'), ('leave', '2026-10-21')):
        _run(tool, tmp_path, action='set', field=field, value=value, trip_id=started.display['id'])
    _run(tool, tmp_path, action='add_leg', kind='stay', what='Hotel',
         trip_id=started.display['id'], cost='{"amount": 980, "currency": "GBP"}')

    _run(tool, tmp_path, action='update_leg', what='Hotel', field='cost',
         value='{"amount": 600, "currency": "GBP"}', trip_id=started.display['id'])
    after = _run(tool, tmp_path, action='budget', trip_id=started.display['id'])
    assert '600.00' in after.content and '980' not in after.content

    _run(tool, tmp_path, action='remove_leg', what='Hotel', trip_id=started.display['id'])
    empty = _run(tool, tmp_path, action='budget', trip_id=started.display['id'])
    assert '0.00' in empty.content


def test_forget_needs_the_id_it_is_deleting(trips, tmp_path):
    """Deleting the wrong holiday is worse than a refusal."""
    tool = TripTool(trips)
    _run(tool, tmp_path, action='start')
    with pytest.raises(ToolError) as caught:
        _run(tool, tmp_path, action='forget')
    assert 'trip_id' in str(caught.value)


def test_check_says_it_is_reporting_and_not_refusing(trips, tmp_path):
    """The wording is the argument. A refusal here would make a person think
    the tool disapproves of their budget."""
    tool = TripTool(trips)
    started = _run(tool, tmp_path, action='start')
    for field, value in (('where', 'Tbilisi'), ('arrive', '2026-10-14'),
                         ('leave', '2026-10-21'), ('budget', '100 GBP')):
        _run(tool, tmp_path, action='set', field=field, value=value, trip_id=started.display['id'])
    _run(tool, tmp_path, action='add_leg', kind='stay', what='Hotel',
         trip_id=started.display['id'], cost='{"amount": 980, "currency": "GBP"}')

    said = _run(tool, tmp_path, action='check', trip_id=started.display['id'])
    assert 'not a refusal' in said.content
    assert 'browser task' in said.content, 'and it should say where booking happens'


def _run(tool, tmp_path, **args):
    import asyncio

    return asyncio.run(tool.run({'action': args.pop('action'), **args}, ctx(tmp_path)))
