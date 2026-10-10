"""A trip, as something you can hold.

`plan.py` is the whole of it: a trip, its legs, its arithmetic and the checks
that say whether it holds together.

There was no domain layer here before, and that was the gap. The browser can
already drive a booking form from the first field to the last, with a real
test that fills in a search and stops at the button that takes money. What
there was no way to do was *hold the plan* — ask for the details, keep them,
work out an itinerary, add it up, and notice it does not fit.

Nothing here books and nothing here spends. Booking is a browser task and it
stops where it has always stopped.
"""
