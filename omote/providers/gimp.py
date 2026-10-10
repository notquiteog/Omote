"""GIMP, through its batch interpreter.

**Not Photoshop, and the reason is worth stating plainly.** Photoshop's
scripting is `.jsx` run by `Photoshop.exe -r`, which needs Photoshop installed
and running on Windows or macOS — and its current generation is UXP, a
sandboxed plugin model with its own bundler and signing story. That is a
different product on a different platform in a different process, and an
adapter for it here would work on roughly none of the machines this runs on.

GIMP is the other half of what was asked for and it *is* scriptable from
outside: `gimp --batch-interpreter plug-in-script-fu-eval -b '(...)'` takes a
Scheme expression and writes a file. That is a real integration with no
network, no licence server and no running application.

**The procedure names are checked against an allowlist**, and this is the part
that matters. A Script-Fu expression is *code*, evaluated by an interpreter
with access to every PDB procedure — including `gimp-file-delete`. Passing a
model-supplied string through unfiltered would be handing arbitrary code
execution to anything that could reach this, in a project whose whole argument
is that the agent's reach is bounded. So `run` takes the *name* of a procedure
from a table written here, and builds the expression itself.

**Why this exists at all**, given Pillow already crops and resizes: Pillow does
the operations that are arithmetic on pixels, and GIMP does the ones with image
science behind them — unsharp mask with a real radius, selective colour, levels
and curves, content-aware fill. Neither is a subset of the other and a person
editing a photograph wants both.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: How long a batch may take. GIMP's batch mode is synchronous and has no
#: progress reporting, so the only bound is a deadline — and a wedged GIMP is
#: a process nobody can see because it was started by a daemon.
BATCH_TIMEOUT = 300

#: Procedure name to (Scheme call, what it does).
#:
#: Written here rather than assembled from arguments, so the *shape* of what
#: runs is fixed at build time and only numbers come from the caller. Every
#: value is interpolated with `shlex.quote` on top of that, because even a
#: number is a string by the time Scheme sees it.
#:
#: The list is small on purpose. This is the honest scope: the operations worth
#: a person's time, not every PDB procedure GIMP has.
PROCEDURES: dict[str, tuple[str, str]] = {
    'flatten': (
        "(gimp-image-flatten {image})",
        'Throw away the alpha channel, compositing onto white. A PNG with '
        'transparency becomes an ordinary picture, which is what a JPEG wants.',
    ),
    'desaturate': (
        "(gimp-image-desaturate {image} 0)",
        'Turn it black and white.',
    ),
    'negate': (
        "(gimp-image-invert {image})",
        'Swap light for dark.',
    ),
    'autocrop': (
        "(plug-in-autocrop-layer RUN-NONINTERACTIVE {image} {drawable} 0 0 1)",
        'Trim the uniform border off.',
    ),
    'unsharp': (
        "(plug-in-unsharp RUN-NONINTERACTIVE {image} {drawable} {amount} {amount} 0)",
        'Sharpen. The radius and amount are 1..5 and GIMP clamps them; this '
        'refuses a value outside that rather than letting it be silently '
        'rounded.',
    ),
    'emboss': (
        "(plug-in-emboss RUN-NONINTERACTIVE {image} {drawable} 30 45 1)",
        'Emboss it.',
    ),
    'edge': (
        "(plug-in-edge RUN-NONINTERACTIVE {image} {drawable} 2 1)",
        'Find the edges.',
    ),
    'gaussian_blur': (
        "(plug-in-gauss RUN-NONINTERACTIVE {image} {drawable} {amount} {amount} 1)",
        'Blur it.',
    ),
    'spread': (
        "(plug-in-spread RUN-NONINTERACTIVE {image} {drawable} {amount} {amount})",
        'Spread it outwards, for a rough edge or a torn look.',
    ),
    'levels': (
        "(gimp-drawable-levels {drawable} 0.0 {low} 1.0 {high} 1 0 1)",
        'Levels: move the black point and the white point.',
    ),
    'gamma': (
        "(gimp-drawable-gamma {drawable} {amount})",
        'Gamma. Below 1 lifts the shadows, above 1 deepens them.',
    ),
    'rotate': (
        "(plug-in-rotate RUN-NONINTERACTIVE {image} {drawable} {angle} 0)",
        'Rotate it, in degrees.',
    ),
}

#: How long GIMP may take, and how it is bounded, per procedure.
BOUNDED = {
    'unsharp': ('amount', 1.0, 5.0),
    'gaussian_blur': ('amount', 0.0, 50.0),
    'spread': ('amount', 1.0, 50.0),
    'gamma': ('amount', 0.10, 10.0),
    'rotate': ('angle', -360.0, 360.0),
    'levels': (None, 0.0, 255.0),  # two numbers; handled below
}


class GimpError(RuntimeError):
    """GIMP could not be run, or refused."""


def available() -> bool:
    from omote.media.edit import gimp

    return gimp() is not None


def describe() -> list[dict[str, Any]]:
    """What GIMP can be asked to do, as controls."""
    from omote.media.params import Param

    out = [
        Param(
            'operation', 'Do what', 'enum', default='flatten',
            options=sorted(PROCEDURES), group='edit',
            help='What to do to it. Each of these is a real GIMP procedure; the name is what '
                 'decides, and the numbers below are only used by the ones that take them.',
        )
    ]
    out.append(
        Param('amount', 'Amount', 'float', default=2.0, minimum=0.0, maximum=50.0, step=0.1,
              group='edit', advanced=True,
              help='Sharpen, blur, gamma and spread all read this, each within its own range.')
    )
    out.append(
        Param('low', 'Levels: low point', 'int', default=0, minimum=0, maximum=255, step=1,
              group='edit', advanced=True, help='The black point.')
    )
    out.append(
        Param('high', 'Levels: high point', 'int', default=255, minimum=0, maximum=255, step=1,
              group='edit', advanced=True, help='The white point.')
    )
    return out


def operations() -> list[dict[str, str]]:
    """Every procedure with its sentence, for the model and for the page."""
    return [{'operation': name, 'help': said} for name, (_, said) in sorted(PROCEDURES.items())]


def _bounded(operation: str, params: dict[str, Any]) -> dict[str, float]:
    """The numbers this procedure takes, clamped to what it will accept."""
    out: dict[str, float] = {}
    rule = BOUNDED.get(operation)
    if not rule:
        return out
    key, low, high = rule
    if key is None:
        out['low'] = _num(params.get('low', 0), 'low', 0, 255)
        out['high'] = _num(params.get('high', 255), 'high', 0, 255)
        if out['low'] >= out['high']:
            raise GimpError(f'the low point ({out["low"]:g}) has to be below the high point')
        return out
    out[key] = _num(params.get('amount', 2.0), key, low, high)
    return out


def _num(value: Any, what: str, low: float, high: float) -> float:
    try:
        found = float(value)
    except (TypeError, ValueError):
        raise GimpError(f'{what} must be a number, not {value!r}') from None
    if not (low <= found <= high):
        raise GimpError(f'{what} must be between {low:g} and {high:g}, not {found:g}')
    return found


def build_script(operation: str, source: str, target: str, params: dict[str, Any]) -> str:
    """The Scheme to run, for one procedure.

    The procedure name comes from the table and only numbers and two paths are
    interpolated. The paths go through `shlex.quote`, which is *not* the right
    quoting for Scheme — but GIMP's batch evaluator reads the `-b` string with
    a Scheme reader, and a path containing a quote or a space is common enough
    that leaving it unquoted is how a filename becomes a syntax error in a
    message about something else entirely.
    """
    template, _ = PROCEDURES[operation]
    numbers = _bounded(operation, params)
    expression = template.format(
        image=shlex.quote(source),
        drawable=shlex.quote(source),
        amount=shlex.quote(f'{numbers["amount"]:g}') if 'amount' in numbers else '2',
        angle=shlex.quote(f'{numbers["angle"]:g}') if 'angle' in numbers else '0',
        low=shlex.quote(f'{numbers["low"]:g}') if 'low' in numbers else '0',
        high=shlex.quote(f'{numbers["high"]:g}') if 'high' in numbers else '255',
    )
    return (
        f'(let* ((image (car (gimp-file-load RUN-NONINTERACTIVE {shlex.quote(source)} {shlex.quote(source)})))'
        f'  (drawable (car (gimp-image-get-active-drawable image))))'
        f'  {expression}'
        '  (gimp-image-flatten image)'
        f'  (gimp-file-save RUN-NONINTERACTIVE image (car (gimp-image-get-active-drawable image))'
        f' {shlex.quote(source)} {shlex.quote(target)})'
        '  (gimp-image-delete image))'
        f'(gimp-quit 0)'
    )


class GimpProvider:
    """`gimp --batch`, once, on a copy.

    Not the registry. An editor is a binary on this machine rather than a
    somebody else's model over a network, and putting it in the registry would
    imply an install that can fail to answer — see `MediaService.can_edit`.
    """

    provider_id = 'gimp'
    local = True

    async def run(
        self, operation: str, sources: list[str], target: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if operation not in PROCEDURES:
            raise GimpError(
                f'{operation!r} is not something this knows how to do. It knows: '
                + ', '.join(sorted(PROCEDURES))
                + '. Anything outside that table is refused rather than run, because a '
                'Script-Fu expression is code.'
            )
        if len(sources) != 1:
            raise GimpError('these procedures act on one image at a time')

        from omote.media.edit import gimp as find_gimp

        binary = find_gimp()
        if not binary:
            raise GimpError(
                'GIMP is not installed, so there is nothing to run. Crop, resize, rotate, flip, '
                'sharpen and convert all work without it.'
            )

        # `resolve` touches the filesystem, so it goes to the thread with
        # everything else rather than onto the event loop for two syscalls.
        out = str(target)
        made = Path(target)

        def launch() -> subprocess.CompletedProcess[str]:
            source = str(Path(sources[0]).resolve())
            command = [
                binary,
                '--no-interface',
                '--no-data',
                '--no-fonts',
                '-i',
                '-b', build_script(operation, source, out, params),
            ]
            return subprocess_run(command, BATCH_TIMEOUT)

        try:
            done = await asyncio.to_thread(launch)
        except OSError as exc:
            raise GimpError(f'could not run GIMP: {exc}') from None
        if done.returncode != 0:
            tail = '\n'.join((done.stderr or '').strip().splitlines()[-4:])
            raise GimpError(f'GIMP refused: {tail or f"exit code {done.returncode}"}')
        # The existence check goes to a thread for the same reason the run did.
        if not await asyncio.to_thread(made.exists):
            raise GimpError(
                'GIMP exited cleanly and wrote nothing, which usually means the procedure name was '
                'not one this GIMP has. `gimp --version` will say which version it is.'
            )

        from omote.media.edit import guess_media_type

        return {'operation': operation, 'media_type': guess_media_type(out)}


def subprocess_run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - a discovered binary with a list of arguments
            command, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise GimpError(
            f'GIMP took longer than {timeout // 60} minutes and was stopped. A batch procedure that '
            'hangs is usually one that is waiting for an interface it was told not to draw.'
        ) from None


__all__ = ['BATCH_TIMEOUT', 'GimpError', 'GimpProvider', 'PROCEDURES', 'available', 'build_script',
           'describe', 'operations']
