"""The companions are pixel art written as arrays of strings.

That has exactly one failure mode worth a test: a row one character short.
It shifts every pixel after it, it is invisible in a diff, and nothing else
in the system will complain — the browser will happily draw the wrong shape.
So every frame is measured here, along with the handful of other mistakes
that are easy to make in a data file and impossible to see: a colour used but
never declared, a state named `sucess`, a frame nothing draws.

These read the JavaScript as text rather than running it, so the parse leans
on the formatting the files are written in. Every check therefore asserts
that it found something to check: a reformat that defeats the parser fails
loudly here instead of quietly passing.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPANIONS = ROOT / 'omote' / 'static' / 'companions'
CREATURES = sorted(p for p in COMPANIONS.glob('*.js') if p.name not in {'engine.js', 'index.js'})
TOOLS = ROOT / 'omote' / 'agent' / 'tools'


def _span(src: str, start: int) -> tuple[int, int]:
    """The inside of the `{...}` or `[...]` that begins at `start`.

    Strings and comments are stepped over rather than counted, so a brace in
    a comment cannot throw the depth off.
    """
    opener = src[start]
    closer = {'{': '}', '[': ']'}[opener]
    depth = 0
    i = start
    while i < len(src):
        ch = src[i]
        if ch in "'\"`":
            i += 1
            while i < len(src) and src[i] != ch:
                i += 2 if src[i] == '\\' else 1
        elif src.startswith('/*', i):
            i = src.index('*/', i) + 1
        elif src.startswith('//', i):
            i = src.index('\n', i)
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return start + 1, i
        i += 1
    raise AssertionError('unbalanced brackets')


def _block(src: str, key: str, indent: str = '  ') -> str | None:
    """The body of a top-level `key: {` or `key: [` property."""
    match = re.search(r'\n' + indent + key + r': ([{\[])', src)
    if not match:
        return None
    start, end = _span(src, match.start(1))
    return src[start:end]


def _members(block: str, opener: str) -> dict[str, str]:
    """Members of a block, by name, values of the given bracket kind."""
    out = {}
    for match in re.finditer(r'(?m)^    (\w+): ' + re.escape(opener), block):
        start, end = _span(block, match.end() - 1)
        out[match.group(1)] = block[start:end]
    return out


def _strings(text: str) -> list[str]:
    return re.findall(r"'([^']*)'", text)


def parse(path: Path) -> dict:
    src = path.read_text()

    ident = re.search(r"\n  id: '(\w+)',", src)
    assert ident, f'{path.name}: no id'

    palette_block = _block(src, 'palette')
    assert palette_block, f'{path.name}: no palette block found'
    palette = dict(re.findall(r"(?m)^    (\S): '(#[0-9a-fA-F]{3,8})',", palette_block))
    assert palette, f'{path.name}: no palette entries parsed'

    variants_block = _block(src, 'variants') or ''
    variants = {
        name: dict(re.findall(r"(?m)^\s*(\S+): '(#[0-9a-fA-F]{3,8})'", body))
        for name, body in re.findall(r'(?m)^    (\w+): \{([^}]*)\},', variants_block)
    }

    frames_block = _block(src, 'frames')
    assert frames_block, f'{path.name}: no frames block found'
    frames = {name: _strings(body) for name, body in _members(frames_block, '[').items()}
    assert frames, f'{path.name}: no frames parsed — has the file been reformatted?'

    states_block = _block(src, 'states')
    assert states_block, f'{path.name}: no states block found'
    states = {}
    for name, body in _members(states_block, '{').items():
        used = re.search(r'frames: \[([^\]]*)\]', body)
        states[name] = {
            'frames': _strings(used.group(1)) if used else [],
            'variant': (re.search(r"variant: '(\w+)'", body) or [None, None])[1],
        }
    assert states, f'{path.name}: no states parsed — has the file been reformatted?'

    portrait = _block(src, 'portrait') or ''

    return {
        'path': path,
        'id': ident.group(1),
        'palette': palette,
        'variants': variants,
        'frames': frames,
        'states': states,
        'portrait': portrait,
        'src': src,
    }


CREATURE_DATA = [parse(p) for p in CREATURES]
BY_ID = pytest.mark.parametrize('c', CREATURE_DATA, ids=[c['id'] for c in CREATURE_DATA])


def test_there_are_companions():
    assert len(CREATURE_DATA) >= 5


@BY_ID
def test_frames_are_rectangular(c):
    """The one that matters. Every row of a frame is the same width."""
    for name, rows in c['frames'].items():
        assert rows, f"{c['id']}/{name}: no rows"
        width = len(rows[0])
        for y, row in enumerate(rows):
            assert len(row) == width, f"{c['id']}/{name} row {y}: {len(row)} wide, expected {width}"


@BY_ID
def test_every_pixel_has_a_colour(c):
    known = set(c['palette']) | {'.'}
    for name, rows in c['frames'].items():
        for y, row in enumerate(rows):
            unknown = set(row) - known
            assert not unknown, f"{c['id']}/{name} row {y}: {sorted(unknown)} not in the palette"


@BY_ID
def test_variants_only_recolour_what_exists(c):
    """A variant that introduces a character paints nothing: the frames can
    only contain characters the base palette declares."""
    for name, overrides in c['variants'].items():
        extra = set(overrides) - set(c['palette'])
        assert not extra, f"{c['id']}: variant {name} sets {sorted(extra)}, which no frame uses"


@BY_ID
def test_states_name_real_frames_and_variants(c):
    for name, state in c['states'].items():
        for frame in state['frames']:
            assert frame in c['frames'], f"{c['id']}: state {name} draws {frame}, which does not exist"
        if state['variant']:
            assert state['variant'] in c['variants'], f"{c['id']}: state {name} wears an undeclared variant"


@BY_ID
def test_has_an_idle_state(c):
    """Every fallback chain ends at idle, so a creature without one can be
    asked for a state it cannot draw."""
    assert 'idle' in c['states']


@BY_ID
def test_portrait_is_drawable(c):
    """The picker draws this before the creature has ever been on the perch."""
    for frame in _strings(c['portrait']):
        assert frame in c['frames'], f"{c['id']}: portrait draws {frame}, which does not exist"


@BY_ID
def test_no_frame_is_orphaned(c):
    """Art nothing draws is art nobody notices is wrong.

    Names built by template literal (`face_bar${n}`) count as used when their
    prefix appears in one.
    """
    rest = c['src'].replace(_block(c['src'], 'frames'), '')
    prefixes = re.findall(r'`(\w+)\$\{', rest)
    for name in c['frames']:
        used = f"'{name}'" in rest or any(name.startswith(p) for p in prefixes)
        assert used, f"{c['id']}: frame {name} is never drawn"


def test_states_are_in_the_shared_vocabulary():
    """A creature that defines `sucess` silently never succeeds. The engine's
    fallback table is the list of states that exist."""
    engine = (COMPANIONS / 'engine.js').read_text()
    at = engine.find('const CHAIN = {')
    assert at != -1, 'could not find CHAIN in engine.js'
    start, end = _span(engine, engine.index('{', at))
    known = set(re.findall(r'(?m)^  (\w+): \[', engine[start:end])) | {'work'}
    assert 'idle' in known and len(known) > 8

    for c in CREATURE_DATA:
        unknown = set(c['states']) - known
        assert not unknown, f"{c['id']}: states {sorted(unknown)} are not states anything asks for"


def test_ids_match_their_files_and_are_registered():
    index = (COMPANIONS / 'index.js').read_text()
    listed = re.search(r'export const COMPANIONS = \[([^\]]*)\]', index)
    assert listed, 'index.js does not export a COMPANIONS list'
    registered = [name.strip() for name in listed.group(1).split(',') if name.strip()]

    ids = [c['id'] for c in CREATURE_DATA]
    assert len(set(ids)) == len(ids), 'two companions share an id'

    for c in CREATURE_DATA:
        assert c['id'] == c['path'].stem, f"{c['path'].name} declares the id {c['id']}"
        assert c['id'] in registered, f"{c['id']} is not in index.js's COMPANIONS"
        assert f"from './{c['id']}.js'" in index, f"{c['id']}.js is never imported"


def test_every_state_has_a_caption():
    """The creature is one half of the signal; the word beside it is the other.

    The composer says what the agent is doing in plain language, and a state
    with no caption falls back to "ready" — which does not read as a gap, it
    reads as a claim that nothing is happening. That is the worst possible
    way for this table to be incomplete.
    """
    engine = (COMPANIONS / 'engine.js').read_text()
    at = engine.find('const CHAIN = {')
    assert at != -1, 'could not find CHAIN in engine.js'
    start, end = _span(engine, engine.index('{', at))
    states = set(re.findall(r'(?m)^  (\w+): \[', engine[start:end]))
    assert 'idle' in states and len(states) > 8

    index = (COMPANIONS / 'index.js').read_text()
    at = index.find('const CAPTIONS = {')
    assert at != -1, 'could not find CAPTIONS in index.js'
    start, end = _span(index, index.index('{', at))
    captions = dict(re.findall(r"(?m)^  (\w+): '([^']+)',", index[start:end]))
    assert captions, 'no captions parsed'

    missing = states - set(captions)
    assert not missing, f'states with nothing to say for themselves: {sorted(missing)}'

    unknown = set(captions) - states
    assert not unknown, f'captions for states nothing can be in: {sorted(unknown)}'


def test_every_tool_moves_the_companion():
    """The creature reacts to the tool that is running, from a table of names.

    A tool added to the server and not added there still works — it falls
    back to plain "running" — but it stops saying anything specific, and that
    is the kind of drift nobody notices. So the table is checked against the
    tools that actually exist.
    """
    engine = (COMPANIONS / 'engine.js').read_text()
    at = engine.find('const TOOL_STATES = {')
    assert at != -1, 'could not find TOOL_STATES in engine.js'
    start, end = _span(engine, engine.index('{', at))
    mapped = dict(re.findall(r'(?m)^  (\w+): \'(\w+)\',', engine[start:end]))
    assert mapped, 'no tool states parsed'

    declared = set()
    # Creative library lives with its store rather than the generic agent tools.
    for path in [*sorted(TOOLS.glob('*.py')), ROOT / 'omote' / 'creative' / 'tools.py']:
        declared |= set(re.findall(r"(?m)^    name = '(\w+)'", path.read_text()))
    assert len(declared) > 15, 'the tool scan found almost nothing — has the layout changed?'

    missing = declared - set(mapped)
    assert not missing, f'tools the companion has nothing to say about: {sorted(missing)}'

    stale = set(mapped) - declared
    assert not stale, f'the companion reacts to tools that no longer exist: {sorted(stale)}'


@pytest.fixture
def companion_page():
    """Load the actual companion module in Chromium without a daemon or provider."""
    import functools
    import http.server
    import threading

    browser_api = pytest.importorskip('playwright.sync_api')

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(COMPANIONS.parent)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with browser_api.sync_playwright() as engine:
            try:
                browser = engine.chromium.launch(headless=True)
            except browser_api.Error as exc:
                pytest.skip(f'Chromium is unavailable: {exc}')
            page = browser.new_page(viewport={'width': 800, 'height': 800})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/test-companion', lambda route: route.fulfill(content_type='text/html', body="""
                <link rel="stylesheet" href="/style.css">
                <div id="first"></div><div id="second"></div><button id="outside">Outside</button>
                <script type="module">
                import { companion, COMPANIONS } from '/companions/index.js';
                import { driverState } from '/companions/engine.js';
                window.companion = companion;
                window.driverState = driverState;
                window.creatures = COMPANIONS;
                companion.mount(document.querySelector('#first'), {place: 'below', scale: 2});
                companion.mount(document.querySelector('#second'), {place: 'below', scale: 2});
                </script>
            """))
            page.goto(f'http://127.0.0.1:{server.server_port}/test-companion')
            page.wait_for_function('window.companion && document.querySelector("#first .pet")')
            yield page, browser_api.expect
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_picker_keyboard_search_selection_and_preview(companion_page):
    page, expect = companion_page
    page.locator('#first .pet').click()
    menu = page.locator('#first .companion-menu')
    expect(menu).to_be_visible()
    search = menu.get_by_role('searchbox')
    expect(search).to_be_focused()
    search.press('ArrowUp')
    expect(menu.get_by_role('button', name='Nobody')).to_be_focused()
    search.focus()
    search.fill('maple')
    expect(menu.locator('.row:visible')).to_have_count(1)
    search.press('ArrowDown')
    expect(menu.get_by_role('button', name='Maple')).to_be_focused()
    menu.get_by_role('combobox', name='Preview a reaction').select_option('browsing')
    expect(menu.locator('.companion-preview')).to_contain_text('compass')
    menu.get_by_role('button', name='Maple').click()
    expect(menu).to_be_hidden()
    assert page.evaluate('localStorage.getItem("omote.companion")') == 'fox'
    expect(page.locator('#second .pet')).to_have_attribute('aria-label', 'Companion: Maple. Choose another.')
    expect(page.locator('#first .pet')).to_be_focused()
    page.locator('#second .pet').click()
    page.keyboard.press('Escape')
    expect(page.locator('#second .companion-menu')).to_be_hidden()
    expect(page.locator('#second .pet')).to_be_focused()
    assert page.evaluate('window.driverState().live') == 2


def test_picker_motion_preference_and_all_creature_reactions(companion_page):
    page, expect = companion_page
    page.locator('#first .pet').click()
    menu = page.locator('#first .companion-menu')
    menu.get_by_role('combobox', name='Companion motion').select_option('still')
    assert page.evaluate('window.driverState().live') == 0
    assert page.evaluate('localStorage.getItem("omote.companion.motion")') == 'still'
    expect(page.locator('#second select[aria-label="Companion motion"]')).to_have_value('still')
    # Every authored callback, palette and frame is exercised through the real renderer.
    states = ['idle', 'typing', 'thinking', 'reading', 'writing', 'running', 'browsing',
              'desktop', 'grinding', 'waiting', 'listening', 'speaking', 'success', 'error']
    for creature in ['fox', 'owl', 'octopus', 'drone']:
        page.evaluate('(id) => window.companion.preview(window.companion.pickers[0], window.creatures.find(c => c.id === id))', creature)
        for state in states:
            menu.get_by_role('combobox', name='Preview a reaction').select_option(state)
    menu.get_by_role('combobox', name='Companion motion').select_option('animated')
    assert page.evaluate('window.driverState().live') == 3
    page.locator('#outside').focus()
    expect(menu).to_be_hidden()
    assert page.evaluate('window.driverState().live') == 2
    # Recreating the module on reload restores the saved device preference.
    page.reload()
    page.wait_for_function('window.companion && document.querySelector("#first .pet")')
    expect(page.locator('#first select[aria-label="Companion motion"]')).to_have_value('animated')


def test_picker_fits_small_viewport_and_empty_search(companion_page):
    page, expect = companion_page
    page.set_viewport_size({'width': 320, 'height': 480})
    page.locator('#first .pet').click()
    menu = page.locator('#first .companion-menu')
    box = menu.bounding_box()
    assert box['x'] >= 0 and box['y'] >= 0
    assert box['x'] + box['width'] <= 320
    assert box['y'] + box['height'] <= 480
    menu.get_by_role('searchbox').fill('no matching creature')
    expect(menu.get_by_text('No companions match this search.')).to_be_visible()
    expect(menu.locator('.row:visible')).to_have_count(0)


def test_authored_reactions_draw_real_frames_with_finite_motion(companion_page):
    page, _ = companion_page
    drawn = page.evaluate('''async () => {
      const { Perch, setMotionPreference } = await import('/companions/engine.js');
      setMotionPreference('animated');
      const rendered = {};
      for (const def of window.creatures) {
        const perch = new Perch(document.createElement('canvas'), {scale: 1});
        perch.use(def);
        const paint = perch.paint.bind(perch);
        let frames = [];
        perch.paint = (name, x, y, ...rest) => {
          if (!(name in def.frames)) throw new Error(`${def.id}: unknown frame ${name}`);
          if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error(`${def.id}: invalid position`);
          frames.push(name);
          paint(name, x, y, ...rest);
        };
        rendered[def.id] = {};
        for (const state of ['idle', 'typing', 'thinking', 'reading', 'writing', 'running',
                             'browsing', 'desktop', 'grinding', 'waiting', 'listening', 'speaking', 'success', 'error']) {
          perch.use(def);
          if (state === 'success' || state === 'error') perch.flash(state);
          else perch.set(state);
          frames = [];
          for (const elapsed of [0, 100, 300, 700, 1100]) perch.draw(performance.now() + elapsed);
          if (!frames.length) throw new Error(`${def.id}: no rendered frames for ${state}`);
          rendered[def.id][state] = [...new Set(frames)];
        }
        perch.destroy();
      }
      return rendered;
    }''')
    assert 'compass' in drawn['fox']['browsing']
    assert {'book_a', 'book_b'} <= set(drawn['owl']['reading'])
    assert 'pencil' in drawn['octopus']['writing']
    assert 'beam' in drawn['drone']['desktop']
    assert 'ink' in drawn['octopus']['error']
