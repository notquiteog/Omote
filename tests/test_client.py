"""Where the page and the server have to agree.

The browser client is data-driven in a few places — a table of tool verbs, a
list of approval modes in a `<select>` — and each of those is a copy of
something the Python side owns. A copy that drifts does not crash: the UI
just starts saying the wrong thing, or offering a choice the server refuses.
Both are the sort of quiet wrong that only a test notices.

These read the page and the client as text rather than running them, so every
check asserts that it found something to check. A rewrite that defeats the
parser fails loudly here instead of quietly passing.
"""

import re
from pathlib import Path
from typing import get_args

from omote.agent.approval import Mode
from omote.scheduling import Schedule

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'omote' / 'static'
TOOLS = ROOT / 'omote' / 'agent' / 'tools'


def _tool_names() -> set[str]:
    declared: set[str] = set()
    sources = [*TOOLS.glob('*.py'), ROOT / 'omote' / 'creative' / 'tools.py']
    for path in sorted(sources):
        declared |= set(re.findall(r"(?m)^    name = '(\w+)'", path.read_text()))
    assert len(declared) > 15, 'the tool scan found almost nothing — has the layout changed?'
    return declared


def test_every_tool_has_a_verb():
    """The transcript says "Ran npm test", not "shell".

    A tool with no verb falls back to its own name, which is honest but reads
    as machinery in the middle of prose. Nobody notices the day a tool is
    added; this does.
    """
    app = (STATIC / 'app.js').read_text()
    at = app.find('const VERBS = {')
    assert at != -1, 'could not find VERBS in app.js'
    verbs = dict(re.findall(r"(?m)^  (\w+): '([^']+)',", app[at : app.index('};', at)]))
    assert verbs, 'no verbs parsed'

    declared = _tool_names()
    missing = declared - set(verbs)
    assert not missing, f'tools the transcript has no plain word for: {sorted(missing)}'

    unreal = set(verbs) - declared
    assert not unreal, f'verbs for tools that no longer exist: {sorted(unreal)}'


def test_the_approval_modes_the_page_offers_are_real():
    """The approval control *sets* the mode rather than describing it.

    An `<option>` whose value is not a `Mode` used to be a cosmetic mistake.
    Since these selects send it, it is now an error message on a click.

    The composer chip and new-session dialog offer every mode. Scheduled
    tasks offer the smaller set their API accepts. There used to be a third — autopilot's
    own select, which offered everything except `read_only` on the grounds that
    a run nobody is answering cannot do anything in that mode anyway. Autopilot
    is a tool now, so the subset it offered moved into the tool's schema, where
    a wrong value is an `invalid` on the call rather than an option in the page.
    """
    html = (STATIC / 'index.html').read_text()
    # A list rather than a dict keyed by name: two of these are called `mode`
    # — one by id and one by name — and collapsing them would leave one of the
    # two unchecked while the test still passed.
    offered = [
        (attrs.strip(), set(re.findall(r'<option value="([\w_]+)"', block)))
        for attrs, block in re.findall(r'<select([^>]*)>(.*?)</select>', html, re.S)
        if re.search(r'<option value="(read_only|ask)"', block)
    ]
    assert len(offered) == 3, f'expected three mode selects, found {len(offered)}'

    real = {m.value for m in Mode}
    for where, block in offered:
        unreal = block - real
        assert not unreal, f'{where} offers {sorted(unreal)}, which the server does not know'

    # Two of them create or change a session and offer every mode.
    full = [where for where, block in offered if block == real]
    assert len(full) == 2, f'expected the composer chip and the dialog to offer all of them, got {full}'

    scheduled = next(block for where, block in offered if 'schedule-mode' in where)
    assert scheduled == set(get_args(Schedule.model_fields['mode'].annotation))
