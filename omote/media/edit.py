"""Changing a file somebody already has.

Not generating one. The difference is not cosmetic and it is why this is a
separate provider protocol: a generation has a prompt and a seed and is
reproducible from those two, and an edit has neither — it has a file and a
request. Offering them through one shape means the edit path has to accept a
prompt it will ignore, and the generation path has to accept an operation it
cannot perform.

**Three back ends, and what each is honestly good for.**

* **Pillow** for images, with no extra dependency on installs that already
  have it — crop, resize, rotate, flip, convert. Deterministic, fast,
  reversible, and the right answer for most of what anybody asks for.
* **ffmpeg** for video and audio, behind an optional extra. One dependency
  buys trim, cut, concatenate, frame extraction, speed, mute, extract-audio,
  and burning a subtitle track — which is every operation a video editor is
  made of, for something that is on every machine and is not a package.
* **GIMP**, through its batch interpreter, for the image operations Pillow
  does not do — the ones with real image science behind them, like
  unsharp-mask, selective colour and content-aware fill. It is offered because
  it is *scriptable from outside*, which Photoshop is not: `.jsx` needs
  Photoshop installed and running on Windows or macOS, and this project is not
  that product. See `omote/providers/gimp.py` for the shape of the thing
  and why it is here rather than Photoshop.

**Every operation records what it did, and nothing else.** The recipe written
beside the result is the operation and its arguments — the input media *ids*,
never the bytes and never a path. A recipe containing a megabyte of base64 is
a sidecar no tool can open, and one containing a path is a record that breaks
the day the file moves.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Arguments are capped because they end up on a command line. This is not a
#: security boundary — the values come from a typed tool, not from a shell — but
#: a length that absurd should be refused rather than passed along.
MAX_ARG = 400


class EditUnavailable(RuntimeError):
    """The operation needs something this machine does not have."""


class EditRefused(ValueError):
    """The operation cannot be done to that file, and here is why."""


def ffmpeg() -> str | None:
    """The ffmpeg binary, or None.

    `imageio-ffmpeg` is the optional extra and it *bundles* a static binary,
    which is the difference between an extra that works on a machine with no
    package manager and one that does not. A binary already on `PATH` is
    preferred, because it is the one somebody deliberately installed.
    """
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - best effort by definition
        log.info('imageio-ffmpeg is installed but gave no path: %s', exc)
        return None


def pillow() -> Any:
    """The Pillow module, or None.

    Imported lazily and by name rather than at module scope: the editing code
    runs on a worker thread for a single operation, and an install without
    the `desktop` extra should be able to import everything else.
    """
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None
    return Image


def gimp() -> str | None:
    """The GIMP binary, or None."""
    return shutil.which('gimp') or shutil.which('gimp-3.0') or shutil.which('gimp-2.10')


def _clean(value: Any, *, what: str = 'a value') -> str:
    """A single command-line argument, checked and length-capped."""
    text = str(value if value is not None else '')
    if len(text) > MAX_ARG:
        raise EditRefused(f'{what} is {len(text)} characters, which is too long to pass along')
    if '\x00' in text:
        raise EditRefused(f'{what} contains a null byte')
    return text


def _number(value: Any, *, low: float, high: float, what: str, integer: bool = False) -> float:
    """A bounded number.

    Bounded because these end up in a command line and in a formula, and
    "0.5 seconds" or a 100000x speed is a value that should be refused with a
    sentence rather than handed to ffmpeg.
    """
    try:
        found = float(value)
    except (TypeError, ValueError):
        raise EditRefused(f'{what} must be a number, not {value!r}') from None
    if not (low <= found <= high):
        raise EditRefused(f'{what} must be between {low:g} and {high:g}, not {found:g}')
    return int(found) if integer else found


# ---------------------------------------------------------------------------
# Video, through ffmpeg
# ---------------------------------------------------------------------------

VIDEO_OPERATIONS = ('trim', 'cut', 'concat', 'frames', 'speed', 'mute', 'audio', 'subtitles', 'poster')


def _run(command: list[str], *, what: str) -> None:
    """Run a tool, and turn its failure into something a person can read.

    ffmpeg writes a great deal to stderr and returns non-zero for a dozen
    ordinary reasons — an unknown codec, a filter it does not have, a file it
    could not open. Passing that through verbatim is a page of text; the last
    few lines are the actual reason.
    """
    try:
        done = subprocess.run(  # noqa: S603 - a fixed binary with a list of arguments
            command, capture_output=True, text=True, timeout=900, check=False,
        )
    except subprocess.TimeoutExpired:
        raise EditUnavailable(f'{what} took longer than fifteen minutes and was stopped') from None
    except OSError as exc:
        raise EditUnavailable(f'could not run {what}: {exc}') from None
    if done.returncode != 0:
        tail = '\n'.join((done.stderr or '').strip().splitlines()[-4:])
        raise EditRefused(f'{what} failed: {tail or f"exit code {done.returncode}"}')


def video(
    operation: str,
    source: str,
    target: str,
    args: dict[str, Any],
    *,
    second: str = '',
    subtitles: str = '',
) -> dict[str, Any]:
    """One video operation. `source` and `target` are paths the caller holds.

    Paths rather than bytes because ffmpeg reads from disk, and the caller is
    the media service, which has already resolved an id to a path inside its own
    store. Nothing here takes a path from a model.
    """
    binary = ffmpeg()
    if not binary:
        raise EditUnavailable(
            'this needs ffmpeg and there is none. Install ffmpeg, or pip install '
            "omote[video] — that extra bundles a static binary, which is the difference "
            'between an extra that works and one that does not.'
        )
    operation = operation.strip()
    if operation not in VIDEO_OPERATIONS:
        raise EditRefused(f'{operation!r} is not a video operation this knows')

    if operation == 'trim':
        start = _number(args.get('start', 0), low=0, high=86_400, what='start')
        length = _number(args.get('length', 5), low=0.04, high=86_400, what='length')
        # `-ss` before `-i` seeks by keyframe, which is fast and slightly
        # imprecise; after it, which is what makes a trim land where somebody
        # asked. Re-encoding is the price and it is the right one.
        _run([
            binary, '-y', '-ss', f'{start:.3f}', '-i', source, '-t', f'{length:.3f}',
            '-c:v', 'libx264', '-preset', 'veryfast', '-c:a', 'aac', target,
        ], what=f'trimming to {start:g}s for {length:g}s')
        return {'operation': 'trim', 'start': start, 'length': length}

    if operation == 'cut':
        start = _number(args.get('start', 0), low=0, high=86_400, what='start')
        end = _number(args.get('end', 1), low=0, high=86_400, what='end')
        if end <= start:
            raise EditRefused(f'the end ({end:g}s) has to be after the start ({start:g}s)')
        head = pathlib_target(f'{target}.head.mp4')
        tail = pathlib_target(f'{target}.tail.mp4')
        try:
            video('trim', source, head, {'start': 0, 'length': max(0.04, start)})
            video('trim', source, tail, {'start': end, 'length': max(0.04, end - start)})
            concat([head, tail], target)
        finally:
            for made in (head, tail):
                pathlib_unlink(made)
        return {'operation': 'cut', 'start': start, 'end': end}

    if operation == 'concat':
        if not second:
            raise EditRefused('joining two clips needs a second one')
        concat([source, second], target)
        return {'operation': 'concat', 'parts': 2}

    if operation == 'frames':
        every = _number(args.get('every', 1), low=0.04, high=60, what='every')
        _run([binary, '-y', '-i', source, '-vf', f'fps=1/{every:g}', target], what='extracting frames')
        return {'operation': 'frames', 'every': every}

    if operation == 'speed':
        factor = _number(args.get('factor', 2), low=0.1, high=10, what='factor')
        _run([
            binary, '-y', '-i', source,
            '-filter_complex', f'[0:v]{factor:g}*v[v];[0:a]{1 / factor:g}*a[a]',
            '-map', '[v]', '-map', '[a]', target,
        ], what=f'speeding up {factor:g}x')
        return {'operation': 'speed', 'factor': factor}

    if operation == 'mute':
        _run([binary, '-y', '-i', source, '-c', 'copy', '-an', target], what='removing the sound')
        return {'operation': 'mute'}

    if operation == 'audio':
        _run([binary, '-y', '-i', source, '-vn', '-c:a', 'libmp3lame', '-q:a', '4', target],
             what='taking the sound out')
        return {'operation': 'audio'}

    if operation == 'subtitles':
        if not subtitles:
            raise EditRefused('burning in subtitles needs the subtitle file')
        _run([
            binary, '-y', '-i', source, '-vf', f"subtitles={_escape_path(subtitles)}", target,
        ], what='burning in subtitles')
        return {'operation': 'subtitles'}

    # poster
    at = _number(args.get('at', 0), low=0, high=86_400, what='at')
    _run([binary, '-y', '-ss', f'{at:.3f}', '-i', source, '-frames:v', '1', target],
         what='taking a still')
    return {'operation': 'poster', 'at': at}


def concat(parts: list[str], target: str) -> None:
    """Join clips, in order.

    Through the *concat demuxer* with a list file rather than by re-encoding
    into one codec: same codecs in, same codecs out, no quality loss and no
    ten-minute wait. And the file is written to a temporary path rather than
    passed on the command line, because a filename with an apostrophe in it is
    common and is not worth quoting by hand.
    """
    binary = ffmpeg()
    if not binary:
        raise EditUnavailable('this needs ffmpeg and there is none')
    listing = pathlib_target(f'{target}.concat.txt')
    pathlib_write_text(listing, ''.join(f"file '{_escape_path(p)}'\n" for p in parts))
    try:
        _run([binary, '-y', '-f', 'concat', '-safe', '0', '-i', listing, '-c', 'copy', target],
             what='joining the clips')
    finally:
        pathlib_unlink(listing)


def probe(path: str) -> dict[str, Any]:
    """What ffmpeg says this file is.

    Duration matters most and is the reason this exists: "how long is the clip
    I just imported" is the question before every trim, and answering it by
    opening the file in a player is a worse answer.
    """
    binary = shutil.which('ffprobe') or ffmpeg()
    if not binary:
        raise EditUnavailable('this needs ffprobe and there is none')
    try:
        done = subprocess.run(  # noqa: S603 - a fixed binary with a list of arguments
            [
                binary, '-v', 'error', '-show_entries', 'format=duration,size:stream=codec_type,width,height',
                '-of', 'default=noprint_wrappers=1', path,
            ],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EditUnavailable(f'could not read that file: {exc}') from None
    if done.returncode != 0:
        raise EditRefused(f'ffprobe could not read it: {(done.stderr or "").strip()[:200]}')

    found: dict[str, Any] = {'duration': 0.0, 'has_video': False, 'has_audio': False}
    for line in done.stdout.splitlines():
        key, _, value = line.partition('=')
        if key == 'duration':
            try:
                found['duration'] = round(float(value), 3)
            except ValueError:
                pass
        elif key == 'codec_type':
            found['has_video' if value == 'video' else 'has_audio'] = True
        elif key in ('width', 'height'):
            found[key] = int(value) if value.isdigit() else None
    return found


# ---------------------------------------------------------------------------
# Images, through Pillow
# ---------------------------------------------------------------------------

IMAGE_OPERATIONS = ('crop', 'resize', 'rotate', 'flip', 'convert', 'sharpen', 'poster')


def image(operation: str, source: str, target: str, args: dict[str, Any]) -> dict[str, Any]:
    """One image operation, without a dependency this install might not have."""
    Image = pillow()
    if Image is None:
        raise EditUnavailable(
            'this needs Pillow and there is none. Install the desktop extra '
            "(pip install 'omote[desktop]') or use the GIMP editor, which needs GIMP."
        )
    operation = operation.strip()
    if operation not in IMAGE_OPERATIONS:
        raise EditRefused(f'{operation!r} is not an image operation this knows')

    with Image.open(source) as made:
        # A PNG with transparency saved as JPEG loses its alpha silently and
        # comes out with a black background. Flattening onto white is the choice
        # that looks like what somebody expected.
        if operation == 'convert' and str(args.get('format', '')).upper() in ('JPEG', 'JPG'):
            if made.mode in ('RGBA', 'LA', 'P'):
                flat = Image.new('RGB', made.size, (255, 255, 255))
                flat.paste(made, mask=made.split()[-1] if 'A' in made.getbands() else None)
                made = flat

        if operation == 'crop':
            box = args.get('box')
            if isinstance(box, (list, tuple)) and len(box) == 4:
                left, top, right, bottom = (int(v) for v in box)
            else:
                left = int(args.get('left', 0))
                top = int(args.get('top', 0))
                width = _number(args.get('width', made.width), low=1, high=100_000,
                                what='width', integer=True)
                height = _number(args.get('height', made.height), low=1, high=100_000,
                                 what='height', integer=True)
                right, bottom = left + width, top + height
            if right <= left or bottom <= top:
                raise EditRefused('the crop has to have a positive width and height')
            made = made.crop((left, top, right, bottom))
            said = {'left': left, 'top': top, 'width': right - left, 'height': bottom - top}
        elif operation == 'resize':
            width = args.get('width')
            height = args.get('height')
            factor = args.get('factor')
            if factor:
                scale = _number(factor, low=0.01, high=32, what='factor')
                made = made.resize(
                    (max(1, int(made.width * scale)), max(1, int(made.height * scale))),
                    Image.LANCZOS,
                )
            else:
                made = made.resize(
                    (int(width or made.width), int(height or made.height)), Image.LANCZOS
                )
            said = {'width': made.width, 'height': made.height}
        elif operation == 'rotate':
            angle = _number(args.get('angle', 90), low=-360, high=360, what='angle')
            made = made.rotate(-angle, expand=True)
            said = {'angle': angle}
        elif operation == 'flip':
            axis = str(args.get('axis', 'horizontal')).strip().lower()
            if axis in ('horizontal', 'h', 'x'):
                made = made.transpose(Image.FLIP_LEFT_RIGHT)
            elif axis in ('vertical', 'v', 'y'):
                made = made.transpose(Image.FLIP_TOP_BOTTOM)
            else:
                raise EditRefused("axis must be 'horizontal' or 'vertical'")
            said = {'axis': axis}
        elif operation == 'sharpen':
            radius = _number(args.get('radius', 2), low=0.1, high=20, what='radius')
            percent = int(_number(args.get('percent', 150), low=0, high=500, what='percent'))
            made = made.filter(Image.UnsharpMask(radius=radius, percent=percent, threshold=3))
            said = {'radius': radius, 'percent': percent}
        elif operation == 'poster':
            # A contact sheet, because one frame of a video is not a preview of
            # it and nine evenly spaced frames are.
            made = _contact_sheet(Image, source, int(args.get('frames', 9)))
            said = {'frames': int(args.get('frames', 9))}
        else:  # convert
            fmt = str(args.get('format', 'PNG')).upper()
            quality = args.get('quality')
            save: dict[str, Any] = {}
            if fmt in ('JPEG', 'JPG') and quality is not None:
                save['quality'] = int(_number(quality, low=1, high=100, what='quality',
                                               integer=True))
            made.save(target, format=fmt, **save)
            said = {'format': fmt}
            made.close()
            return {'operation': 'convert', **said}

        made.save(target)
        return {'operation': operation, **said, 'width': made.width, 'height': made.height}


def _contact_sheet(Image: Any, source: str, count: int) -> Any:
    """Nine frames from a video, side by side, as one picture.

    Only reachable with ffmpeg present, because it is the thing ffmpeg is for;
    the error is the missing-binary one rather than something invented.
    """
    binary = ffmpeg()
    if not binary:
        raise EditUnavailable('a contact sheet needs ffmpeg')
    import tempfile  # noqa: PLC0415

    made_frames: list[Any] = []
    with tempfile.TemporaryDirectory(prefix='omote-frames-') as scratch:
        pattern = f'{scratch}/frame-%03d.jpg'
        _run([binary, '-y', '-i', source, '-vf', f'fps={count}/max', '-frames:v', str(count),
              pattern], what='extracting frames')
        for found in sorted(pathlib_glob(f'{scratch}/frame-*.jpg')):
            with Image.open(found) as frame:
                made_frames.append(frame.copy())

    if not made_frames:
        raise EditRefused('that file has no frames to make a sheet from')
    width = min(f.width for f in made_frames)
    scaled = [f.resize((width, max(1, int(f.height * width / f.width)))) for f in made_frames]
    columns = 3
    rows = (len(scaled) + columns - 1) // columns
    cell_h = max(f.height for f in scaled)
    sheet = Image.new('RGB', (width * columns, cell_h * rows), (16, 16, 20))
    for index, frame in enumerate(scaled):
        sheet.paste(frame, ((index % columns) * width, (index // columns) * cell_h))
    return sheet


# ---------------------------------------------------------------------------
# The small filesystem helpers, so the ffmpeg paths stay readable
# ---------------------------------------------------------------------------


def pathlib_target(name: str) -> str:
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    # `name` may be an absolute target path. Prefixing it produced a missing
    # /tmp/omote-/full/path tree, and shared basenames could also collide.
    descriptor, path = tempfile.mkstemp(prefix='omote-', suffix=Path(name).suffix)
    os.close(descriptor)
    return path


def pathlib_unlink(path: str) -> None:
    from pathlib import Path  # noqa: PLC0415

    try:
        Path(path).unlink(missing_ok=True)
    except OSError:  # pragma: no cover - best effort
        log.debug('could not remove %s', path, exc_info=True)


def pathlib_write_text(path: str, text: str) -> None:
    from pathlib import Path  # noqa: PLC0415

    Path(path).write_text(text, encoding='utf-8')


def pathlib_glob(pattern: str) -> list[str]:
    from glob import glob  # noqa: PLC0415

    return glob(pattern)


def _escape_path(path: str) -> str:
    r"""A path for ffmpeg's filter syntax, which is not a shell.

    `subtitles=...` goes through the *filtergraph* parser, whose rules are its
    own: a colon separates options and a backslash escapes. So the characters
    that matter here are `:` and `\` and `'` — none of which mean anything to a
    shell, which is why this function exists at all and is not `shlex.quote`.
    """
    return path.replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")


#: A scratch file needs a real extension, because Pillow infers its format from
#: the name and ffmpeg infers its muxer from it. `x.out` makes Pillow raise
#: "unknown file extension", which is a confusing way to learn that.
SCRATCH_SUFFIX: dict[str, str] = {
    'pillow': '.png',
    'gimp': '.png',
    'trim': '.mp4', 'cut': '.mp4', 'concat': '.mp4', 'speed': '.mp4',
    'mute': '.mp4', 'subtitles': '.mp4',
    'frames': '.png', 'poster': '.png', 'audio': '.mp3',
}


def scratch_suffix(editor: str, operation: str) -> str:
    return SCRATCH_SUFFIX.get(operation) or SCRATCH_SUFFIX.get(editor, '.bin')


def guess_media_type(path: str) -> str:
    """What a produced file is, from its bytes.

    `ffmpeg` names its output after the *codec* and writes `.out` here rather
    than a real extension, so the sidecar would say `application/octet-stream`
    and the gallery would file an `.mp4` as a `.bin`. Sniffed rather than
    guessed from the extension, for the same reason everything else in this
    project is sniffed: several of these tools are vague about what they call
    their output.
    """
    from omote.providers.hosted_media import sniff  # noqa: PLC0415

    try:
        with open(path, 'rb') as handle:
            head = handle.read(64)
    except OSError:
        return 'application/octet-stream'
    return sniff(head, url=path)


# ---------------------------------------------------------------------------
# What is on this machine
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Capabilities:
    """Which editors this install can actually serve.

    Asked of the machine rather than assumed, because the alternative is a
    `kind` in the picker that resolves to an editor that is not installed —
    the same failure the media service already refuses to offer for
    generators.
    """

    ffmpeg: bool = False
    pillow: bool = False
    gimp: bool = False
    _which: dict[str, str] = field(default_factory=dict, repr=False)

    def for_kind(self, kind: str) -> list[str]:
        """Editor names that could handle this kind of file."""
        if kind == 'video':
            return ['ffmpeg'] if self.ffmpeg else []
        if kind == 'audio':
            return ['ffmpeg'] if self.ffmpeg else []
        if kind == 'image':
            found = []
            if self.pillow:
                found.append('pillow')
            if self.gimp:
                found.append('gimp')
            return found
        return []


def capabilities() -> Capabilities:
    return Capabilities(
        ffmpeg=ffmpeg() is not None,
        pillow=pillow() is not None,
        gimp=gimp() is not None,
    )


__all__ = [
    'Capabilities', 'EditRefused', 'EditUnavailable', 'IMAGE_OPERATIONS', 'VIDEO_OPERATIONS',
    'capabilities', 'concat', 'ffmpeg', 'gimp', 'guess_media_type', 'image', 'pillow', 'probe',
    'scratch_suffix', 'video',
]
