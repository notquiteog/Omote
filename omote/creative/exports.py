"""Read exported files back: a successful save reply alone is not verification."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def inspect_file(path: Path, expected=None, *, decode=False):
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('Export file is missing or empty.')
    before = path.stat()
    result = {'path': str(path), 'bytes': before.st_size, 'verified': False, 'errors': [], 'warnings': []}
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024**2), b''):
            digest.update(data)
    result['sha256'] = digest.hexdigest()
    if path.suffix.lower() == '.svg':
        raw = path.read_bytes() if path.stat().st_size <= 16 * 1024**2 else b''
        try:
            if not raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('Oversized SVG or entity declarations are not supported.')
            root = ET.fromstring(raw)
            if root.tag.rsplit('}', 1)[-1] != 'svg':
                raise ValueError('Not an SVG document.')
            box = [float(value) for value in root.get('viewBox', '').replace(',', ' ').split()]
            if box and (len(box) != 4 or any(not math.isfinite(value) for value in box) or box[2] <= 0 or box[3] <= 0):
                raise ValueError('Invalid SVG viewBox.')
            result.update(kind='vector', format='SVG', verified=True, view_box=box,
                          declared_width=root.get('width'), declared_height=root.get('height'))
            if len(box) == 4:
                result.update(width=box[2], height=box[3])
        except (ET.ParseError, ValueError) as error:
            result['errors'].append(str(error))
    image_error = None
    try:
        from PIL import Image

        if not result['verified']:
            with Image.open(path) as im:
                result.update(kind='image', format=im.format, width=im.width, height=im.height,
                              mode=im.mode, frames=getattr(im, 'n_frames', 1), alpha='A' in im.getbands() or 'transparency' in im.info)
                im.verify()
            with Image.open(path) as im:
                im.load()
                if result['alpha']:
                    result['has_transparency'] = im.convert('RGBA').getchannel('A').getextrema()[0] < 255
            result['verified'] = True
    except ImportError:
        pass
    except Exception as error:  # noqa: BLE001 — malformed exports are data failures
        image_error = type(error).__name__
    if not result['verified']:
        # ffprobe may recover only the header of a truncated PNG/JPEG. Do not
        # turn a failed pixel decode into a successful verification that way.
        raster_failure = image_error and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.tiff', '.tif', '.bmp', '.psd')
        binary = None if raster_failure or path.suffix.lower() == '.svg' else shutil.which('ffprobe')
        if binary:
            try:
                done = subprocess.run([binary, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries',
                    'format=format_name,duration:stream=codec_type,codec_name,width,height,avg_frame_rate,pix_fmt,sample_rate,channels,color_space,color_transfer,color_primaries:stream_tags=alpha_mode',
                    '-of', 'json', str(path)], capture_output=True, text=True, timeout=20, check=False)
                data = json.loads(done.stdout) if done.returncode == 0 else {}
                streams = data.get('streams', [])
                video = next((row for row in streams if row.get('codec_type') == 'video'), {})
                if streams:
                    primary = video or streams[0]
                    result.update(kind='video' if video else 'audio', format=data.get('format', {}).get('format_name'), streams=streams,
                                  has_audio=any(row.get('codec_type') == 'audio' for row in streams), codec=primary.get('codec_name'),
                                  width=video.get('width'), height=video.get('height'), pixel_format=video.get('pix_fmt'))
                    if video:
                        pixel = str(video.get('pix_fmt', ''))
                        result['alpha'] = pixel.startswith(('yuva', 'rgba', 'bgra', 'argb', 'abgr', 'gbrap', 'ya8', 'ya16')) or str(video.get('tags', {}).get('alpha_mode')) == '1'
                    duration = float(data.get('format', {}).get('duration', 0))
                    result['duration'] = duration if math.isfinite(duration) else None
                    num, den = video.get('avg_frame_rate', '0/1').split('/')
                    result['frame_rate'] = float(num) / float(den) if float(den) else None
                    result['verified'] = True
                    result['decode_verified'] = False
                    if decode:
                        from omote.media.edit import ffmpeg

                        decoder = ffmpeg()
                        if not decoder:
                            result['errors'].append('Full decoding needs FFmpeg; only container metadata was checked.')
                        else:
                            decoded = subprocess.run([decoder, '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe',
                                '-i', str(path), '-map', '0:v?', '-map', '0:a?', '-f', 'null', '-'],
                                capture_output=True, text=True, timeout=120, check=False)
                            result['decode_verified'] = decoded.returncode == 0
                            if decoded.returncode:
                                result['errors'].append('Full media decoding failed; export may be incomplete or corrupt.')
            except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError):
                result['errors'].append('Media probing or full decoding did not complete.')
        if not result['verified']:
            result['kind'] = 'document'
            result['warnings'].append('File exists and is hashed. Reopen it in its native app to verify editable layers, vectors or timeline state; media structure is not verified.')
            if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.tiff', '.mp4', '.mov', '.webm', '.wav', '.mp3'):
                result['errors'].append(f'Could not decode this media export ({image_error or "decoder unavailable"}).')
    for name, wanted in (expected or {}).items():
        actual = result.get(name)
        matches = actual == wanted
        if name in ('duration', 'frame_rate') and isinstance(actual, (int, float)):
            matches = abs(actual - wanted) <= (.15 if name == 'duration' else .05)
        if not matches:
            result['errors'].append(f'{name}: expected {wanted!r}, found {actual!r}.')
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        result['errors'].append('The export changed during verification. Wait for the render to finish before checking it.')
    result['ok'] = result['verified'] and not result['errors']
    return result
