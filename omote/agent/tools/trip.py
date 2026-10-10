"""Planning a trip: ask for the details, hold them, and check the arithmetic.

**The division of labour is the same one the `finance` tool uses.** This tool
holds a plan and reports on it. It does not book anything and it does not
decide anything — "is this a good idea" is not a question a tool should answer
about somebody's holiday, and "shall I buy this flight" is not a question
anything in this repository is allowed to answer.

**Booking stays in the browser, and stops where it has always stopped.** The
last step of a trip is a browser task, and the button that takes money grades
`PURCHASE` — which is asked in *every* approval mode including the most
permissive one, and refused outright in the two that promise nothing changes.
That is `tests/test_booking_walkthrough.py` and `test_money_and_secrets.py`, and
this tool does not add a way around either.

**What it does that a conversation cannot.** Three things, and all three are
arithmetic rather than judgement:

* **Intake that survives.** Asking for six things across six turns is the normal
  shape of this conversation. The answers are on disk, so a turn that compacts
  between question three and question four does not lose them, and `intake`
  reports what is still missing rather than starting again — which is what
  makes the second call to it cheaper than the first.
* **A total that follows.** Change one leg and the total is the new one,
  because the total is a property of the legs rather than a sentence somebody
  typed. A trip whose cost is restated each time it is discussed drifts.
* **A check worth running.** Does it fit the dates, does it fit the budget, does
  it clash with something already in the calendar. Reported, never enforced —
  a plan that goes over is not an error, because the person may have decided
  that, and the useful output is *how far* over and *which legs*.
"""

from __future__ import annotations

from typing import Any

from omote.agent.tools.base import Assessment, Output, Tool, ToolContext, ToolError
from omote.protocol.agent import Risk
from omote.trips.plan import KINDS, Trip, TripError, Trips, amount, conflicts_from_ics, moment

ACTIONS = (
    'start', 'intake', 'set', 'add_leg', 'update_leg', 'remove_leg', 'list_legs',
    'budget', 'check', 'summary', 'trips', 'open', 'forget',
)

RISK: dict[str, Risk] = {
    'start': Risk.WRITE,       # making one is a change to a file on disk
    'intake': Risk.READ,
    'set': Risk.WRITE,
    'add_leg': Risk.WRITE,
    'update_leg': Risk.WRITE,
    'remove_leg': Risk.WRITE,
    'list_legs': Risk.READ,
    'budget': Risk.READ,
    'check': Risk.READ,
    'summary': Risk.READ,
    'trips': Risk.READ,
    'open': Risk.READ,
    'forget': Risk.WRITE,
}

#: What each intake field is, so a coherent set of questions can be asked in one
#: go rather than one at a time. The *shape* of the answer is the thing worth
#: recording: a trip needs a place and two dates, and everything else is a
#: preference except the budget, which is the one number that changes what you
#: can recommend.
QUESTIONS: dict[str, str] = {
    'where': 'Where to? A city is enough.',
    'from': 'Travelling from where?',
    'arrive': 'What date do you arrive? (YYYY-MM-DD is fine — a date without a time is a whole day.)',
    'leave': 'And what date do you leave?',
    'travellers': 'How many of you? One number.',
    'budget': 'What is the budget, and in what currency? e.g. {"amount": 1200, "currency": "GBP"}. '
              'A rough figure is fine — it is there so the arithmetic can tell you when you are over.',
    'preferences': 'Anything that matters: quiet, walkable, near a station, no early starts, '
                   'vegetarian, a day trip rather than a week. If nothing does, say so.',
}


class TripTool(Tool):
    name = 'trip'
    description = (
        'Plan a trip: hold the details, work out an itinerary, and check it against the dates, '
        'the budget and the calendar.\n\n'
        'Start with `intake`. It tells you exactly what is still missing, in one coherent set '
        'rather than one question at a time, and it persists — so if the conversation is '
        'interrupted or compacted, the answers are still here and you ask only what is left.\n\n'
        'Then `add_leg` for each thing. Every leg needs a kind, a name, and — for anything you '
        'want checked against anything else — times **with a timezone offset**. A time without '
        'one is refused rather than assumed to be local, because "assume local" is wrong for '
        'exactly the legs that matter: the flight that lands at 21:00 and the pick-up booked for '
        '20:00 are in different zones.\n\n'
        '`check` is worth running before anybody books anything. It says whether it fits the '
        'dates, whether it fits the budget and by how much, and whether it clashes with '
        'something already booked. It reports; it never refuses, because a plan that goes over is '
        'not an error — it is information the person asked for.\n\n'
        '**Booking is a browser task and not this tool.** When the plan is right, search for it '
        'with the browser or web tools, and book with them. The button that takes money grades as '
        'a purchase, which means the person is asked before it is pressed, in every mode. Do not '
        'look for a shortcut here — there is not one, and that is deliberate.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': list(ACTIONS)},
            'trip_id': {'type': 'string', 'description': 'Which trip. Defaults to the only one.'},
            'field': {
                'type': 'string',
                'description': f"For set: one of {', '.join(QUESTIONS)}. For update_leg: any field on the leg.",
            },
            'value': {'type': 'string', 'description': 'The value, as a string.'},
            'kind': {'type': 'string', 'enum': list(KINDS), 'description': 'For add_leg.'},
            'what': {'type': 'string', 'description': 'For add_leg: the name of it. "Flight to Tbilisi".'},
            'where': {'type': 'string', 'description': 'For add_leg: the place it happens.'},
            'start': {'type': 'string', 'description': 'ISO with an offset: 2026-10-14T09:30+04:00'},
            'end': {'type': 'string', 'description': 'ISO with an offset.'},
            'cost': {
                'type': 'string',
                'description': 'For add_leg: {"amount": 120, "currency": "GBP"}. A currency is '
                               'required, because adding two currencies needs a rate and this '
                               'will not invent one.',
            },
            'booked': {'type': 'boolean', 'description': 'For update_leg: whether this is done.'},
            'calendar': {
                'type': 'string',
                'description': 'An .ics file to check against. Get it from the calendar tool; if '
                               'there is no calendar, leave it out rather than guessing.',
            },
        },
        'required': ['action'],
    }

    def __init__(self, trips: Trips) -> None:
        self.trips = trips

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        action = str(args.get('action') or '').strip()
        if action not in ACTIONS:
            return Assessment(
                risk=Risk.READ, summary='', invalid=f'action must be one of: {", ".join(ACTIONS)}'
            )
        said = ' '.join(
            str(args.get(key) or '') for key in ('what', 'field', 'trip_id') if args.get(key)
        )
        return Assessment(risk=RISK[action], summary=f'{action} {said}'.strip()[:100])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        action = str(args['action'])
        handler = getattr(self, _HANDLERS.get(action, ''), None)
        if handler is None:  # pragma: no cover - ACTIONS and _HANDLERS are asserted equal
            raise ToolError(f'{action} is not something this tool knows how to do')
        try:
            return await handler(args, ctx)
        except TripError as exc:
            raise ToolError(str(exc)) from exc

    # -- finding the trip ---------------------------------------------------

    def _one(self, args: dict[str, Any]) -> Trip:
        wanted = str(args.get('trip_id') or '').strip()
        found = self.trips.list()
        if wanted:
            return self.trips.get(wanted)
        if not found:
            raise TripError(
                'there is no trip yet. Call `trip` with action "start" to begin one, then "intake".'
            )
        if len(found) > 1:
            # Guessing between two holidays is how you plan the wrong one.
            raise TripError(
                f'there are {len(found)} trips: {", ".join(t.id for t in found)}. '
                'Pass trip_id — it does not guess between them.'
            )
        return found[0]

    # -- the actions --------------------------------------------------------

    async def _start(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        made = self.trips.new()
        self.trips.save(made)
        # The intake questions *and* the trip, because the caller needs the id to
        # keep talking about it and the questions are the first thing worth
        # saying. Returning only the questions meant the id had to be looked up
        # again with a second call.
        asked = await self._intake({'trip_id': made.id}, ctx)
        return Output(content=asked.content, display={**made.to_json(), 'missing': asked.display['missing']})

    async def _intake(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        trip = self._one(args)
        missing = trip.missing()
        if not missing:
            return Output(
                content=(
                    f'Everything is answered for {trip.where or "this trip"}. '
                    f'{len(trip.legs)} legs so far. `check` before anybody books anything.'
                ),
                display=trip.to_json(),
            )
        lines = ['Still needed, as one set rather than one at a time:', '']
        for name in missing:
            lines.append(f'  {name}: {QUESTIONS.get(name, "What should it be?")}')
        lines += [
            '',
            'Answer them with action "set" — several at once is fine and is the point of this. '
            'They are saved as you go, so being interrupted loses nothing.',
        ]
        if len(missing) > 1:
            lines.append('')
            lines.append(
                f'{len(missing)} of these are optional in the sense that a trip can be planned '
                'without them. The three that are not are where, and the two dates.'
            )
        return Output(content='\n'.join(lines), display={'trip_id': trip.id, 'missing': missing})

    async def _set(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        trip = self._one(args)
        name = str(args.get('field') or '').strip()
        raw = args.get('value')
        if raw in (None, ''):
            raise TripError(f'set needs a value. Fields: {", ".join(QUESTIONS)}')
        text = str(raw).strip()

        # `from` is the word people use and it is a Python keyword, so the
        # attribute it sets is `origin`. The mapping is written out rather than
        # derived, because a `setattr` with a name from a tool argument is
        # exactly the kind of thing that should be a lookup and not a guess.
        field = {'from': 'origin'}.get(name, name)
        if field in ('where', 'origin', 'preferences', 'notes'):
            setattr(trip, field, text)
        elif name in ('arrive', 'leave'):
            # A bare date is a whole day, which is the common case and is
            # unambiguous. Anything with a time needs its offset.
            when = moment(text if 'T' in text or ' ' in text else f'{text}T00:00:00+00:00',
                          what=name)
            setattr(trip, name, when.date().isoformat())
        elif name == 'travellers':
            try:
                trip.travellers = int(text)
            except ValueError:
                raise TripError(f'travellers must be a number, not {text!r}') from None
            if trip.travellers < 1:
                raise TripError('there is at least one of you')
        elif name == 'budget':
            trip.budget, trip.budget_currency = amount(text, what='the budget')
        else:
            raise TripError(f'{name!r} is not a field of a trip. Fields: {", ".join(QUESTIONS)}')
        self.trips.save(trip)
        still = trip.missing()
        said = f'{name} = {text}'
        tail = f' Still needed: {", ".join(still)}.' if still else ' That is everything.'
        return Output(content=f'Noted: {said}.{tail}', display=trip.to_json())

    async def _add_leg(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        trip = self._one(args)
        if not trip.arrive or not trip.leave:
            raise TripError('set the dates before adding legs — a leg outside the window is the '
                            'most common way a plan goes wrong, and there is nothing to check against yet')
        leg = trip.add_leg(
            str(args.get('kind') or 'other'),
            str(args.get('what') or ''),
            where=str(args.get('where') or ''),
            start=str(args.get('start') or ''),
            end=str(args.get('end') or ''),
            cost=args.get('cost'),
            booked=bool(args.get('booked')),
        )
        self.trips.save(trip)
        _, _, _ = trip.total()
        return Output(
            content=(
                f'Added "{leg.what}". {len(trip.legs)} legs so far.'
                + (' Run `check` to see whether it still fits.' if len(trip.legs) > 1 else '')
            ),
            display=leg.to_json(),
        )

    async def _update_leg(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        trip = self._one(args)
        leg = trip.leg(str(args.get('leg') or args.get('what') or ''))
        field = str(args.get('field') or '').strip()
        if field == 'booked':
            leg.booked = bool(args.get('value') or args.get('booked'))
        elif field in ('start', 'end'):
            setattr(leg, field, str(args.get('value') or ''))
            moment(getattr(leg, field), what=f'{field} of "{leg.what}"')
        elif field in ('what', 'where', 'note', 'ref'):
            setattr(leg, field, str(args.get('value') or ''))
        elif field == 'cost':
            leg.cost, leg.currency = amount(args.get('value'), what='the cost')
        else:
            raise TripError(f'{field!r} is not a field of a leg: what, where, start, end, cost, '
                            'booked, ref, note')
        self.trips.save(trip)
        return Output(content=f'Updated {field} on "{leg.what}".', display=leg.to_json())

    async def _remove_leg(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        trip = self._one(args)
        leg = trip.leg(str(args.get('leg') or args.get('what') or ''))
        trip.legs.remove(leg)
        self.trips.save(trip)
        total, currency, _ = trip.total()
        return Output(
            content=f'Removed "{leg.what}". The total is now {total / 100:.2f} {currency}.',
            display={'total': f'{total / 100:.2f}', 'currency': currency},
        )

    async def _list_legs(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        trip = self._one(args)
        if not trip.legs:
            return Output(content='No legs yet.', display={'legs': []})
        lines = []
        for leg in sorted(trip.legs, key=lambda one: one.start or '9999'):
            cost = f'{leg.cost / 100:.2f} {leg.currency}' if leg.cost is not None else '—'
            mark = 'booked' if leg.booked else 'not booked'
            when = leg.start or 'no time'
            lines.append(f'  {leg.kind:9} {leg.what:32} {when:26} {cost:>16}  {mark}')
        return Output(content='\n'.join(lines), display={'legs': [one.to_json() for one in trip.legs]})

    async def _budget(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        trip = self._one(args)
        total, currency, counted = trip.total()
        lines = [f'  {one.what}: {one.cost / 100:.2f}' for one in sorted(
            counted, key=lambda one: -(one.cost or 0)
        )]
        lines.append(f'  total: {total / 100:.2f} {currency}')
        if trip.budget is not None:
            left = trip.budget - total
            lines.append(f'  budget: {trip.budget / 100:.2f} {trip.budget_currency}')
            if left < 0:
                lines.append(f'  OVER BY {-left / 100:.2f}')
            else:
                lines.append(f'  left: {left / 100:.2f}')
        missing_cost = [one.what for one in trip.legs if one.cost is None]
        if missing_cost:
            lines.append('')
            lines.append('No cost recorded for: ' + ', '.join(missing_cost)
                         + '. The total is therefore a lower bound.')
        return Output(
            content='\n'.join(lines)
            + '\n\nA figure, not a verdict. Whether a total is too much is not a question this tool '
              'answers.',
            display=trip.to_json(),
        )

    async def _check(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        trip = self._one(args)
        busy = []
        ics = str(args.get('calendar') or '').strip()
        if ics:
            busy = conflicts_from_ics(ics)
        report = trip.check(busy)

        if report['ok']:
            return Output(
                content=(
                    f'{trip.where or "The trip"} fits: {report["legs"]} legs, '
                    f'{report["total"]} {report["currency"]}'
                    + (f' against a budget of {report["budget"]}' if report['budget'] else '')
                    + '. Nothing clashes'
                    + (f' with {len(busy)} calendar entries' if busy else '')
                    + '.'
                ),
                display=report,
            )
        lines = ['Not quite:', '']
        for one in report['problems']:
            lines.append(f'  [{one["kind"]}] {one["detail"]}')
        lines += [
            '',
            f'Total {report["total"]} {report["currency"]}'
            + (f' against a budget of {report["budget"]}' if report['budget'] else '')
            + '.',
            '',
            'This is what it says, not a refusal — a plan that goes over is not a mistake, it is '
            'information you asked for. Change a leg, change the budget, or say it is fine.',
        ]
        if report['unbooked']:
            lines += [
                '',
                'Not booked yet: ' + ', '.join(report['unbooked'])
                + '. Booking is a browser task; the button that takes money still asks first.',
            ]
        return Output(content='\n'.join(lines), display=report)

    async def _summary(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        trip = self._one(args)
        report = trip.check()
        lines = [
            f'{trip.where or "A trip"}'
            + (f' from {trip.origin}' if trip.origin else '')
            + (f', {trip.travellers} travelling' if trip.travellers > 1 else ''),
            f'  {trip.arrive} to {trip.leave}',
        ]
        if trip.preferences:
            lines.append(f'  {trip.preferences}')
        lines.append(f'  {len(trip.legs)} legs, {report["total"]} {report["currency"]}'
                     + (f' of {report["budget"]}' if report['budget'] else ''))
        if trip.notes:
            lines.append(f'  note: {trip.notes}')
        return Output(content='\n'.join(lines), display=trip.to_json())

    async def _trips(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        found = self.trips.list()
        if not found:
            return Output(content='No trips.', display={'trips': []})
        lines = [
            f'  {one.id}  {one.where or "(nowhere yet)":20} {one.arrive or "—"} to {one.leave or "—":10}'
            f'  {len(one.legs)} legs'
            for one in found
        ]
        return Output(content='\n'.join(lines), display={'trips': [one.id for one in found]})

    async def _open(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        return await self._summary({'trip_id': str(args['trip_id'])}, ctx)

    async def _forget(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        trip_id = str(args.get('trip_id') or '').strip()
        if not trip_id:
            raise TripError('forget needs trip_id — deleting the wrong holiday is worse than a refusal')
        gone = self.trips.delete(trip_id)
        return Output(
            content='Forgot that trip.' if gone else f'There was no trip called {trip_id}.',
            display={'deleted': gone, 'trip_id': trip_id},
        )


#: Action to method name. Names, not functions — a dict of unbound methods is a plain
#: function each, so every entry has to remember to pass `self`, and this one did not.
#: Looked up on `self`, a wrong name raises when it is called rather than at import.
_HANDLERS: dict[str, str] = {
    'start': '_start',
    'intake': '_intake',
    'set': '_set',
    'add_leg': '_add_leg',
    'update_leg': '_update_leg',
    'remove_leg': '_remove_leg',
    'list_legs': '_list_legs',
    'budget': '_budget',
    'check': '_check',
    'summary': '_summary',
    'trips': '_trips',
    'open': '_open',
    'forget': '_forget',
}


def trip_tools(trips: Trips) -> list[Tool]:
    return [TripTool(trips)]


__all__ = ['ACTIONS', 'QUESTIONS', 'TripTool', 'trip_tools']
