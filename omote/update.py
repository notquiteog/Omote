"""Checking GitHub for a newer omote, and getting it safely.

This is the one place in the project that downloads something and runs it, so
the design is mostly about what could go wrong and what is done about each.

**Where the integrity comes from.** The release workflow already publishes a
`SHA256SUMS.txt` next to every installer, built from the artifacts it just
tested on four machines. That file is the anchor: an installer's hash is
checked against it before anything is offered to run, and a mismatch is a
refusal rather than a warning. It is not a signature — anybody who can publish
a release can publish a checksum — but it does catch the ordinary failures,
which are a truncated download, a proxy that substituted an error page, and a
mirror serving last week's build.

**Why not Tauri's own updater.** `tauri-plugin-updater` wants a minisign key
pair and refuses to install anything it cannot verify against one. This project
signs macOS builds ad-hoc until there is an Apple Developer ID, so the
official updater would refuse every build it published. The custom path here
uses the checksum the workflow already emits, and says so, rather than
pretending to verify more than it does.

**Why nothing happens by itself.** `check()` fetches; `apply()` is a separate
call that a person made; and the download is written to a staging file and
*staged*, not executed. Replacing a running binary is the OS's problem, not
this process's — on Windows a running executable cannot be overwritten at all,
and on macOS the bundle has to be swapped while the app is not holding it open.
The two steps are separate for the same reason `git commit` and
`POST /api/git/propose` are.

**What is left alone.** `local_only` is honoured: with it on, the check does
not happen, because it is a network call to GitHub and a person who asked for
no remote traffic did not ask for that. Tor is not used — the update host is a
CDN-hosted release asset, and routing a version check through a proxy to learn
whether there is a new version buys nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import platform
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from omote.config import config

log = logging.getLogger(__name__)

# The project's own repository. Not configurable, and deliberately: a setting
# that points the updater somewhere else is a setting that ships code from
# somewhere else, and there is no version of that which is a feature rather
# than a vulnerability.
REPO = 'notquiteog/Omote'
# The repository's API root, *not* the releases endpoint. Appending `/releases`
# here as well as at the call site produced
# `.../repos/notquiteog/Omote/releases/releases`, which GitHub answers
# with a 404 — and a 404 from this call is reported as "no releases published
# yet", which is exactly what it said for six months of a project with no
# releases. A bug that reports the truth by accident.
API = f'https://api.github.com/repos/{REPO}'

# A release body is written for a browser. Truncated for a notification and
# for the interface, because the alternative is a dialog nobody can close.
BODY_LIMIT = 600
# Big enough for a macOS disk image, which is the largest thing published.
MAX_ASSET = 400 * 1024 * 1024
# GitHub answers an unauthenticated request for 60 releases an hour per
# address, which is shared by a whole office. An hour between checks costs
# nothing and a 403 does not.
CHECK_INTERVAL = 3600
# How many releases to ask for. This is 10 upstream's default and was 10 here,
# which is fewer than a burst of release candidates: GitHub returns releases
# newest-*created* first, not highest-*version* first, so ten rc tags in a
# fortnight push the stable release that a 0.6 install needs out of the window
# and the updater reports "nothing newer" while a newer version is sitting
# there. One hundred covers that and costs one request either way.
RELEASES_PER_PAGE = 100


def github_token() -> str:
    """A GitHub token from the environment, or ''.

    Read here rather than imported from `agent/git.py` so that the updater has
    no dependency on the agent package — it runs at start-up, before anything
    else, and a version check should not be able to fail for a reason that has
    nothing to do with versioning.

    `GH_TOKEN` as well as `GITHUB_TOKEN` because `gh auth login` exports the
    first and somebody who has set the CLI up is very likely to have it without
    reasons to also export the second. It moves this install from 60 requests
    an hour to 5000, which matters because the rate limit is per address and an
    address is often a whole office.

    The value is never logged, never put in an exception and never returned to
    a caller that would show it.
    """
    for name in ('GITHUB_TOKEN', 'GH_TOKEN'):
        token = os.environ.get(name, '').strip()
        if token:
            return token
    return ''


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Version:
    """A version, ordered.

    Pre-release ordering is the part that matters and the part everybody gets
    wrong: `0.2.0-rc.1` is *older* than `0.2.0`, so an install that shipped a
    release candidate is offered the final one, and an install on the final is
    not offered the candidate. The reverse would have every released build
    asking to go back.
    """

    major: int = 0
    minor: int = 0
    patch: int = 0
    # `('rc', 1)` — a dotted prerelease with a numeric tail, when it has one.
    prerelease: tuple[Any, ...] = ()
    original: str = ''

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    def __str__(self) -> str:
        base = f'{self.major}.{self.minor}.{self.patch}'
        if not self.prerelease:
            return base
        tail = '.'.join(str(part) for part in self.prerelease)
        return f'{base}-{tail}'


_SEMVER = re.compile(
    r'^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?'
    r'(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?\s*$'
)


def parse(text: str) -> Version | None:
    """A version out of a tag or a version field, or None if it is not one.

    Tolerant about a leading `v` and about missing components, because tags
    get written by hand and `v1` should still mean 1.0.0. Returns None rather
    than raising: an unparseable release name is a release to skip, not a
    daemon to refuse to start over.
    """
    match = _SEMVER.match(text or '')
    if not match:
        return None
    tail = match.group(4)
    prerelease: tuple[Any, ...] = ()
    if tail:
        # Each dot-separated part compares as a number when it is one, because
        # `rc.2` has to sort above `rc.10` if it is `rc.2`.
        parts: list[Any] = []
        for part in tail.split('.'):
            parts.append(int(part) if part.isdigit() else part)
        prerelease = tuple(parts)
    return Version(
        major=int(match.group(1)),
        minor=int(match.group(2) or 0),
        patch=int(match.group(3) or 0),
        prerelease=prerelease,
        original=(text or '').strip(),
    )


def _sort_key(version: Version) -> tuple[Any, ...]:
    """A total order. A missing prerelease outranks a present one.

    The trick is that numbers sort before non-numbers, which Python's tuple
    comparison will not do for us — comparing `1` to `'alpha'` is a TypeError,
    not an ordering. Each part becomes `(0, number, '')` or `(1, 0, text)`.
    """
    return (
        version.major,
        version.minor,
        version.patch,
        1 if not version.prerelease else 0,
        tuple((0, part, '') if isinstance(part, int) else (1, 0, part) for part in version.prerelease),
    )


def is_newer(candidate: str, current: str, *, allow_prerelease: bool = False) -> bool:
    """Whether `candidate` is a version this install should move to.

    `allow_prerelease` is the "show me what is coming" switch, and it is off
    by default: an install that silently went to a release candidate would be
    running code the author has not promised anybody.

    The one exception is an install that is *already* on a prerelease. It got
    there by asking — from a downloaded build, or from the same switch — and
    holding it on `rc.1` while `rc.2` exists is not a safety property, it is a
    person stuck. So a prerelease is offered to somebody already running one.
    """
    new = parse(candidate)
    old = parse(current)
    if new is None or old is None:
        return False
    if new.is_prerelease and not allow_prerelease and not old.is_prerelease:
        return False
    return _sort_key(new) > _sort_key(old)


# ---------------------------------------------------------------------------
# What GitHub says
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Asset:
    name: str
    url: str
    size: int = 0
    content_type: str = ''

    @property
    def is_checksums(self) -> bool:
        return self.name.upper().startswith('SHA256SUMS')


@dataclass(slots=True)
class Release:
    tag: str
    name: str = ''
    body: str = ''
    html_url: str = ''
    published: str = ''
    prerelease: bool = False
    draft: bool = False
    assets: list[Asset] = field(default_factory=list)

    @property
    def version(self) -> Version | None:
        return parse(self.tag)

    def checksums(self) -> dict[str, str]:
        """The published `SHA256SUMS.txt`, if it has been fetched already.

        This used to be a stub that always returned `{}` and replaced its own
        `False` "not fetched yet" sentinel with that `{}` on the first call —
        so the cache was permanently poisoned and the method was, in the end,
        doing nothing. `fetch_checksums` did the work instead, and still does;
        what this is now is a read of that work, with a sentinel that cannot
        collide with the answer.
        """
        return self._sums if isinstance(self._sums, dict) else {}

    _sums: dict[str, str] | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        found = self.version
        chosen = self.pick()
        return {
            'tag': self.tag,
            'version': str(found) if found else self.tag,
            'name': self.name,
            'notes': (self.body or '')[:BODY_LIMIT],
            'notes_truncated': len(self.body or '') > BODY_LIMIT,
            'url': self.html_url,
            'published': self.published,
            'prerelease': self.prerelease,
            'asset': chosen.name if chosen else None,
        }

    def pick(self) -> Asset | None:
        return pick_asset(self.assets, kind=installation_kind())


def _release_from(raw: dict[str, Any]) -> Release:
    return Release(
        tag=str(raw.get('tag_name') or ''),
        name=str(raw.get('name') or ''),
        body=str(raw.get('body') or ''),
        html_url=str(raw.get('html_url') or ''),
        published=str(raw.get('published_at') or ''),
        prerelease=bool(raw.get('prerelease')),
        # Read even though it is not used here. It is invisible on an
        # unauthenticated request — GitHub hides drafts from anonymous callers —
        # and the moment somebody exports `GITHUB_TOKEN`, which the rate limit
        # above makes likely, drafts start arriving in this list. Filtering them
        # in `_newest` is a one-line guard that is much cheaper to add now than
        # to notice later.
        draft=bool(raw.get('draft')),
        assets=[
            Asset(
                # `Path(name).name` because this string is about to become a
                # path. GitHub sanitises separators in asset names today, so
                # this is defence in depth rather than a live hole — but it is
                # the one place a name from the network becomes a filesystem
                # path, and the desktop app that later runs it does not check
                # containment either.
                name=Path(str(a.get('name') or '')).name,
                url=str(a.get('browser_download_url') or ''),
                size=int(a.get('size') or 0),
                content_type=str(a.get('content_type') or ''),
            )
            for a in (raw.get('assets') or [])
            if a.get('name')
        ],
    )


# ---------------------------------------------------------------------------
# Which file, on this machine
# ---------------------------------------------------------------------------


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    """`linux-x64`, `macos-arm64`, `windows-x64` — the shapes Tauri names."""
    system = (system or platform.system()).strip().lower()
    machine = (machine or platform.machine()).strip().lower()

    arch = {
        'x86_64': 'x64',
        'amd64': 'x64',
        'x64': 'x64',
        'arm64': 'arm64',
        'aarch64': 'arm64',
        'x86': 'x86',
        'i386': 'x86',
        'i686': 'x86',
    }.get(machine, machine or 'x64')

    if system in ('darwin', 'macos', 'mac'):
        return f'macos-{arch}'
    if system in ('windows', 'win32', 'nt'):
        return f'windows-{arch}'
    return f'linux-{arch}'


# Extensions, in the order they are preferred. The order is the decision: a
# single file that runs without a package manager is preferred over one that
# needs root, and a NSIS installer over an MSI because it does not want an
# administrator to uninstall.
PREFERRED: dict[str, tuple[str, ...]] = {
    'linux-x64': ('.appimage', '.deb', '.rpm'),
    'linux-arm64': ('.appimage', '.deb', '.rpm'),
    'windows-x64': ('-setup.exe', '.msi'),
    'windows-x86': ('-setup.exe', '.msi'),
    'macos-arm64': ('.dmg',),
    'macos-x64': ('.dmg',),
    'macos-x86': ('.dmg',),
}


def installation_kind() -> str:
    explicit = os.environ.get('OMOTE_INSTALL_KIND', '')
    if explicit in ('deb', 'rpm', 'appimage'):
        return explicit
    if os.environ.get('APPIMAGE'):
        return 'appimage'
    if platform.system() == 'Linux':
        try:
            distro = platform.freedesktop_os_release()
            family = f'{distro.get("ID", "")} {distro.get("ID_LIKE", "")}'.split()
            if any(name in family for name in ('debian', 'ubuntu', 'pop')):
                return 'deb'
            if any(name in family for name in ('fedora', 'rhel', 'suse', 'opensuse', 'centos')):
                return 'rpm'
        except OSError:
            pass
    return ''


def pick_asset(assets: list[Asset], *, key: str | None = None, kind: str = '') -> Asset | None:
    """The installer for this machine, or None.

    Matching is by extension and architecture rather than by filename, because
    Tauri names artifacts after the bundle type and the target triple and
    those change with the bundler version. An AppImage is named
    `omote_0.1.0_amd64.AppImage` on one release and
    `omote_amd64.AppImage` on the next.
    """
    wanted = PREFERRED.get(key or platform_key())
    if (key or platform_key()).startswith('linux-') and kind in ('deb', 'rpm', 'appimage'):
        wanted = ('.' + kind,)
    if not wanted:
        return None
    for suffix in wanted:
        for asset in assets:
            name = asset.name.lower()
            if name.endswith(suffix) and _arch_matches(name, key or platform_key()):
                return asset
    return None


# How each architecture is spelled in an artifact name. Tauri and the Linux
# packagers disagree about every one of these, which is why it is a set.
ARCH_TOKENS: dict[str, tuple[str, ...]] = {
    'x64': ('x64', 'x86_64', 'amd64'),
    'arm64': ('arm64', 'aarch64'),
    'x86': ('x86', 'i686', 'i386'),
}


def _arch_matches(name: str, key: str) -> bool:
    """Whether an artifact's name is for this machine's architecture.

    The subtle part is the default. A release that publishes one installer per
    platform and puts no architecture in the name is publishing it for that
    platform's only real target, and refusing to guess would mean never
    offering an update on the most common layout.

    But the default only applies when the name says *nothing at all*. The
    first version of this checked whether the target's own token appeared,
    concluded `amd64` said nothing about an arm64 Mac, and handed an Intel disk
    image to an Apple Silicon machine — because `amd64` is an architecture the
    function did not know to look for. So it looks for all of them, and only
    defaults when it found none.
    """
    wanted = key.rsplit('-', 1)[-1]
    said: str | None = None
    for arch, tokens in ARCH_TOKENS.items():
        if any(token in name for token in tokens):
            said = arch
            break
    return said is None or said == wanted


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class NoReleasesYet(Exception):
    """The repository has published nothing. A state, not a fault.

    Separate from the general failure because it reads as one: a project with
    no tags yet is doing exactly what it should, and the interface should say
    "nothing to update to" rather than report an error in a red box.
    """


@dataclass(slots=True)
class UpdateState:
    """What is known, and what is happening.

    Kept in one place with a lock, because the interesting failures are
    concurrency failures: two checks at once against a sixty-an-hour limit, or
    a download and a shutdown at the same moment.
    """

    current: str
    latest: Release | None = None
    checked_at: str = ''
    error: str = ''
    downloading: bool = False
    progress: int = 0
    downloaded: int = 0
    staged: str = ''
    installation: dict[str, Any] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def public(self) -> dict[str, Any]:
        latest = self.latest
        available = bool(latest and is_newer(latest.tag, self.current, allow_prerelease=latest.prerelease))
        return {
            'current': self.current,
            'available': available,
            'latest': latest.public() if latest else None,
            'checked_at': self.checked_at,
            'error': self.error,
            'downloading': self.downloading,
            'progress': self.progress,
            'downloaded': self.downloaded,
            'staged': self.staged,
            'installation': self.installation,
            'enabled': config.update_check_enabled and not config.local_only,
            'platform': platform_key(),
            'managed': config.is_frozen,
        }


class Updates:
    def __init__(self, current: str, staging: Path) -> None:
        self.state = UpdateState(current=current)
        self.staging = Path(staging)
        self.refresh_installation()

    def refresh_installation(self):
        import json

        try:
            path = self.staging / 'install-result.json'
            if path.stat().st_size <= 32768:
                value = json.loads(path.read_text())
                if isinstance(value, dict) and value.get('status') in ('installing', 'complete', 'error'):
                    self.state.installation = value
        except (OSError, ValueError):
            pass

    # -- checking ---------------------------------------------------------

    async def check(self, *, force: bool = False, allow_prerelease: bool = False) -> dict[str, Any]:
        """Ask GitHub what the newest release is. Cheap, and safe to repeat.

        Refuses when `local_only` is on and when the person has switched the
        feature off, rather than quietly doing it anyway: a check is a network
        request to a third party, and somebody who asked for no remote traffic
        did not ask for that.
        """
        async with self.state._lock:
            if not config.update_check_enabled:
                self.state.error = 'update checks are switched off on this install'
                return self.state.public()
            if config.local_only:
                self.state.error = 'this install is set to local only, so it does not ask GitHub'
                return self.state.public()
            recent = self.state.checked_at and not force
            if recent and _age(self.state.checked_at) < CHECK_INTERVAL:
                return self.state.public()

            headers = {
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
                'User-Agent': f'omote/{self.state.current}',
            }
            token = github_token()
            if token:
                headers['Authorization'] = f'Bearer {token}'
            try:
                releases = await self._fetch(f'{API}/releases?per_page={RELEASES_PER_PAGE}', headers)
            except NoReleasesYet:
                # Checked, and there is nothing to check. Not an error, and it
                # still counts as a check so the hour does not restart it.
                self.state.checked_at = _now()
                self.state.error = ''
                self.state.latest = None
                return self.state.public()
            except (aiohttp.ClientError, TimeoutError) as exc:
                # `TimeoutError` is in this tuple because on 3.11+
                # `asyncio.TimeoutError is TimeoutError` and `TimeoutError` is
                # an `OSError`, *not* an `aiohttp.ClientError` — so a slow or
                # unreachable GitHub used to escape as an unhandled 500 rather
                # than the message written for it here. The one caller that
                # already had it right was `fetch_checksums`, which is what made
                # this look like it worked.
                self.state.error = f'could not reach GitHub ({exc or "it timed out"})'
                return self.state.public()
            except ValueError as exc:
                self.state.error = f'GitHub said something unexpected ({exc})'
                return self.state.public()

            self.state.checked_at = _now()
            self.state.error = ''
            self.state.latest = _newest(releases, self.state.current, allow_prerelease)
            return self.state.public()

    async def _fetch(self, url: str, headers: dict[str, str], *, seconds: int = 20) -> Any:
        # transport-exempt: not a model server, and not a provider. This asks
        # GitHub about a release and fetches the artifact attached to it. The
        # Tor toggle is per-connection on model traffic, and a version check
        # is not worth a proxy; the privacy switch that *is* honoured is
        # `local_only`, checked above. See omote/net/tor.py.
        async with aiohttp.ClientSession() as client:
            async with asyncio.timeout(seconds):
                async with client.get(url, headers=headers) as response:
                    if response.status == 403:
                        # Almost always the unauthenticated rate limit, and
                        # saying so is more useful than "forbidden" — but not
                        # always. GitHub sets `x-ratelimit-remaining: 0` on the
                        # rate limit and leaves it alone on a genuine refusal,
                        # so the header is what tells the two apart. Telling them
                        # apart matters: "nothing is wrong with this install" is
                        # reassuring, and it is the wrong thing to say to
                        # somebody whose egress proxy just refused the request.
                        remaining = response.headers.get('x-ratelimit-remaining', '')
                        if remaining.strip() == '0':
                            raise ValueError(
                                'GitHub rate limit reached (requests left: 0). It resets hourly; '
                                'nothing is wrong with this install. Setting GITHUB_TOKEN raises '
                                'the limit from 60 an hour to 5000.'
                            )
                        raise ValueError(
                            f'GitHub refused the request (HTTP 403){" — a token is set but it is not accepted" if github_token() else ""}. '
                            'Nothing is wrong with this install; the release list is simply not available.'
                        )
                    if response.status == 404:
                        raise NoReleasesYet(f'{REPO} has published no releases yet')
                    response.raise_for_status()
                    return await response.json(content_type=None)

    # -- downloading ------------------------------------------------------

    async def download(self, release: Release, asset: Asset, *, sums: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch the installer to a staging file and check it.

        The checksum is verified *before* the file is moved into place, so a
        mismatch never leaves something runnable-looking on disk. A missing
        checksum is allowed and said out loud: a release published without
        one is a packaging mistake, and refusing to update from it is a
        support ticket rather than a safety measure.

        The lock is the same one `check` takes. Two `apply` calls at once wrote
        to the same `.part` path and interleaved their progress counters, which
        is the exact failure the lock's own docstring names and which only
        `check` was actually using it for.
        """
        if asset.size and asset.size > MAX_ASSET:
            raise UpdateRefused(f'that release is {asset.size // (1024 * 1024)}MB, which is not an installer')

        self.staging.mkdir(parents=True, exist_ok=True)
        # The name has already been reduced to its last segment by
        # `_release_from`; this is the second check on the same door, and it is
        # the one that matters because this is the line that joins it to a path.
        target = (self.staging / Path(asset.name).name).resolve()
        if target.parent != self.staging.resolve():
            raise UpdateRefused(f'{asset.name} is not a filename, so it will not be downloaded')
        partial = target.with_suffix(target.suffix + '.part')

        headers = {'Accept': 'application/octet-stream', 'User-Agent': f'omote/{self.state.current}'}
        token = github_token()
        if token:
            headers['Authorization'] = f'Bearer {token}'

        async with self.state._lock:
            self.state.downloading = True
            self.state.progress = 0
            self.state.downloaded = 0
            self.state.staged = ''
            # Anything left by an earlier attempt — an interrupted run, or the
            # orphan a timeout used to leave behind — is not wanted and must not
            # be mistaken for a finished download.
            partial.unlink(missing_ok=True)
            try:
                # transport-exempt: the release artifact named by GitHub, checked
                # against the checksum published beside it. Same reasoning as
                # `_fetch` above.
                async with aiohttp.ClientSession() as client:
                    async with client.get(asset.url, headers=headers, timeout=None) as response:
                        if response.status >= 400:
                            raise UpdateRefused(f'GitHub returned HTTP {response.status} for {asset.name}')
                        total = int(response.headers.get('content-length') or asset.size or 0)
                        # What the server says it is sending. A truncated
                        # download from a release with no `SHA256SUMS.txt` was
                        # previously staged as a success, because the only
                        # integrity check was a checksum that file may not
                        # exist to provide.
                        expected_bytes = total or asset.size
                        if expected_bytes > MAX_ASSET:
                            raise UpdateRefused(
                                f'{asset.name} is {expected_bytes // (1024 * 1024)}MB, which is not an installer'
                            )
                        digest = hashlib.sha256()
                        with partial.open('wb') as handle:
                            async for chunk in response.content.iter_chunked(256 * 1024):
                                # Also capped while streaming: `asset.size` is
                                # GitHub's metadata, not a promise about what is
                                # on the other end of the connection, and a
                                # response with no `content-length` was written
                                # to disk with no limit at all.
                                self.state.downloaded += len(chunk)
                                if self.state.downloaded > MAX_ASSET:
                                    raise UpdateRefused(
                                        f'{asset.name} is larger than any installer this project publishes; stopped'
                                    )
                                handle.write(chunk)
                                digest.update(chunk)
                                if expected_bytes:
                                    self.state.progress = min(99, int(self.state.downloaded * 100 / expected_bytes))
                if expected_bytes and self.state.downloaded != expected_bytes:
                    raise UpdateRefused(
                        f'{asset.name} arrived short — {self.state.downloaded} of {expected_bytes} bytes. '
                        'It has been deleted and not installed; try again.'
                    )
                self.state.progress = 100
            except (aiohttp.ClientError, TimeoutError) as exc:
                # `TimeoutError` belongs here for the same reason it belongs in
                # `check`, and the consequence of it missing was worse than a
                # 500: the `unlink` only ran in the `ClientError` arm, so every
                # interrupted download left a `.part` file behind that nothing
                # ever swept up.
                partial.unlink(missing_ok=True)
                raise UpdateRefused(f'the download did not finish ({exc or "it timed out"})') from exc
            except UpdateRefused:
                partial.unlink(missing_ok=True)
                raise
            finally:
                self.state.downloading = False

            expected = (sums or {}).get(asset.name, '')
            actual = digest.hexdigest()
            if expected and expected.lower() != actual:
                partial.unlink(missing_ok=True)
                log.error('%s: sha256 %s, expected %s', asset.name, actual, expected)
                raise UpdateRefused(
                    f'{asset.name} does not match the checksum published with it. It has been deleted and '
                    f'not installed. If this persists the release was cut badly — check the release page.'
                )
            partial.replace(target)
            _make_runnable(target)
            if expected:
                import json

                proof = target.with_name(target.name + '.verified.json')
                proof.write_text(json.dumps({'name': target.name, 'sha256': actual, 'version': str(release.version), 'bytes': self.state.downloaded}))
            self.state.staged = str(target)
            self.state.progress = 100
            return {
                'path': str(target),
                'bytes': self.state.downloaded,
                'sha256': actual,
                'verified': bool(expected),
                'expected': expected,
            }

    async def fetch_checksums(self, release: Release) -> dict[str, str]:
        """The published `SHA256SUMS.txt`, parsed.

        A failure here is not fatal and returns nothing: the caller then says
        out loud that the download was not verified, which is the honest
        outcome, rather than refusing an update because a convenience file was
        missing.
        """
        if release.checksums():
            return release.checksums()
        asset = next((a for a in release.assets if a.is_checksums), None)
        if asset is None:
            return {}
        headers = {'User-Agent': f'omote/{self.state.current}'}
        try:
            # transport-exempt: the checksum file published beside the asset.
            async with aiohttp.ClientSession() as client:
                async with client.get(asset.url, headers=headers, timeout=20) as response:
                    response.raise_for_status()
                    text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning('could not fetch %s: %s', asset.name, exc)
            return {}
        out: dict[str, str] = {}
        for line in text.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and len(parts[0]) == 64:
                # `sha256sum` writes `hash␠␠name` in text mode and `hash␠*name`
                # in binary mode; the star marks a filename containing a
                # backslash or newline. So the separator is stripped and *then*
                # one leading star is removed — not `lstrip('*')`, which eats
                # every leading star and would silently mangle a filename that
                # genuinely starts with one.
                name = parts[1].strip()
                if name.startswith('*'):
                    name = name[1:]
                out[name] = parts[0].lower()
        release._sums = out
        return out


def _make_runnable(path: Path) -> None:
    """Give a staged installer its execute bit, on the platforms that have one.

    This is not tidiness. The desktop app checks the mode before running
    anything — `is_executable` in `desktop/src-tauri/src/main.rs` refuses a
    staged file with no `0o111` bits — and a file created with `open('wb')`
    gets `0o666 & ~umask`, which is `0o644` on an ordinary Linux box. So every
    AppImage and every `.deb` the updater downloaded was refused by the only
    thing that could run it, with "is not marked runnable", on every Unix
    platform and for every release. Windows happened to work, because there the
    same function returns `true` unconditionally.

    The bit is set *after* the checksum is verified and the file is renamed into
    place, so there is never a moment at which an unverified download is
    executable.

    Errors are swallowed deliberately: on Windows there is no execute bit, the
    call is a no-op, and a filesystem that refuses the mode change is not a
    reason to throw away a verified download.
    """
    try:
        os.chmod(path, 0o755)
    except OSError:  # pragma: no cover - platform and filesystem dependent
        log.debug('could not mark %s runnable', path, exc_info=True)


class UpdateRefused(RuntimeError):
    """An update was declined, and the reason is one a person can act on."""


def _newest(releases: list[Any], current: str, allow_prerelease: bool) -> Release | None:
    """The newest release worth offering.

    A prerelease is skipped unless asked for, which is what the comment
    elsewhere in this project promises: an install sitting on `0.1.0` is not
    shown `0.2.0-rc.1`, which is a thing the author has not promised anybody.
    (This docstring used to add "*or* it is the only thing newer than what is
    installed", which the code below never did and a test contradicted — the
    safe promise is the one worth keeping.)

    Drafts are always skipped. GitHub hides them from unauthenticated callers,
    so this guard does nothing until somebody sets `GITHUB_TOKEN` — and at that
    moment, without it, the updater would cheerfully offer an unfinished
    release.
    """
    found = [
        release
        for release in (_release_from(raw) for raw in releases if isinstance(raw, dict))
        if release.version and not release.draft
    ]
    found.sort(key=lambda r: _sort_key(r.version), reverse=True)  # type: ignore[arg-type]
    for release in found:
        if release.prerelease and not allow_prerelease:
            continue
        if is_newer(release.tag, current, allow_prerelease=allow_prerelease):
            return release
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def _age(stamp: str) -> float:
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return 1e9
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (datetime.now(UTC) - then).total_seconds()


def sha256_file(path: Path) -> str:
    """The hash of a file on disk, for verifying a staged download twice."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(256 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


# `sys` is imported for the frozen check that decides whether an update is
# even applicable: a `pip install` can be replaced in place, and a PyInstaller
# bundle cannot.
def is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False)) or 'PyInstaller' in sys.modules


__all__ = [
    'API',
    'BODY_LIMIT',
    'Asset',
    'NoReleasesYet',
    'Release',
    'UpdateRefused',
    'UpdateState',
    'Updates',
    'Version',
    'github_token',
    'is_frozen',
    'is_newer',
    'parse',
    'pick_asset',
    'platform_key',
    'sha256_file',
]
