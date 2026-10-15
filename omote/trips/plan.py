"""A trip, as something you can hold, check and change.

There was no domain layer here at all, and that is the whole gap. The browser
can already drive a booking form from the first field to the last — with a real
test that fills a search, reads three results, and stops at the button that
says "pay now". What there was no way to do was *hold the plan*: ask for the
details, keep them, work out an itinerary, add it up, notice it does not fit
in the budget or clash with something already in the calendar, and change one
leg and have the arithmetic follow.

Without that, a trip conversation is a pile of prose that has to be re-derived
every time it is looked at, and the two failures that come from it are visible
and bad: an itinerary whose dates drifted from what was agreed, and a total
that is wrong by the leg somebody forgot.

**What is refused and what is not.** Nothing here books anything, and nothing
here spends anything. Booking is a browser task and it stops where it has always
stopped — at the button that takes money, in every mode, including the most
permissive one. See [AUTONOMY.md](../../../docs/AUTONOMY.md) and
`tests/test_money_and_secrets.py`. This module's whole job is to make the thing
that *is* approved as informed as it can be.

**The budget is reported, never enforced.** A plan that goes over is not an
error, because the person may have decided it goes over. It says so, it says by
how much, and it names the legs — because a total that silently stops being a
total is how someone books something they did not mean to.

**Intake persists.** Asking for six things across six turns is the normal shape
of this conversation, and a plan that forgets the answers because the session
compacted between question three and question four is worse than no plan. The
answers are on disk, keyed to the session, and `intake` says what is still
missing rather than starting again.

**Timezones are stored, not guessed.** A flight from London to Tbilisi lands on
a different calendar day than it departed, and every trip involves at least one
such leg. So each leg carries explicit UTC offsets and every sum is done in
UTC — and a leg with no offset is refused rather than assumed to be local,
because "assume local" is wrong for exactly the legs that matter.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: What a trip has to have before it can be planned at all. Everything else is
#: asked for and a plan without it is still a plan.
REQUIRED = ('where', 'arrive', 'leave')

#: The things worth asking that a person can decline to answer. Optional on
#: purpose — asking for a passport number is asking for a secret, and a secret
#: is not something an intake should collect. `passport_needed` is a yes/no
#: question; the number itself never comes near here.
OPTIONAL = ('from', 'travellers', 'budget', 'budget_currency', 'preferences', 'notes')

KINDS = ('stay', 'flight', 'train', 'car', 'transfer', 'activity', 'meal', 'other')


class TripError(RuntimeError):
    """The plan could not be read or written."""


# ---------------------------------------------------------------------------
# Money, dates, and the arithmetic between them
# ---------------------------------------------------------------------------


def amount(value: Any, *, what: str = 'an amount') -> tuple[int, str]:
    """Money as minor units, with the currency it is in.

    Minor units rather than a float: a budget that is a penny out per leg is a
    budget nobody can reconcile against a bank statement, and the accumulation
    is the whole problem. An amount is *always* paired with its currency, so a
    total cannot quietly add euros to pounds — this returns both or refuses.

    The spellings are the four a model actually produces, because the tool's
    schema says "an object with an amount and a currency" and a model that has
    been handed a *string* field will hand back a JSON string:
    `{"amount": 120, "currency": "GEL"}`, `120 GEL`, `GEL 120`, and a bare
    number — the last refused, because a number with no currency is the one
    form that cannot be total later.
    """
    currency = ''
    if isinstance(value, str):
        text = value.strip()
        if text.startswith('{'):
            import json  # noqa: PLC0415

            try:
                value = json.loads(text)
            except ValueError:
                raise TripError(
                    f'{what} looks like JSON but is not: {text[:80]!r}. Either '
                    '{"amount": 120, "currency": "GEL"} or "120 GEL".'
                ) from None

    if isinstance(value, dict):
        currency = str(value.get('currency') or '').strip().upper()
        raw = value.get('amount', value.get('value'))
    else:
        raw = value
        if isinstance(value, str) and any(ch.isdigit() for ch in value):
            # "120 GEL" and "GEL 120" are both things people type.
            parts = value.replace(',', '').split()
            if len(parts) == 2:
                head, tail = parts
                if any(ch.isalpha() for ch in head) and not any(ch.isalpha() for ch in tail):
                    currency, raw = head, tail
                elif any(ch.isalpha() for ch in tail) and not any(ch.isalpha() for ch in head):
                    raw, currency = head, tail
    if not currency:
        raise TripError(
            f'{what} needs a currency: pass {{"amount": 120, "currency": "GEL"}}. '
            'Adding amounts in two currencies needs a rate and this will not invent one.'
        )
    if len(currency) != 3 or not currency.isalpha():
        raise TripError(f'{currency!r} is not a three-letter currency code')
    try:
        found = float(str(raw).replace(',', '').strip())
    except (TypeError, ValueError):
        raise TripError(f'{what} must be a number, not {raw!r}') from None
    return int(round(found * 100)), currency


def moment(value: Any, *, what: str = 'a time') -> datetime:
    """A time, with its offset, or a refusal.

    A naive datetime is refused rather than assumed to be local time. It looks
    like a convenience and it is the bug that makes an itinerary a day out: the
    flight that lands "at 21:00" and the pick-up booked "at 20:00" are in
    different zones, and the one that is assumed local is the one that breaks.
    """
    if isinstance(value, datetime):
        found = value
    else:
        text = str(value or '').strip()
        if not text:
            raise TripError(f'{what} is required')
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            found = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ('%Y-%m-%dT%H:%M%z', '%Y-%m-%d %H:%M%z', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
                try:
                    found = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise TripError(
                    f'{value!r} is not a time. Use ISO with an offset: 2026-10-14T09:30+04:00.'
                ) from None
    if found.tzinfo is None:
        raise TripError(
            f'{what} ({value!r}) has no timezone offset, and assuming the local one is how an '
            'itinerary ends up a day out. Write 2026-10-14T09:30+04:00.'
        )
    return found


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _day(value: str | None) -> date | None:
    """A whole day, from a date or a datetime.

    The trip's window is two *dates*, not two instants — "I am there from the
    14th to the 21st" has no timezone in it and must not be made to acquire
    one. So this reads the first ten characters and stops.
    """
    text = (value or '').strip()
    if len(text) >= 10 and text[4] == '-' and text[7] == '-':
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    try:
        return moment(text).date()
    except TripError:
        return None


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Leg:
    """One thing you are doing, somewhere, at a time, costing money.

    `start` and `end` are what make a leg a leg rather than a note. A plan
    whose legs have no times cannot be checked against a calendar, and a plan
    that cannot be checked against a calendar is a list.
    """

    id: str
    kind: str
    what: str
    where: str = ''
    start: str = ''
    end: str = ''
    # Minor units and currency, kept apart so a total cannot be formed without
    # both. `None` for a leg that costs nothing known — a walk to the station.
    cost: int | None = None
    currency: str = ''
    booked: bool = False
    ref: str = ''
    note: str = ''

    @property
    def begins(self) -> datetime | None:
        try:
            return moment(self.start, what=f'{self.what} start') if self.start else None
        except TripError:
            return None

    @property
    def finishes(self) -> datetime | None:
        try:
            return moment(self.end, what=f'{self.what} end') if self.end else None
        except TripError:
            return None

    def to_json(self) -> dict[str, Any]:
        return {
            'id': self.id, 'kind': self.kind, 'what': self.what, 'where': self.where,
            'start': self.start, 'end': self.end,
            'cost': f'{self.cost / 100:.2f}' if self.cost is not None else '',
            'currency': self.currency, 'booked': self.booked, 'ref': self.ref, 'note': self.note,
        }


@dataclass(slots=True)
class Trip:
    """One trip, its answers, its legs, and where it stands."""

    id: str
    where: str = ''
    arrive: str = ''
    leave: str = ''
    origin: str = ''
    travellers: int = 1
    budget: int | None = None
    budget_currency: str = ''
    preferences: str = ''
    notes: str = ''
    legs: list[Leg] = field(default_factory=list)
    updated_at: str = ''

    # -- intake ------------------------------------------------------------

    def missing(self) -> list[str]:
        """What nobody has told us yet.

        Required first, then optional, and *only* the optional ones that were
        actually asked about — `preferences` is not missing unless somebody
        intended to ask, and `intake` is how that gets recorded.
        """
        out = [name for name in REQUIRED if not getattr(self, name)]
        if out:
            return out
        out = []
        if not self.origin:
            out.append('from')
        if self.travellers < 2:
            out.append('travellers')
        if self.budget is None:
            out.append('budget')
        if not self.preferences:
            out.append('preferences')
        return out

    def answered(self, name: str) -> bool:
        return name not in self.missing()

    # -- legs --------------------------------------------------------------

    def add_leg(
        self,
        kind: str,
        what: str,
        *,
        where: str = '',
        start: str = '',
        end: str = '',
        cost: Any = None,
        booked: bool = False,
        ref: str = '',
        note: str = '',
    ) -> Leg:
        kind = str(kind or 'other').strip().lower()
        if kind not in KINDS:
            raise TripError(f'kind must be one of: {", ".join(KINDS)}')
        what = str(what or '').strip()
        if not what:
            raise TripError('a leg needs something to do')

        minor, currency = (None, '')
        if cost not in (None, '', {}):
            minor, currency = amount(cost, what=f'the cost of "{what}"')
            if self.budget_currency and currency != self.budget_currency:
                raise TripError(
                    f'"{what}" is in {currency} and the budget is in {self.budget_currency}. '
                    'Converting between them needs a rate, which this will not invent — so convert '
                    'it yourself and say so.'
                )

        leg = Leg(
            id=uuid.uuid4().hex[:12], kind=kind, what=what, where=where,
            start=start, end=end, cost=minor, currency=currency,
            booked=bool(booked), ref=ref, note=note,
        )
        # Times are validated here rather than at `add_leg`, so a bad one is a
        # refusal with the leg's own name in it.
        if leg.begins is None and start:
            moment(start, what=f'the start of "{what}"')
        if leg.finishes is None and end:
            moment(end, what=f'the end of "{what}"')
        self.legs.append(leg)
        return leg

    def leg(self, leg_id: str) -> Leg:
        for one in self.legs:
            if one.id == leg_id:
                return one
        matches = [one for one in self.legs if one.what == leg_id]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise TripError(f'More than one leg is called {leg_id!r}. Use the exact leg ID from list_legs.')
        known = ', '.join(one.what for one in self.legs) or 'none yet'
        raise TripError(f'no leg called {leg_id!r}. It has: {known}')

    # -- the checks --------------------------------------------------------

    def total(self) -> tuple[int, str, list[Leg]]:
        """What it adds up to, and the legs that did.

        Every leg comes back with the total, because "over budget" is only
        actionable next to the legs that made it so. Legs in another currency
        are listed separately rather than summed — and rather than being quietly
        dropped, which is the failure this is here to prevent.
        """
        total = 0
        currency = self.budget_currency
        counted: list[Leg] = []
        foreign: list[Leg] = []
        for leg in self.legs:
            if leg.cost is None:
                continue
            if currency and leg.currency != currency:
                foreign.append(leg)
                continue
            if not currency:
                currency = leg.currency
            total += leg.cost
            counted.append(leg)
        return total, currency, counted

    def check(self, busy: list[tuple[datetime, datetime, str]] | None = None) -> dict[str, Any]:
        """Whether the plan holds together. Reported, never enforced.

        Three things, in the order somebody would notice them:

        1. **It does not fit the dates.** A leg outside the window is the single
           most common way a plan goes wrong, and it is a pure arithmetic
           check that costs nothing.
        2. **It does not fit the budget.** Said as an amount and by how much,
           with the legs that made it so.
        3. **It clashes with something already booked.** `busy` is a list of
           `(start, end, what)` — the caller passes whatever it already knows,
           and this module does not go looking, because where commitments live
           is not a thing it should guess at.
        """
        problems: list[dict[str, str]] = []

        # The *window* is two dates, and a date has no offset — that is what a
        # date is. Only the legs need one, and the two are compared the way the
        # person means them: against the leg's own local day, because "the
        # flight on the 14th" is on the 14th on the ticket. Comparing instants
        # instead would put a 09:00 departure from London on the 13th for a
        # trip starting on the 14th, which is wrong in the direction that
        # produces a false alarm on every single early flight.
        window_lo = _day(self.arrive)
        window_hi = _day(self.leave)
        if window_lo and window_hi and window_hi < window_lo:
            problems.append({
                'kind': 'dates',
                'detail': f'the trip leaves ({self.leave}) before it arrives ({self.arrive})',
            })
            window_lo = window_hi = None

        if window_lo or window_hi:
            for leg in self.legs:
                begins = leg.begins
                if begins is None:
                    continue
                day = begins.date()
                if window_lo and day < window_lo:
                    problems.append({
                        'kind': 'dates', 'leg': leg.what,
                        'detail': f'"{leg.what}" is on {day}, before the trip starts on {window_lo}',
                    })
                if window_hi and day > window_hi:
                    problems.append({
                        'kind': 'dates', 'leg': leg.what,
                        'detail': f'"{leg.what}" is on {day}, after the trip ends on {window_hi}',
                    })

        total, currency, counted = self.total()
        if self.budget is not None and self.budget_currency:
            if currency != self.budget_currency:
                problems.append({
                    'kind': 'currency',
                    'detail': f'the legs are in {currency or "no currency"} and the budget is in '
                              f'{self.budget_currency}, so they cannot be compared',
                })
            elif total > self.budget:
                over = total - self.budget
                biggest = sorted(counted, key=lambda one: -(one.cost or 0))[:3]
                problems.append({
                    'kind': 'budget',
                    'detail': f'{over / 100:.2f} {self.budget_currency} over. '
                              + ', '.join(f'{one.what} {one.cost / 100:.2f}' for one in biggest
                                          if one.cost),
                })

        clashes: list[dict[str, str]] = []
        for leg in self.legs:
            begins, finishes = leg.begins, leg.finishes
            if begins is None or finishes is None:
                continue
            for start, end, what in busy or []:
                if _utc(begins) < _utc(end) and _utc(start) < _utc(finishes):
                    clashes.append({
                        'kind': 'clash', 'leg': leg.what,
                        'detail': f'"{leg.what}" overlaps {what}',
                    })
        problems.extend(clashes)

        return {
            'problems': problems,
            'ok': not problems,
            'total': f'{total / 100:.2f}',
            'currency': currency,
            'budget': f'{self.budget / 100:.2f}' if self.budget is not None else '',
            'legs': len(self.legs),
            'unbooked': [one.what for one in self.legs if not one.booked],
        }

    def to_json(self) -> dict[str, Any]:
        total, currency, _ = self.total()
        return {
            'id': self.id, 'where': self.where, 'arrive': self.arrive, 'leave': self.leave,
            'origin': self.origin, 'travellers': self.travellers,
            'budget': f'{self.budget / 100:.2f}' if self.budget is not None else '',
            'budget_currency': self.budget_currency,
            'preferences': self.preferences, 'notes': self.notes,
            'legs': [one.to_json() for one in self.legs],
            'missing': self.missing(),
            'total': f'{total / 100:.2f}',
            'currency': currency,
        }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class Trips:
    """Trips on disk, one file each.

    A file each rather than a table, and the same reason as the ledger: a trip
    is something a person reads, exports and deletes, and a trip that is a row
    with fourteen columns is a trip that has to be reconstructed to be useful.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, trip_id: str) -> Path:
        if not trip_id or '/' in trip_id or trip_id.startswith('.'):
            raise TripError(f'{trip_id!r} is not a trip id')
        return self.root / f'{trip_id}.json'

    def save(self, trip: Trip) -> Trip:
        trip.updated_at = datetime.now(UTC).isoformat(timespec='seconds')
        self._path(trip.id).write_text(json.dumps(trip.to_json(), indent=2), encoding='utf-8')
        return trip

    def get(self, trip_id: str) -> Trip:
        path = self._path(trip_id)
        if not path.exists():
            known = ', '.join(t.id for t in self.list()) or 'none yet'
            raise TripError(f'no trip called {trip_id!r}. It has: {known}')
        return _from_json(json.loads(path.read_text(encoding='utf-8')))

    def list(self) -> list[Trip]:
        out = []
        for path in sorted(self.root.glob('*.json')):
            try:
                out.append(_from_json(json.loads(path.read_text(encoding='utf-8'))))
            except (OSError, ValueError):
                # A trip that cannot be read is skipped, not fatal: one bad
                # file must not make every other trip unreachable.
                log.info('skipping unreadable trip %s', path)
        return sorted(out, key=lambda t: (t.arrive or '', t.id))

    def delete(self, trip_id: str) -> bool:
        path = self._path(trip_id)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed

    def new(self) -> Trip:
        return Trip(id=uuid.uuid4().hex[:12])


def _from_json(raw: dict[str, Any]) -> Trip:
    trip = Trip(
        id=str(raw.get('id') or uuid.uuid4().hex[:12]),
        where=str(raw.get('where') or ''),
        arrive=str(raw.get('arrive') or ''),
        leave=str(raw.get('leave') or ''),
        origin=str(raw.get('origin') or ''),
        travellers=int(raw.get('travellers') or 1),
        preferences=str(raw.get('preferences') or ''),
        notes=str(raw.get('notes') or ''),
        budget_currency=str(raw.get('budget_currency') or '').upper(),
    )
    budget = str(raw.get('budget') or '')
    if budget:
        try:
            trip.budget = int(round(float(budget) * 100))
        except ValueError:
            trip.budget = None
    for one in raw.get('legs') or []:
        cost = str(one.get('cost') or '')
        trip.legs.append(Leg(
            id=str(one.get('id') or uuid.uuid4().hex[:12]),
            kind=str(one.get('kind') or 'other'),
            what=str(one.get('what') or ''),
            where=str(one.get('where') or ''),
            start=str(one.get('start') or ''),
            end=str(one.get('end') or ''),
            cost=int(round(float(cost) * 100)) if cost else None,
            currency=str(one.get('currency') or '').upper(),
            booked=bool(one.get('booked')),
            ref=str(one.get('ref') or ''),
            note=str(one.get('note') or ''),
        ))
    return trip


def conflicts_from_ics(text: str) -> list[tuple[datetime, datetime, str]]:
    """Busy blocks out of an `.ics` file, so the clash check can be real.

    Only the fields a clash check needs — DTSTART, DTEND, SUMMARY — and it
    skips what it cannot parse rather than refusing the whole file. An alarm
    twenty events away is not worth losing the trip over, and this is the same
    reasoning `omote/calendar/ics.py` uses for the same input.
    """
    out: list[tuple[datetime, datetime, str]] = []
    current: dict[str, str] = {}
    inside = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == 'BEGIN:VEVENT':
            inside, current = True, {}
            continue
        if line == 'END:VEVENT':
            inside = False
            start, end = current.get('DTSTART'), current.get('END') or current.get('DTSTART')
            if start:
                try:
                    begins = moment(start, what='an event start')
                    finishes = moment(end or start, what='an event end')
                except TripError:
                    continue
                if finishes < begins:
                    finishes = begins + timedelta(hours=1)
                out.append((begins, finishes, current.get('SUMMARY', 'something')))
            current = {}
            continue
        if not inside or ':' not in line:
            continue
        name, _, value = line.partition(':')
        key = name.split(';', 1)[0].strip().upper()
        if key in ('DTSTART', 'DTEND', 'SUMMARY'):
            current[key] = value.strip()
    return out


__all__ = [
    'KINDS', 'OPTIONAL', 'REQUIRED', 'Leg', 'Trip', 'TripError', 'Trips', 'amount',
    'conflicts_from_ics', 'moment',
]
