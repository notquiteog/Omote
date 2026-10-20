"""What machine this is, and how you change something on it.

"Install Steam" is not one instruction. It is `apt install steam-installer` on
Debian, `flatpak install com.valvesoftware.Steam` where there is no native
package, already-done on a Steam Deck, `winget install Valve.Steam` on
Windows, and a cask on macOS — and on an immutable system the first of those
is not merely wrong, it fails in a way that invites an agent to start
disabling the read-only rootfs, which is how someone's console gets bricked.

So this module answers three questions before anything acts:

**What is this?** OS, distribution, desktop, session type, whether the root
filesystem is writable, whether it is a Steam Deck. Read rather than guessed:
`/etc/os-release` and the XDG environment are the answers the system gives
about itself.

**What can install things here?** The managers that are actually present, in a
sensible order of preference, each knowing whether it needs elevation and
whether it installs for the system or for one user.

**How would this machine let us elevate?** Root already, passwordless sudo, a
polkit agent, a password prompt — or nothing, which is a real answer and one
the agent needs so it can say so instead of hanging on a `sudo` that is
waiting for a password nobody will type.

None of it decides anything. It reports, and the tools and the model decide,
because the right route for "install Epic Games Store" on Linux is not an
Epic package — there is no Linux client — it is Heroic, and that is a judgment
rather than a lookup.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Manager:
    """One way of installing software on this machine."""

    name: str
    # 'system' changes the machine for everyone and needs elevation.
    # 'user' installs into the calling user's own home and does not.
    scope: str
    search: tuple[str, ...]
    install: tuple[str, ...]
    #: What to append after the package name, if anything (`-y`, `--noconfirm`).
    assume_yes: tuple[str, ...] = ()
    note: str = ''
    #: Lists what this manager already has installed, one entry per line, so
    #: "is it here already, and how?" is answered before installing it twice.
    listing: tuple[str, ...] = ()

    @property
    def needs_elevation(self) -> bool:
        return self.scope == 'system'


# Ordered by preference within each family. A native package is preferred to a
# sandboxed one where both exist — it is smaller, it updates with the system,
# and it is what the distribution tests. Flatpak is next because it works
# everywhere including immutable systems, and snap last because several
# distributions ship it disabled.
LINUX_MANAGERS: tuple[Manager, ...] = (
    Manager('apt', 'system', ('apt-cache', 'search'), ('apt-get', 'install'), ('-y',),
            listing=('dpkg-query', '-W', '-f', '${Package}:${Architecture} ${Version}\n')),
    Manager('dnf', 'system', ('dnf', 'search'), ('dnf', 'install'), ('-y',),
            listing=('rpm', '-qa')),
    Manager('pacman', 'system', ('pacman', '-Ss'), ('pacman', '-S'), ('--noconfirm',),
            listing=('pacman', '-Q')),
    Manager('zypper', 'system', ('zypper', 'search'), ('zypper', 'install'), ('-y',),
            listing=('rpm', '-qa')),
    Manager('apk', 'system', ('apk', 'search'), ('apk', 'add'), (),
            listing=('apk', 'info', '-v')),
    Manager(
        'flatpak', 'user', ('flatpak', 'search'), ('flatpak', 'install', '--user'), ('-y',),
        note='Sandboxed and per-user, so it needs no elevation and works on an '
             'immutable system where the package managers above do not.',
        listing=('flatpak', 'list', '--app', '--columns=application,version,installation'),
    ),
    Manager('snap', 'system', ('snap', 'find'), ('snap', 'install'), (), listing=('snap', 'list')),
    Manager('nix-env', 'user', ('nix-env', '-qa'), ('nix-env', '-i'), (), listing=('nix-env', '-q')),
)

WINDOWS_MANAGERS: tuple[Manager, ...] = (
    Manager(
        'winget', 'system', ('winget', 'search'), ('winget', 'install'),
        ('--accept-package-agreements', '--accept-source-agreements'),
        note=(
            "Ships with Windows 11 and recent 10. Elevates by prompting through UAC, "
            "which appears on the person's own screen rather than here."
        ),
    ),
    Manager('choco', 'system', ('choco', 'search'), ('choco', 'install'), ('-y',)),
    Manager('scoop', 'user', ('scoop', 'search'), ('scoop', 'install'), ()),
)

MAC_MANAGERS: tuple[Manager, ...] = (
    Manager('brew', 'user', ('brew', 'search'), ('brew', 'install'), (),
            listing=('brew', 'list', '--versions')),
)


@dataclass(frozen=True, slots=True)
class Platform:
    system: str
    distro: str = ''
    like: tuple[str, ...] = ()
    version: str = ''
    desktop: str = ''
    session: str = ''
    # A root filesystem that cannot simply be written to: SteamOS, Silverblue,
    # anything ostree. The distinction that matters most for installing, and
    # the one an agent will otherwise fight rather than route around.
    immutable: bool = False
    steam_deck: bool = False
    managers: tuple[Manager, ...] = ()
    elevation: str = 'none'
    elevation_note: str = ''
    #: The machine's own architecture, in its package manager's spelling where
    #: it has one (`amd64` rather than `x86_64` on Debian).
    arch: str = ''
    #: Further architectures the package manager installs for: Debian
    #: multiarch, almost always `i386` for Steam and Wine. A library installed
    #: for the native one alone does nothing for programs built for these.
    foreign_arches: tuple[str, ...] = ()

    @property
    def family(self) -> tuple[str, ...]:
        """Every name this distribution answers to, for matching a recipe."""
        return tuple(n for n in (self.distro, *self.like) if n)

    def manager(self, name: str) -> Manager | None:
        return next((m for m in self.managers if m.name == name), None)

    def to_json(self) -> dict[str, object]:
        return {
            'system': self.system,
            'distro': self.distro,
            'like': list(self.like),
            'version': self.version,
            'desktop': self.desktop,
            'session': self.session,
            'immutable': self.immutable,
            'steam_deck': self.steam_deck,
            'managers': [
                {'name': m.name, 'scope': m.scope, 'needs_elevation': m.needs_elevation,
                 'note': m.note}
                for m in self.managers
            ],
            'elevation': self.elevation,
            'elevation_note': self.elevation_note,
            'arch': self.arch,
            'foreign_arches': list(self.foreign_arches),
        }


# ---------------------------------------------------------------------------
# Reading the machine
# ---------------------------------------------------------------------------


def _os_release(path: Path | None = None) -> dict[str, str]:
    """`/etc/os-release`, which is the answer the system gives about itself.

    Parsed rather than shelled out to, and tolerant of a missing file: macOS
    and Windows have none, and neither does a container built from scratch.
    """
    target = path or Path('/etc/os-release')
    try:
        text = target.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return {}

    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _is_immutable(release: dict[str, str]) -> bool:
    """Whether the root filesystem is one you are meant to leave alone.

    Three signals, because the systems that do this do not agree on how to say
    so: SteamOS ships `steamos-readonly`, the ostree family ships
    `rpm-ostree`, and `/` being mounted read-only is the general case. Any one
    of them means "do not reach for the system package manager", and being
    wrong in that direction only costs a flatpak.
    """
    if shutil.which('steamos-readonly') or shutil.which('rpm-ostree'):
        return True
    if release.get('VARIANT_ID') in ('silverblue', 'kinoite', 'sericea', 'steamdeck'):
        return True
    try:
        with Path('/proc/mounts').open(encoding='utf-8') as handle:
            for line in handle:
                parts = line.split()
                if len(parts) > 3 and parts[1] == '/':
                    return 'ro' in parts[3].split(',')
    except OSError:
        pass
    return False


def _elevation() -> tuple[str, str]:
    """How this machine would let a command run as root, and what that costs.

    Ordered by how little it asks of the person. The important one is the last
    branch: a `sudo` that will prompt for a password is not a route an agent
    can take on its own, because the prompt goes to a terminal nobody is
    reading and the command hangs there until it times out. Saying so lets the
    agent explain the problem instead of becoming it.
    """
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        return 'root', 'already running as root'

    if shutil.which('sudo'):
        try:
            # -n is "never prompt": it succeeds only where a password is not
            # needed, which is exactly the question being asked.
            done = subprocess.run(
                ['sudo', '-n', 'true'], capture_output=True, timeout=10, check=False
            )
            if done.returncode == 0:
                return 'sudo-nopasswd', 'sudo works without a password'
        except (OSError, subprocess.SubprocessError):
            pass

    if sys.platform == 'darwin' and Path('/usr/bin/osascript').exists():
        return 'macos-auth', 'macOS can request administrator access through its native authentication dialog. Passwords stay with the OS.'

    if shutil.which('pkexec'):
        return 'pkexec', (
            'pkexec is available. It asks through the desktop, on the screen the person is '
            'actually looking at, so the password never passes through here — but it needs '
            'a polkit agent running in their session, and there is no way to know from here '
            'whether one is.'
        )

    if shutil.which('sudo') or shutil.which('doas'):
        return 'sudo-password', (
            'sudo needs a password. Nothing here can supply one to a terminal the person is '
            'not looking at, so a system-wide install will hang rather than fail. Prefer a '
            'per-user manager such as flatpak, or ask them to run the command themselves.'
        )

    return 'none', 'no way to run anything as root was found'


def _architectures() -> tuple[str, tuple[str, ...]]:
    """The native architecture, and any foreign ones the package manager serves.

    Asked of dpkg where it exists, because multiarch is a dpkg setting and its
    names are the ones `apt install lib:i386` takes. It matters for libraries
    more than programs: a preload library such as libtorsocks installed for
    amd64 alone does nothing for a 32-bit program, and fails open rather than
    loudly — the traffic goes direct.
    """
    native = platform.machine()
    if not shutil.which('dpkg'):
        return native, ()
    try:
        native = subprocess.run(
            ['dpkg', '--print-architecture'],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip() or native
        foreign = subprocess.run(
            ['dpkg', '--print-foreign-architectures'],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return native, ()
    return native, tuple(foreign)


# Detection shells out — `sudo -n true` is the only honest way to ask whether
# sudo will prompt — and a session is built every time somebody opens one. None
# of these facts change while the process runs, so it is worked out once.
_CACHED: Platform | None = None


def detect(fresh: bool = False) -> Platform:
    """Everything about this machine that changes what an instruction means.

    Cached, because this is called whenever a session is created and one of
    its probes runs a subprocess. `fresh` is for tests and for the case where
    somebody has just installed a package manager and wants it noticed.
    """
    global _CACHED
    if _CACHED is not None and not fresh:
        return _CACHED
    _CACHED = _detect()
    return _CACHED


def _detect() -> Platform:
    system = 'windows' if os.name == 'nt' else platform.system().lower()

    if system == 'windows':
        managers = tuple(m for m in WINDOWS_MANAGERS if shutil.which(m.name))
        return Platform(
            system='windows',
            version=platform.version(),
            desktop='windows',
            session='windows',
            managers=managers,
            elevation='uac',
            elevation_note=(
                "Windows elevates by prompting through UAC on the person's own screen. "
                'winget asks for it when a package needs it.'
            ),
        )

    if system == 'darwin':
        how, why = _elevation()
        return Platform(
            system='darwin',
            version=platform.mac_ver()[0],
            desktop='aqua',
            session='quartz',
            managers=tuple(m for m in MAC_MANAGERS if shutil.which(m.name)),
            elevation=how,
            elevation_note=why,
        )

    release = _os_release()
    distro = release.get('ID', '')
    like = tuple(release.get('ID_LIKE', '').split())
    immutable = _is_immutable(release)

    managers = tuple(m for m in LINUX_MANAGERS if shutil.which(m.name))
    if immutable:
        # On a read-only root the system managers are present and will not
        # work — reordering rather than hiding them, because the model still
        # needs to be able to say "apt exists here and is the wrong tool".
        managers = tuple(sorted(managers, key=lambda m: m.scope != 'user'))

    how, why = _elevation()
    arch, foreign = _architectures()
    return Platform(
        system='linux',
        distro=distro,
        like=like,
        version=release.get('VERSION_ID', ''),
        desktop=os.environ.get('XDG_CURRENT_DESKTOP', ''),
        session=os.environ.get('XDG_SESSION_TYPE', ''),
        immutable=immutable,
        steam_deck=(
            distro == 'steamos'
            or release.get('VARIANT_ID') == 'steamdeck'
            or Path('/etc/steamos-release').exists()
        ),
        managers=managers,
        elevation=how,
        elevation_note=why,
        arch=arch,
        foreign_arches=foreign,
    )


# ---------------------------------------------------------------------------
# Things people ask for by name
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Route:
    """How to get one named thing onto one kind of machine."""

    manager: str
    package: str
    note: str = ''


@dataclass(frozen=True, slots=True)
class Recipe:
    """A thing people ask for, and what it actually means per platform.

    This exists because the honest answer to several of these is not a
    package. "Install the Epic Games Store on Linux" has no Epic package to
    install — there is no Linux client — and the useful answer is Heroic,
    which is a different program that does the same job. A model can be told
    that here once, rather than each person finding out by watching it fail.
    """

    names: tuple[str, ...]
    routes: dict[str, Route] = field(default_factory=dict)
    note: str = ''


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        names=('steam', 'steam client', 'valve steam'),
        routes={
            'debian': Route('apt', 'steam-installer',
                            note='Needs i386 multiarch; the package pulls it in.'),
            'ubuntu': Route('apt', 'steam-installer'),
            'arch': Route('pacman', 'steam', note='Needs the multilib repository enabled.'),
            'fedora': Route('dnf', 'steam', note='From RPM Fusion, which must be enabled.'),
            'linux': Route('flatpak', 'com.valvesoftware.Steam'),
            'windows': Route('winget', 'Valve.Steam'),
            'darwin': Route('brew', '--cask steam'),
        },
        note='Already installed on a Steam Deck — check before installing anything.',
    ),
    Recipe(
        names=('epic games store', 'epic games launcher', 'epic', 'epic games'),
        routes={
            'windows': Route('winget', 'EpicGames.EpicGamesLauncher'),
            'darwin': Route('brew', '--cask epic-games'),
            'linux': Route(
                'flatpak', 'com.heroicgameslauncher.hgl',
                note='Heroic, not Epic. Epic ships no Linux client at all, and Heroic is '
                     'the usual way people play their Epic library on Linux — it signs in '
                     'to the same account and runs the games through Proton.',
            ),
        },
        note='On Linux and SteamOS this installs Heroic rather than Epic itself. Say so '
             'before doing it: someone who asked for Epic should be told they are getting '
             'a different program, and why.',
    ),
    Recipe(
        names=('heroic', 'heroic games launcher'),
        routes={
            'linux': Route('flatpak', 'com.heroicgameslauncher.hgl'),
            'windows': Route('winget', 'HeroicGamesLauncher.HeroicGamesLauncher'),
        },
    ),
    Recipe(
        names=('lutris',),
        routes={'linux': Route('flatpak', 'net.lutris.Lutris')},
    ),
    Recipe(
        names=('discord',),
        routes={
            'linux': Route('flatpak', 'com.discordapp.Discord'),
            'windows': Route('winget', 'Discord.Discord'),
            'darwin': Route('brew', '--cask discord'),
        },
    ),
    Recipe(
        names=('telegram', 'telegram desktop'),
        routes={
            'linux': Route('flatpak', 'org.telegram.desktop'),
            'windows': Route('winget', 'Telegram.TelegramDesktop'),
            'darwin': Route('brew', '--cask telegram'),
        },
        note='On Linux the flatpak keeps its data in ~/.var/app/org.telegram.desktop, not '
             '~/.local/share/TelegramDesktop where the tarball build does — copy tdata '
             'across before first launch to keep a signed-in session.',
    ),
    Recipe(
        names=('protonup', 'protonup-qt', 'proton ge'),
        routes={'linux': Route('flatpak', 'net.davidotek.pupgui2')},
        note='Manages Proton-GE builds for Steam and Heroic. The usual next step after '
             'installing either on Linux.',
    ),
)


def recipe_for(what: str) -> Recipe | None:
    """The recipe for something asked for by name, matched loosely."""
    wanted = ' '.join(what.lower().split())
    for recipe in RECIPES:
        if wanted in recipe.names:
            return recipe
    # A containing match, so "install steam please" and "the epic games store"
    # both land. Longest name first, so 'epic games store' beats 'epic'.
    for recipe in RECIPES:
        for name in sorted(recipe.names, key=len, reverse=True):
            if name in wanted:
                return recipe
    return None


def search_terms(what: str, recipe: Recipe | None = None) -> tuple[str, ...]:
    """The words that identify a thing among what is already installed.

    A recipe knows its package ids as well as its names, which is how a
    flatpak listed as `org.telegram.desktop` is recognised as "telegram".
    """
    if recipe is not None:
        ids = (r.package.split()[-1] for r in recipe.routes.values())
        return tuple(dict.fromkeys(t.lower() for t in (*recipe.names, *ids)))
    filler = {'install', 'uninstall', 'remove', 'reinstall', 'the', 'please', 'app', 'latest'}
    return tuple(w for w in what.lower().split() if len(w) > 2 and w not in filler)


def matching_lines(listing: str, terms: tuple[str, ...]) -> list[str]:
    """Lines of a manager's installed list that mention any of the terms."""
    return [
        line.strip() for line in listing.splitlines()
        if line.strip() and any(t in line.lower() for t in terms)
    ]


@dataclass(frozen=True, slots=True)
class Launcher:
    """A desktop entry, and whether the program it starts is still there."""

    path: Path
    name: str
    target: str
    #: False when it points at an absolute path that no longer exists: a
    #: menu entry left by an unpacked tarball whose folder was deleted.
    present: bool


def launcher_dirs() -> tuple[Path, ...]:
    home = Path.home()
    return (
        home / '.local/share/applications',
        home / '.local/share/flatpak/exports/share/applications',
        Path('/var/lib/flatpak/exports/share/applications'),
        Path('/usr/share/applications'),
        Path('/usr/local/share/applications'),
    )


def launchers_for(terms: tuple[str, ...], dirs: tuple[Path, ...] | None = None) -> list[Launcher]:
    """Desktop entries for something, whoever put them there.

    Package managers only know what they installed. A program unpacked by hand
    — Telegram's tarball, an AppImage — registers itself here and nowhere else,
    and is invisible to every listing above. Looking only at those is how an
    agent concludes nothing is installed while a stale launcher sits in the
    menu, or installs a second copy beside the first.
    """
    found: list[Launcher] = []
    for directory in dirs if dirs is not None else launcher_dirs():
        try:
            entries = sorted(directory.glob('*.desktop'))
        except OSError:
            continue
        for path in entries:
            try:
                text = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            fields: dict[str, str] = {}
            for line in text.splitlines():
                if line.startswith('[') and fields:
                    break       # only the main [Desktop Entry] group
                key, sep, value = line.partition('=')
                if sep and key.strip() in ('Name', 'Exec', 'TryExec'):
                    fields.setdefault(key.strip(), value.strip())
            name = fields.get('Name', '')
            haystack = f'{path.name} {name}'.lower()
            if not any(t in haystack for t in terms):
                continue
            target = fields.get('TryExec', '')
            if not target:
                try:
                    target = (shlex.split(fields.get('Exec', '')) or [''])[0]
                except ValueError:      # unbalanced quoting in a hand-written entry
                    target = fields.get('Exec', '').split(' ', 1)[0]
            present = not target.startswith('/') or Path(target).exists()
            found.append(Launcher(path, name, target, present))
    return found


def route_for(recipe: Recipe, host: Platform) -> Route | None:
    """The route this machine should take, most specific first.

    Distribution, then anything it says it is like, then the OS. `pop` is not
    in the table and does not need to be: it declares `ID_LIKE="ubuntu debian"`
    and picks up Ubuntu's route, which is the point of reading `ID_LIKE` at
    all rather than enumerating every derivative.
    """
    for key in (*host.family, host.system):
        route = recipe.routes.get(key)
        if route and (host.manager(route.manager) or route.manager == 'flatpak'):
            return route
    return None


# ---------------------------------------------------------------------------
# Display settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DisplayRoute:
    """How a display setting is changed on this desktop.

    Some desktops expose one as a command and some only as a switch in a
    settings window, and there is no honest way to make that difference go
    away. So it is reported: `command` where one exists, and otherwise the
    exact panel to open and what to look for in it — which the desktop tools
    can then drive, since omote can already see and click a screen.

    Pretending otherwise is the failure worth avoiding. An agent told there is
    a universal command will invent one, run it, get "command not found", and
    try the next invention.
    """

    #: argv template with `{output}` and `{state}`, or () when there is none.
    command: tuple[str, ...] = ()
    #: How to open the settings page, for the cases that need a human or the
    #: desktop tools.
    panel: tuple[str, ...] = ()
    where: str = ''
    supported: bool = True


def hdr_route(host: Platform) -> DisplayRoute:
    """How HDR is turned on here.

    Only one desktop currently exposes it to the command line in a form worth
    relying on. The rest are a switch in a window — which is a route rather
    than a dead end, because the desktop tools can open a window and click.
    """
    desktop = (host.desktop or '').upper()

    if host.system == 'windows':
        return DisplayRoute(
            panel=('cmd', '/c', 'start', 'ms-settings:display'),
            where='Settings → System → Display → "Use HDR". It is per-display, so select '
                  'the right one at the top first.',
        )

    if host.system == 'darwin':
        return DisplayRoute(
            panel=('open', '-b', 'com.apple.systempreferences'),
            where='System Settings → Displays. Apple manages HDR per display and mostly '
                  'automatically; there is no switch on many Macs.',
        )

    if host.steam_deck or 'GAMESCOPE' in desktop:
        return DisplayRoute(
            where='Steam → Settings → Display → HDR, in the Steam Deck interface. HDR needs '
                  'gamescope, so it applies in Game Mode and not on the desktop.',
        )

    if shutil.which('kscreen-doctor'):
        return DisplayRoute(
            command=('kscreen-doctor', 'output.{output}.hdr.{state}'),
            panel=('systemsettings', 'kcm_kscreen'),
            where='System Settings → Display & Monitor → High Dynamic Range.',
        )

    if 'COSMIC' in desktop:
        return DisplayRoute(
            panel=('cosmic-settings', 'display'),
            where='Settings → Displays. cosmic-randr, which is the command line for '
                  'displays here, has no HDR subcommand — this one is the window only.',
        )

    if 'GNOME' in desktop:
        return DisplayRoute(
            panel=('gnome-control-center', 'display'),
            where='Settings → Displays. On GNOME versions before 48 HDR is behind an '
                  'experimental flag and may not appear at all.',
        )

    return DisplayRoute(
        supported=False,
        where=f'nothing is known about changing HDR on {host.desktop or "this desktop"}.',
    )
