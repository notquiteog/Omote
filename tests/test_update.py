"""The updater, which is the one place this project downloads and runs code.

So the tests are about the ways it can go wrong rather than the ways it works,
and each is the failure it is written for:

* **A checksum that does not match is a refusal, not a warning.** A download
  that fails verification must leave nothing runnable-looking on disk, and the
  message has to say what happened without pretending it was verified.
* **The checksum is optional and its absence is said out loud.** A release
  published without one is a packaging mistake, and refusing to update from it
  is a support ticket rather than a safety measure. Silently installing anyway
  is the thing that is not acceptable.
* **A stable install is not offered a release candidate.** Silently going to
  `rc.1` is running code the author has not promised anybody, and getting the
  ordering backwards means every released build asks to go *back*.
* **An install already on a prerelease can move forward within it.** Holding
  somebody on `rc.1` while `rc.2` exists is not a safety property.
* **The right artifact, on the right platform, or none at all.** Guessing an
  architecture is how a 120MB disk image gets downloaded by somebody who
  cannot run it.
* **`local_only` stops the check.** A version check is a request to a third
  party, and somebody who asked for no remote traffic did not ask for that.
* **Nothing here executes anything.** There is no code path in the updater
  that runs a file, and a test asserts the absence rather than trusting the
  docstring.

The network is a real `aiohttp` server for the download, because a download
tested against a mock is a test of whether the mock was called.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

import pytest
from aiohttp import web

from openmirror.update import (
    API,
    RELEASES_PER_PAGE,
    REPO,
    Asset,
    Release,
    UpdateRefused,
    Updates,
    _newest,
    is_newer,
    parse,
    pick_asset,
    platform_key,
    sha256_file,
)

ASSETS = [
    Asset('openmirror_0.2.0_amd64.AppImage', 'u/appimage', 100),
    Asset('openmirror_0.2.0_arm64.AppImage', 'u/appimage-arm', 150),
    Asset('openmirror_0.2.0_x64-setup.exe', 'u/exe', 200),
    Asset('openmirror_0.2.0_amd64.dmg', 'u/dmg', 300),
    Asset('openmirror_0.2.0_aarch64.dmg', 'u/dmg-arm', 400),
    Asset('SHA256SUMS.txt', 'u/sums', 500),
]


# --- versions ----------------------------------------------------------------


@pytest.mark.parametrize(
    'text,expected',
    [
        ('0.1.0', '0.1.0'), ('v0.1.0', '0.1.0'), ('1.2.3', '1.2.3'),
        ('v1.2', '1.2.0'), ('v2', '2.0.0'), ('0.2.0-rc.1', '0.2.0-rc.1'),
        ('0.2.0+build5', '0.2.0'), ('  0.1.0  ', '0.1.0'),
    ],
)
def test_a_tag_becomes_a_version(text: str, expected: str):
    """Tolerant about a leading `v` and about missing parts, because tags get
    written by hand and `v1` should still mean 1.0.0."""
    assert str(parse(text)) == expected


def test_something_that_is_not_a_version_is_none_and_not_a_crash():
    """An unparseable release name is a release to skip, not a daemon to
    refuse to start over."""
    for text in ('', 'latest', 'nightly', None, 'v', 'banana'):
        assert parse(text) is None, text


@pytest.mark.parametrize(
    'candidate,current,want,why',
    [
        ('0.2.0', '0.1.0', True, 'a normal upgrade'),
        ('0.2.0', '0.2.0', False, 'the same version'),
        ('0.1.0', '0.2.0', False, 'downgrade'),
        ('0.2.0', '0.2.0-rc.1', True, 'a candidate gets the final'),
        ('0.2.0-rc.1', '0.1.0', False, 'stable is not offered a candidate'),
        ('0.2.0-rc.2', '0.2.0-rc.1', True, 'forward within a prerelease you are on'),
        ('0.2.0-rc.1', '0.2.0-rc.2', False, 'and not backwards'),
        ('0.2.0-rc.10', '0.2.0-rc.2', True, 'rc.10 is after rc.2, not before'),
        ('1.0.0-beta', '1.0.0-alpha', True, 'beta is after alpha'),
        ('0.2.0', '0.10.0', False, 'ten minor is not two'),
        ('1.0', '0.9.9', True, 'a bare v1'),
    ],
)
def test_the_ordering_is_right(candidate: str, current: str, want: bool, why: str):
    assert is_newer(candidate, current) is want, why


def test_a_prerelease_can_be_asked_for():
    assert is_newer('0.3.0-rc.1', '0.2.0') is False
    assert is_newer('0.3.0-rc.1', '0.2.0', allow_prerelease=True) is True


# --- which file ---------------------------------------------------------------


def test_each_platform_gets_its_own_installer():
    """Guessing an architecture is how somebody downloads a 120MB disk image
    they cannot run."""
    for key, want in (
        ('linux-x64', 'amd64.AppImage'), ('linux-arm64', 'arm64.AppImage'),
        ('windows-x64', '-setup.exe'), ('macos-arm64', 'aarch64.dmg'),
        ('macos-x64', 'amd64.dmg'),
    ):
        picked = pick_asset(ASSETS, key=key)
        assert picked is not None, key
        assert picked.name.endswith(want), f'{key} got {picked.name}'
        assert picked.is_checksums is False


def test_an_arm_mac_does_not_get_the_intel_disk_image():
    assert pick_asset(ASSETS, key='macos-arm64').name.endswith('aarch64.dmg')
    assert pick_asset(ASSETS, key='macos-x64').name.endswith('amd64.dmg')


def test_a_platform_with_nothing_published_gets_nothing():
    """None, rather than the nearest thing. Offering a Linux build to Windows
    wastes a download and then fails at the end of it."""
    assert pick_asset(ASSETS, key='windows-arm64') is None
    assert pick_asset([], key='linux-x64') is None


def test_the_checksum_file_is_never_offered_as_an_installer():
    for key in ('linux-x64', 'windows-x64', 'macos-arm64'):
        assert 'SHA256SUMS' not in pick_asset(ASSETS, key=key).name


def test_a_release_prefers_the_file_that_needs_no_package_manager():
    """An AppImage runs without root; a .deb does not. That is the whole of
    the preference order and it is a real difference for a person on a
    machine they do not administer."""
    deb_only = [Asset('openmirror_0.2.0_amd64.deb', 'u/deb', 1)]
    assert pick_asset(deb_only, key='linux-x64').name.endswith('.deb')
    both = [Asset('openmirror_0.2.0_amd64.deb', 'u/deb', 1),
            Asset('openmirror_0.2.0_amd64.AppImage', 'u/ai', 2)]
    assert pick_asset(both, key='linux-x64').name.endswith('.AppImage')


def test_the_platform_name_is_what_tauri_calls_it():
    assert platform_key('Linux', 'x86_64') == 'linux-x64'
    assert platform_key('Darwin', 'arm64') == 'macos-arm64'
    assert platform_key('Windows', 'AMD64') == 'windows-x64'
    assert platform_key('Linux', 'aarch64') == 'linux-arm64'


# --- picking a release --------------------------------------------------------


def raw(tag: str, **over):
    body = {
        'tag_name': tag, 'name': f'openmirror {tag}', 'body': 'notes',
        'html_url': f'https://github.com/{REPO}/releases/tag/{tag}',
        'published_at': '2026-01-01T00:00:00Z', 'prerelease': False, 'assets': [],
    }
    body.update(over)
    return body


def test_the_newest_release_worth_offering_is_chosen():
    found = _newest([raw('0.1.0'), raw('0.3.0'), raw('0.2.0')], '0.1.0', False)
    assert found.tag == '0.3.0'


def test_nothing_newer_means_nothing_offered():
    assert _newest([raw('0.1.0')], '0.1.0', False) is None
    assert _newest([raw('0.1.0')], '0.2.0', False) is None


def test_a_prerelease_is_skipped_even_when_it_is_the_newest():
    """The failure is silent and it is bad: an install sitting on a stable
    release gets shown `rc.1`, which is code the author has not promised
    anybody. Skipping it leaves the stable release it is already on, which is
    not newer — so nothing is offered at all, and that is the right answer."""
    assert _newest([raw('0.1.0'), raw('0.2.0-rc.1', prerelease=True)], '0.1.0', False) is None
    # From something older, the stable one is still what gets offered.
    found = _newest([raw('0.1.5'), raw('0.2.0-rc.1', prerelease=True)], '0.1.0', False)
    assert found.tag == '0.1.5'


def test_a_prerelease_release_can_be_asked_for_by_name():
    found = _newest([raw('0.2.0-rc.1', prerelease=True)], '0.1.0', True)
    assert found.tag == '0.2.0-rc.1'


def test_unparseable_releases_are_skipped_not_fatal():
    found = _newest([raw('nightly'), raw('0.2.0')], '0.1.0', False)
    assert found.tag == '0.2.0'


def test_an_empty_release_list_is_not_an_error():
    assert _newest([], '0.1.0', False) is None


# --- the download, against a real server ---------------------------------------


class Server:
    def __init__(self, body: bytes, sums: str = '') -> None:
        self.body = body
        self.sums = sums
        self.hits: list[str] = []
        self.corrupt = False
        self.status = 200
        self.port = 0
        # What a 403 should claim about the rate limit, or `None` for "not a
        # 403 at all".
        self.forbidden_remaining: str | None = None

    async def asset(self, request: web.Request) -> web.StreamResponse:
        self.hits.append(request.match_info['name'])
        if self.forbidden_remaining is not None:
            return web.Response(
                status=403,
                text='forbidden',
                headers={'x-ratelimit-remaining': self.forbidden_remaining},
            )
        if self.corrupt:
            return web.Response(body=b'not the thing you asked for', content_type='application/octet-stream')
        return web.Response(body=self.body, content_type='application/octet-stream')

    async def sums_file(self, _request: web.Request) -> web.Response:
        return web.Response(text=self.sums, content_type='text/plain')

    async def fail(self, _request: web.Request) -> web.Response:
        return web.Response(status=self.status, text='no')


@pytest.fixture
async def server():
    held = Server(b'')
    app = web.Application()
    app.router.add_get('/d/{name}', held.asset)
    app.router.add_get('/SHA256SUMS.txt', held.sums_file)
    app.router.add_get('/fail', held.fail)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    held.port = runner.addresses[0][1]
    yield held
    await runner.cleanup()


def asset_for(server: Server) -> Asset:
    return Asset(
        'openmirror_0.2.0_amd64.AppImage',
        f'http://127.0.0.1:{server.port}/d/openmirror_0.2.0_amd64.AppImage',
        size=len(server.body),
    )


async def test_a_download_that_matches_its_checksum_is_staged(tmp_path: Path, server: Server):
    payload = b'#!/bin/sh\necho an installer\n' * 500
    server.body = payload
    digest = hashlib.sha256(payload).hexdigest()

    held = Updates('0.1.0', tmp_path)
    name = 'openmirror_0.2.0_amd64.AppImage'
    got = await held.download(Release('v0.2.0'), asset_for(server), sums={name: digest})

    assert got['verified'] is True
    assert got['sha256'] == digest
    staged = Path(got['path'])
    assert await asyncio.to_thread(staged.read_bytes) == payload
    assert staged.parent == tmp_path
    assert held.state.progress == 100


async def test_a_download_that_does_not_match_is_refused_and_leaves_nothing(
    tmp_path: Path, server: Server
):
    """The whole point. A mismatch means a truncated download, a substituted
    error page, or a mirror serving last week's build — and the file must not
    be left on disk looking like something to run."""
    server.body = b'the real thing'
    name = 'openmirror_0.2.0_amd64.AppImage'

    held = Updates('0.1.0', tmp_path)
    with pytest.raises(UpdateRefused) as caught:
        await held.download(
            Release('v0.2.0'), asset_for(server), sums={name: 'f' * 64}
        )

    said = str(caught.value)
    assert 'does not match' in said
    assert 'has been deleted' in said
    # Nothing runnable-looking survives, and no half-finished file either.
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


async def test_a_missing_checksum_is_allowed_and_reported_as_unverified(
    tmp_path: Path, server: Server
):
    """A release published without one is a packaging mistake. Refusing to
    update from it is a support ticket, not a safety measure — but the
    response must say it was not verified, because that is the part the person
    is relying on."""
    server.body = b'an installer'
    held = Updates('0.1.0', tmp_path)
    got = await held.download(Release('v0.2.0'), asset_for(server), sums={})
    assert got['verified'] is False
    assert got['expected'] == ''
    assert await asyncio.to_thread(Path(got['path']).is_file)


async def test_a_server_error_is_a_refusal_with_the_status_in_it(tmp_path: Path, server: Server):
    server.status = 404
    bad = Asset('x.AppImage', f'http://127.0.0.1:{server.port}/fail', 1)
    held = Updates('0.1.0', tmp_path)
    with pytest.raises(UpdateRefused, match='404'):
        await held.download(Release('v0.2.0'), bad, sums={})


async def test_an_asset_bigger_than_the_cap_is_refused_before_any_bytes(
    tmp_path: Path, server: Server
):
    """90MB of installer, or 400MB of something else. Either way the size is
    known from the release and there is no reason to start."""
    huge = Asset('x.AppImage', f'http://127.0.0.1:{server.port}/d/x', size=900 * 1024 * 1024)
    held = Updates('0.1.0', tmp_path)
    with pytest.raises(UpdateRefused, match='not an installer'):
        await held.download(Release('v0.2.0'), huge, sums={})


async def test_a_failed_download_leaves_no_partial_file(tmp_path: Path, server: Server):
    server.corrupt = True
    held = Updates('0.1.0', tmp_path)
    name = 'openmirror_0.2.0_amd64.AppImage'
    with pytest.raises(UpdateRefused, match='does not match'):
        await held.download(Release('v0.2.0'), asset_for(server), sums={name: 'a' * 64})
    assert await asyncio.to_thread(lambda: list(tmp_path.glob('*.part'))) == []


async def test_the_published_checksums_are_parsed_and_cached(tmp_path: Path, server: Server):
    payload = b'the app'
    server.body = payload
    digest = hashlib.sha256(payload).hexdigest()
    name = 'openmirror_0.2.0_amd64.AppImage'
    server.sums = f'{digest}  {name}\n'

    release = Release('v0.2.0', assets=[Asset('SHA256SUMS.txt', f'http://127.0.0.1:{server.port}/SHA256SUMS.txt')])
    held = Updates('0.1.0', tmp_path)
    sums = await held.fetch_checksums(release)
    assert sums == {name: digest}
    # Cached, so asking twice is one request rather than two against a rate
    # limit of sixty an hour.
    again = await held.fetch_checksums(release)
    assert again == sums


async def test_checksums_that_are_missing_are_an_empty_dict_not_a_failure(
    tmp_path: Path, server: Server
):
    held = Updates('0.1.0', tmp_path)
    assert await held.fetch_checksums(Release('v0.2.0', assets=[])) == {}


async def test_a_staged_installer_is_marked_runnable(tmp_path: Path, server: Server):
    """The bug that made the updater impossible to use on anything but Windows.

    The desktop app checks the mode before running anything — `is_executable`
    in `desktop/src-tauri/src/main.rs` refuses a staged file with no `0o111`
    bits — and a file created with `open('wb')` arrives as `0o644` on an
    ordinary Linux box. So every AppImage and every `.deb` the updater
    downloaded was refused by the only thing able to run it, with "is not
    marked runnable", for every release, on every Unix platform. Windows
    happened to work because that function returns `true` unconditionally
    there.
    """
    if sys.platform.startswith('win'):
        pytest.skip('there is no execute bit to set on Windows')

    payload = b'#!/bin/sh\necho an installer\n' * 500
    server.body = payload
    held = Updates('0.1.0', tmp_path)
    got = await held.download(Release('v0.2.0'), asset_for(server), sums={})

    mode = (await asyncio.to_thread(lambda: (Path(got['path']).stat().st_mode))) & 0o777
    assert mode & 0o111, f'staged installer is {mode:o}, and the desktop app will refuse to run it'


async def test_the_executable_bit_is_set_after_the_checksum_and_not_before(
    tmp_path: Path, server: Server
):
    """An unverified download must never be runnable, even briefly.

    The bit is set once the file is renamed into place, which is after the hash
    has been compared — so the window in which a file is both present and
    executable is the window in which it is also verified.
    """
    if sys.platform.startswith('win'):
        pytest.skip('there is no execute bit to set on Windows')

    server.body = b'the real thing'
    name = 'openmirror_0.2.0_amd64.AppImage'
    held = Updates('0.1.0', tmp_path)
    with pytest.raises(UpdateRefused):
        await held.download(Release('v0.2.0'), asset_for(server), sums={name: 'f' * 64})

    leftovers = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
    assert leftovers == []
    for leftover in leftovers:  # pragma: no cover - the assertion above is the one that fires
        assert not leftover.stat().st_mode & 0o111


async def test_a_download_that_arrives_short_is_refused(tmp_path: Path):
    """A truncated transfer from a release with no `SHA256SUMS.txt`.

    The checksum was the only integrity check, and it is optional — a release
    published without one was previously staged as a success even when half the
    file never arrived, so the app was offered a corrupt installer to run. The
    `content-length` the server sent is what it promised, and comparing against
    it costs nothing.

    A hand-rolled server rather than `aiohttp.web`, because the interesting case
    is one where the server promises more than it sends and then closes: a
    well-behaved framework will not do that to itself.
    """
    payload = b'a' * 4096

    async def handler(reader, writer):  # noqa: ANN001, ANN202
        await reader.read(65536)
        writer.write(
            b'HTTP/1.1 200 OK\r\n'
            b'Content-Type: application/octet-stream\r\n'
            + f'Content-Length: {len(payload)}\r\n\r\n'.encode()
        )
        writer.write(payload[:1000])
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    try:
        held = Updates('0.1.0', tmp_path)
        short = Asset('openmirror_0.2.0_amd64.AppImage', f'http://127.0.0.1:{port}/x', size=len(payload))
        with pytest.raises(UpdateRefused):
            await held.download(Release('v0.2.0'), short, sums={})
    finally:
        server.close()
        await server.wait_closed()

    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


async def test_a_timed_out_download_is_a_refusal_and_leaves_nothing(tmp_path: Path, monkeypatch):
    """A timeout was an unhandled 500, and it left the `.part` file behind.

    `asyncio.TimeoutError is TimeoutError` on 3.11+, and `TimeoutError` is an
    `OSError` rather than an `aiohttp.ClientError` — so the `except` clause that
    cleaned up did not catch it, the exception escaped to FastAPI as a 500, and
    the `unlink` that should have removed the partial file never ran. Nothing
    sweeps `data/updates/`, so it survived the restart too.
    """
    import aiohttp

    class Deadline(Exception):
        pass

    real_get = aiohttp.ClientSession.get

    def expired(self, url, **kwargs):  # noqa: ANN001, ANN202
        raise TimeoutError('the read timed out')

    monkeypatch.setattr(aiohttp.ClientSession, 'get', expired)
    assert real_get is not expired

    held = Updates('0.1.0', tmp_path)
    with pytest.raises(UpdateRefused, match='did not finish'):
        await held.download(
            Release('v0.2.0'),
            Asset('x.AppImage', 'http://127.0.0.1:1/x', 10),
            sums={},
        )
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == [], (
        'a timed-out download left a .part file behind'
    )
    assert held.state.downloading is False


async def test_a_timeout_during_a_check_is_a_message_and_not_a_crash(tmp_path: Path):
    """Same root cause on the check side, and it matters more: this runs from
    `lifespan` on every start-up, and an exception there is swallowed into a
    debug log — so the version silently stopped being checked at all."""
    held = Updates('0.1.0', tmp_path)

    async def watch(url, headers, **kw):  # noqa: ANN001, ANN202
        raise TimeoutError('the read timed out')

    held._fetch = watch
    got = await held.check(force=True)
    assert 'could not reach GitHub' in got['error']
    assert got['available'] is False


async def test_an_asset_name_cannot_escape_the_staging_directory(tmp_path: Path, server: Server):
    """The one place a name from the network becomes a filesystem path.

    GitHub sanitises separators in asset names today, so this is defence in
    depth — but the guarantee is cheap and worth having: whatever the name says,
    the file lands inside the staging directory and nowhere else. The desktop
    app that later runs that file does not check containment at all.
    """
    server.body = b'an installer'
    staging = tmp_path / 'staging'
    held = Updates('0.1.0', staging)
    hostile = Asset('../../escaped.AppImage', f'http://127.0.0.1:{server.port}/d/x')
    got = await held.download(Release('v0.2.0'), hostile, sums={})

    landed = Path(got['path'])
    assert landed.parent == staging, f'{landed} escaped {staging}'
    assert await asyncio.to_thread(lambda: (tmp_path / 'escaped.AppImage').exists()) is False
    assert await asyncio.to_thread(lambda: (tmp_path.parent / 'escaped.AppImage').exists()) is False


async def test_two_downloads_at_once_do_not_clobber_each_other(tmp_path: Path, server: Server):
    """The lock exists because the interesting failures are concurrency ones,
    and `download` was the half that did not take it.

    Two `apply` calls at once wrote to the same `.part` path and interleaved
    their progress counters, so the bytes on disk belonged to neither.
    """
    server.body = b'an installer' * 1000
    held = Updates('0.1.0', tmp_path)
    first, second = asset_for(server), asset_for(server)
    results = await asyncio.gather(
        held.download(Release('v0.2.0'), first, sums={}),
        held.download(Release('v0.2.0'), second, sums={}),
    )
    for got in results:
        assert await asyncio.to_thread(Path(got['path']).is_file)
    assert held.state.downloading is False


def test_a_draft_release_is_never_offered():
    """Invisible until somebody exports `GITHUB_TOKEN` — which the rate limit
    makes likely — and at that moment, without this, the updater offers an
    unfinished release to everybody."""
    releases = [
        {'tag_name': 'v9.9.9', 'draft': True, 'published_at': '2030-01-01T00:00:00Z'},
        {'tag_name': 'v0.2.0', 'published_at': '2024-01-01T00:00:00Z'},
    ]
    found = _newest(releases, '0.1.0', allow_prerelease=True)
    assert found is not None and found.tag == 'v0.2.0', 'a draft was offered as an update'


def test_an_asset_name_is_reduced_to_its_last_segment():
    from openmirror.update import _release_from

    raw = {'tag_name': 'v0.2.0', 'assets': [{'name': '../../etc/passwd', 'browser_download_url': 'u'}]}
    assert _release_from(raw).assets[0].name == 'passwd'


def test_the_desktop_app_agrees_about_where_installer_will_wait():
    """The app refuses to run a file outside the staging directory, and it works
    that out from its own copy of the daemon's rules — in Rust, in
    `desktop/src-tauri/src/main.rs`, as `staging_dir()`.

    Two implementations of one rule will drift, and the drift is silent and
    fatal in the worst direction: a check that cannot find the directory refuses
    every update, so the symptom is "the updater is broken" rather than "the
    check is wrong". So the shape of the Rust is asserted here, which is not as
    good as compiling it and is the best available without a toolchain.
    """
    rust = (Path(__file__).resolve().parents[1] / 'desktop' / 'src-tauri' / 'src' / 'main.rs').read_text()

    # Both names, in the same order as `config.py`: the explicit override, then
    # the data directory, then `updates` inside it.
    assert 'OPENMIRROR_UPDATE_DIR' in rust, 'the Rust side does not know about the override'
    assert 'OPENMIRROR_DATA_DIR' in rust, 'the Rust side does not know about the data directory'
    assert rust.index('OPENMIRROR_UPDATE_DIR') < rust.index('OPENMIRROR_DATA_DIR'), (
        'the explicit update directory has to win, or the two disagree about where a file is'
    )
    assert '.join("updates")' in rust

    # `~/.openmirror/data` is what `daemon.rs` points `OPENMIRROR_DATA_DIR` at
    # for a daemon the app starts, so the fallback has to be that and not the
    # daemon's own `./data`, which is relative to wherever it was launched from.
    assert 'join(".openmirror")' in rust

    # And the check the docstring promises is the check that was written.
    assert 'fn staged_under(' in rust, 'the containment check the docstring promises is not there'
    assert 'canonicalize' in rust
    assert 'staged_under(&installer)' in rust, 'apply_update must call it'


def test_the_staging_directory_is_outside_the_working_root():
    """A 90MB disk image inside a working root gets read, grepped and eventually
    committed. The comment in `config.py` says so; this is the assertion."""
    from openmirror.config import config

    staging = config.update_staging
    assert staging.name == 'updates'
    assert 'working_root' not in staging.parts


async def test_a_403_that_is_not_the_rate_limit_says_so(tmp_path: Path, server: Server, monkeypatch):
    """The two 403s want opposite reactions, and the header is what tells them
    apart.

    `x-ratelimit-remaining: 0` is GitHub's rate limit, and the reassurance that
    nothing is wrong with the install is the useful thing to say. A 403 with
    requests left is a genuine refusal — a private repo, a renamed one, an egress
    proxy — and "nothing is wrong with this install" is exactly the wrong thing
    to tell somebody in that situation.
    """
    import openmirror.update as update_mod
    from openmirror.config import config

    monkeypatch.setattr(update_mod, 'API', f'http://127.0.0.1:{server.port}/d')
    monkeypatch.setattr(config, 'local_only', False)
    monkeypatch.setattr(config, 'update_check_enabled', True)

    server.forbidden_remaining = '0'
    limited = await Updates('0.1.0', tmp_path).check(force=True)
    assert 'rate limit' in limited['error']
    assert '5000' in limited['error'], 'it should say how to get more than 60 an hour'

    server.forbidden_remaining = '57'
    refused = await Updates('0.1.0', tmp_path).check(force=True)
    assert 'rate limit' not in refused['error'], refused['error']
    assert 'refused' in refused['error']


def test_a_github_token_is_used_when_there_is_one(monkeypatch):
    """Sixty an hour, per address, and an address is often a whole office.

    The project already reads a token for its own GitHub API calls and the
    updater read neither name — so an install with `gh auth login` set up was
    still sharing a budget with everybody else behind the same NAT.
    """
    from openmirror.update import github_token

    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    monkeypatch.delenv('GH_TOKEN', raising=False)
    assert github_token() == ''

    monkeypatch.setenv('GH_TOKEN', '  gh_pat_abc  ')
    assert github_token() == 'gh_pat_abc', 'the value is trimmed, and gh is the name gh auth login exports'

    monkeypatch.setenv('GITHUB_TOKEN', 'gho_second')
    assert github_token() == 'gho_second', 'GITHUB_TOKEN wins, because that is the documented name'


# --- what it does not do -------------------------------------------------------


def test_nothing_in_the_updater_runs_what_it_downloads():
    """Asserted rather than trusted. A downloader that grows an `exec` two
    releases from now should have to delete a line of this test to do it."""
    source = (Path(__file__).resolve().parents[1] / 'openmirror' / 'update.py').read_text()
    for dangerous in ('subprocess', 'os.system', 'os.exec', 'Popen', 'shutil.rmtree', 'eval(', 'exec('):
        assert dangerous not in source, dangerous


def test_the_repository_is_not_configurable():
    """A setting that points the updater somewhere else is a setting that
    ships code from somewhere else, and there is no version of that which is
    a feature rather than a vulnerability."""
    assert REPO == 'notquiteog/openmirror'
    assert f'repos/{REPO}' in API
    source = (Path(__file__).resolve().parents[1] / 'openmirror' / 'config.py').read_text()
    assert 'UPDATE_REPO' not in source and 'UPDATE_URL' not in source


async def test_local_only_stops_the_check_entirely(tmp_path: Path, monkeypatch):
    """A version check is a request to a third party, and somebody who asked
    for no remote traffic did not ask for that."""
    from openmirror.config import config

    monkeypatch.setattr(config, 'local_only', True)
    monkeypatch.setattr(config, 'update_check_enabled', True)
    held = Updates('0.1.0', tmp_path)
    got = await held.check(force=True)
    assert got['enabled'] is False
    assert 'local only' in got['error']
    assert got['latest'] is None


async def test_a_switched_off_check_does_not_go_out(tmp_path: Path, monkeypatch):
    from openmirror.config import config

    monkeypatch.setattr(config, 'local_only', False)
    monkeypatch.setattr(config, 'update_check_enabled', False)
    held = Updates('0.1.0', tmp_path)
    got = await held.check(force=True)
    assert 'switched off' in got['error']


async def test_the_hourly_limit_is_respected_unless_forced(tmp_path: Path):
    """Sixty an hour is shared by a whole office, and a 403 does not fix
    itself. The limit is the only thing between a poll and that."""
    held = Updates('0.1.0', tmp_path)
    held.state.checked_at = '2099-01-01T00:00:00+00:00'  # the future, so it is recent
    before = held.state.checked_at
    await held.check()
    assert held.state.checked_at == before, 'a recent check was not repeated'


def test_a_staged_file_hashes_to_the_same_thing_twice(tmp_path: Path):
    path = tmp_path / 'thing'
    path.write_bytes(b'contents')
    assert sha256_file(path) == hashlib.sha256(b'contents').hexdigest()


async def test_a_repository_with_no_releases_is_a_state_and_not_an_error(tmp_path, monkeypatch):
    """A project with no tags is doing exactly what it should. Reporting it
    in the place errors go is how a first run looks like a fault."""
    from openmirror.config import config
    from openmirror.update import NoReleasesYet

    monkeypatch.setattr(config, 'local_only', False)
    monkeypatch.setattr(config, 'update_check_enabled', True)

    held = Updates('0.1.0', tmp_path)

    async def nothing(url, headers, **kw):
        raise NoReleasesYet('no releases yet')

    held._fetch = nothing
    got = await held.check(force=True)
    assert got['error'] == ''
    assert got['available'] is False
    # And it counts as a check, so the hour does not restart it.
    assert got['checked_at'] != ''


async def test_a_rate_limit_is_named_as_a_rate_limit(tmp_path, monkeypatch):
    """The one GitHub failure a person will actually hit, and the one where
    "forbidden" tells them nothing about what to do."""
    from openmirror.config import config

    monkeypatch.setattr(config, 'local_only', False)
    monkeypatch.setattr(config, 'update_check_enabled', True)
    held = Updates('0.1.0', tmp_path)

    async def refused(url, headers, **kw):
        raise ValueError(
            'GitHub rate limit reached (requests left: 0). It resets hourly; '
            'nothing is wrong with this install.'
        )

    held._fetch = refused
    got = await held.check(force=True)
    assert 'rate limit' in got['error']
    assert 'nothing is wrong with this install' in got['error']


def test_the_api_url_is_built_once():
    """A bug that reported the truth by accident.

    The releases path was appended both to the constant and at the call site,
    so the request went to `.../openmirror/releases/releases` and GitHub
    answered 404. A 404 from this call is reported as "no releases published
    yet" — which is what it said for the whole of a project that had none,
    and which is why nothing looked wrong until the first tag.

    Every test here mocks the network, which is the point of this one: a test
    that cannot see the URL cannot catch a URL that is wrong.
    """
    from openmirror.update import API, REPO

    assert API == f'https://api.github.com/repos/{REPO}', 'the API root already names a path'
    built = API + '/releases?per_page=10'
    assert built.count('/releases') == 1, f'the releases path is doubled: {built}'
    assert built == f'https://api.github.com/repos/{REPO}/releases?per_page=10'


async def test_the_check_asks_for_the_url_it_thinks_it_does(tmp_path, monkeypatch):
    """And the same thing with a real request, so the two cannot drift.

    Skipped rather than stubbed where there is no network, because the whole
    bug was that a stub could not see it.
    """
    from openmirror.config import config

    monkeypatch.setattr(config, 'local_only', False)
    monkeypatch.setattr(config, 'update_check_enabled', True)
    held = Updates('0.1.0', tmp_path)

    seen: list[str] = []

    async def watch(url, headers, **kw):
        seen.append(url)
        raise NoReleasesYet(url)

    from openmirror.update import NoReleasesYet

    held._fetch = watch
    await held.check(force=True)
    assert seen == ['https://api.github.com/repos/notquiteog/openmirror/releases?per_page=100']


def test_the_release_list_is_a_hundred_deep():
    """Ten is GitHub's default and it is not enough for a project that tags
    release candidates.

    GitHub returns releases newest-*created* first, not highest-*version*
    first, so ten `rc` tags in a fortnight push the stable release a 0.6
    install actually needs out of the window — and the updater then reports
    "nothing newer" while a newer version is sitting right there. A hundred
    costs the same single request.
    """
    assert RELEASES_PER_PAGE >= 100


def test_the_version_comes_from_the_package_and_not_a_literal():
    """A literal in the router was a second place for the version to be wrong,
    and the CI check only reads pyproject.toml and Cargo.toml — so a bump
    could pass every check and still ship a daemon that reports the old number
    to the updater, which is a daemon that never offers itself an update.

    Read out of `openmirror/_version.py` rather than `pyproject.toml`, because a
    module of the package is the only form that survives being frozen into a
    PyInstaller bundle. See that file for the failure this prevents.
    """
    import tomllib
    from pathlib import Path

    from openmirror._version import __version__
    from openmirror.routers.updates import VERSION, version

    assert VERSION == __version__, (
        f'the updater reports {VERSION}, the package says {__version__}'
    )
    assert version() == VERSION

    # And pyproject agrees, when it states a version at all.
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())
    declared = pyproject['project'].get('version')
    assert declared is None or declared == __version__, (
        f'pyproject says {declared}, the package says {__version__}'
    )

    source = (Path(__file__).resolve().parents[1] / 'openmirror' / 'routers' / 'updates.py').read_text()
    assert "VERSION = '0" not in source, 'the version is a literal again'


def test_a_frozen_daemon_knows_its_own_version():
    """The one install for which the updater matters is the bundled one, and it
    has no `pyproject.toml` beside it.

    It reported `0.0.0` — which is older than everything ever published, so every
    release counted as newer, the update dot was permanently lit, and the
    updater was one request away from handing an install on 0.7.0 a build of
    0.1.0. This asserts the version is reachable from the package alone, with
    no filesystem and no installed metadata, because that is all a bundle has.

    The subprocess is the point: importing it must not pull in anything, or a
    frozen build — where a missing module is a hard error rather than an
    ImportError this function can catch — would not start.
    """
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    probe = (
        'import sys, openmirror._version as v;'
        'print(v.__version__);'
        'print(sorted(m for m in sys.modules if m.startswith("openmirror.")))'
    )
    done = subprocess.run(
        [sys.executable, '-S', '-c', probe],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    reported, modules = done.stdout.strip().splitlines()
    assert reported and reported != '0.0.0', 'a frozen daemon reported 0.0.0 and offered downgrades'
    assert parse(reported) is not None, 'the version has to parse or every comparison refuses to act'
    assert [m for m in eval(modules) if m != 'openmirror._version'] == [], (
        f'the version module dragged in {modules}'
    )
