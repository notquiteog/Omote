---
name: world-travel
description: Plan vacations and business travel, compare real hotel options, build a time-zone-aware itinerary, and prepare bookings for the user's final confirmation.
argument-hint: "[destination, dates, and preferences]"
---

Start with the `trip` intake when available. Keep what the user has already said
and ask only consequential missing questions in one concise `ask_user` call:
origin/destination, dates, people/rooms, total budget and currency, accessibility,
transport needs, cancellation preference and special requirements. Do not assume
citizenship, residence, mobility or language from a person's name or location.

Search current sources using the web tools, then inspect hotel/airline/rail
booking pages using the actual browser. Use official government sources for entry
requirements. Explain which nationality/residency facts are needed rather than
collecting passport numbers in chat. Do not fabricate availability, rates, visa
rules, opening hours or exchange rates. If a site is unavailable in this region,
report that and use a reachable authorized source; do not bypass access controls.

Compare concrete options with travel time, exact room/occupancy, meal plan,
accessibility, cancellation deadline and total including taxes/fees. Quote costs
in their actual currencies. The trip tool keeps separate currency totals; only
compare converted budgets with a sourced rate and timestamp. Record local dates
and explicit UTC offsets for itinerary legs and include place/time-zone names in
notes; check overnight travel and daylight-saving changes. Use `trip check` for
schedule/budget conflicts, and update the plan from verified results.

For flights, compare the actual airports (not just city names), local departure
and arrival dates with offsets, overnight arrivals, stops and connection time,
fare class, carry-on/checked baggage, seat fees, change/refund terms and total for
all travelers. Keep separate tickets and self-transfers explicit. For hotels,
check the exact room, guest count, bed configuration, number of nights, mandatory
taxes/resort fees/deposits and the cancellation deadline in the property's time
zone. Recheck live availability and the final price at checkout; search results
are not a reservation or a guaranteed quote.

Before a booking, show the final provider, dates, traveller/room details, currency,
full amount payable now/later, cancellation terms and charge responsibility.
Ask missing traveller questions without inventing details. Let the user complete
login, card and identity checks through `browser_hand_over` when possible.
Click the final reservation/payment button with `intent: purchase` even for a
pay-at-property or refundable reservation, since it still creates a commitment.
Do not call another API or shell command to evade the final confirmation.

After submission, inspect the confirmation number and status. Mark a leg booked
only after that evidence exists. Save or send a receipt only if requested. If the
page fails after submission, check booking history before retrying. A plan,
pre-filled form or approval prompt is not a completed booking.

Record the observed booking reference and cancellation/change details on the
matching trip leg. Mark canceled reservations unbooked only after confirmation;
cancellation may involve fees and uses the same commitment review. Do not store
card, passport or identity-document numbers in itinerary notes or vector memory.
