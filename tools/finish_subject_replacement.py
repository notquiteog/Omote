"""Composite and independently verify a rendered replacement against source pixels.

The generated-frame and target-mask directories must have matching zero-based
PNG sequences at 24 fps. Source frames and source masks use the source's frame
rate. A short preview is explicitly recorded as incomplete, never a full pass.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image

from omote.media.replacement import BackgroundAudit, composite_frame, subject_mask


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def image(path, mode):
    with Image.open(path) as opened:
        return np.array(opened.convert(mode))


def audio_packets(path):
    binary = shutil.which('ffprobe')
    if not binary:
        raise RuntimeError('FFprobe is required to verify original audio packets.')
    result = subprocess.check_output(
        [binary, '-v', 'error', '-select_streams', 'a:0', '-show_packets',
         '-show_entries', 'packet=data_hash', '-show_data_hash', 'sha256', '-of', 'json', str(path)],
        timeout=60,
    )
    return [packet['data_hash'] for packet in json.loads(result).get('packets', [])]


@contextlib.contextmanager
def decoded(binary, path, frames):
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([binary, '-v', 'error', '-nostdin', '-i', str(path),
                                    '-map', '0:v:0', '-frames:v', str(frames), '-fps_mode', 'passthrough', '-f', 'rawvideo',
                                    '-pix_fmt', 'rgb24', '-threads', '2', '-'],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors)
        try:
            yield process.stdout
            if process.wait(timeout=60):
                errors.seek(0)
                raise RuntimeError(errors.read(4000).decode(errors='replace'))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()


def finish(args):
    binary = shutil.which('ffmpeg')
    if not binary:
        raise RuntimeError('This validation harness requires FFmpeg.')
    directory = Path(args.directory).resolve()
    run = Path(args.run).resolve()
    source = Path(args.source).resolve()
    manifest = json.loads((run / 'manifest.json').read_text())
    probe = json.loads((directory / 'source/probe.json').read_text())
    video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    rate = Fraction(video['avg_frame_rate'])
    if rate != Fraction(video['r_frame_rate']):
        raise ValueError('Variable-rate source requires timestamp alignment before this experiment.')
    width, height = video['width'], video['height']
    source_count = int(video['nb_frames'])
    generated_count = manifest['generated_frames']
    count = min(source_count, int(Fraction(generated_count, 24) * rate))
    output = run / 'finished'
    output.mkdir(exist_ok=False)
    frames = output / 'frames'
    masks = output / 'masks'
    frames.mkdir()
    masks.mkdir()
    mask_hash = hashlib.sha256()
    for index in range(count):
        # Match time, never stretch a shorter generation over the whole source.
        at = Fraction(index, rate) * 24
        generated_index = min(generated_count - 1, (at.numerator * 2 + at.denominator) // (at.denominator * 2))
        original = image(directory / f'source/frames/{index:05d}.png', 'RGB')
        generated = image(run / f'frames/{generated_index:05d}.png', 'RGB')
        if original.shape != (height, width, 3):
            raise ValueError('Source metadata and decoded frames disagree.')
        silhouettes = [
            image(directory / f'source-mask-{side}/{index:05d}.png', 'L') for side in ('left', 'right')
        ] + [
            image(run / f'target-mask-{side}/{generated_index:05d}.png', 'L') for side in ('left', 'right')
        ]
        mask = subject_mask(silhouettes, margin=2, feather=1)
        mask_hash.update(mask.tobytes())
        result = composite_frame(original, generated, mask)
        Image.fromarray(result).save(frames / f'{index:05d}.png')
        Image.fromarray(mask).save(masks / f'{index:05d}.png')
    master = output / 'replacement-lossless.mkv'
    command = [binary, '-v', 'error', '-nostdin', '-framerate', str(rate), '-start_number', '0',
               '-i', str(frames / '%05d.png'), '-i', str(source), '-map', '0:v:0', '-map', '1:a:0?',
               '-vf', 'setparams=range=full:colorspace=gbr:color_primaries=bt709:color_trc=bt709',
               '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'bgr0', '-threads', '2', '-c:a', 'copy',
               '-colorspace', 'rgb', '-color_range', 'pc', '-color_primaries', 'bt709', '-color_trc', 'bt709',
               '-avoid_negative_ts', 'disabled', '-map_metadata', '-1']
    if count != source_count:
        command += ['-t', str(float(Fraction(count, rate)))]
    subprocess.run([*command, str(master)], check=True, timeout=1800)
    audit = BackgroundAudit(count)
    with decoded(binary, master, count + 1) as stream, decoded(binary, source, count) as original_stream:
        for index in range(count):
            data = stream.read(width * height * 3)
            original_data = original_stream.read(width * height * 3)
            if len(data) != width * height * 3 or len(original_data) != width * height * 3:
                raise ValueError('Delivered master ended early or has incomplete frames.')
            actual = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
            original = np.frombuffer(original_data, dtype=np.uint8).reshape(height, width, 3)
            audit.add(original, actual,
                      image(masks / f'{index:05d}.png', 'L'))
        if stream.read(1):
            raise ValueError('Delivered master has extra frames.')
    original_audio = audio_packets(source) if count == source_count else None
    delivered_audio = audio_packets(master) if count == source_count else None
    report = {
        'seed': manifest['seed'], 'full_clip': count == source_count, 'source_frames': source_count,
        'frames': count, 'source_sha256': digest(source), 'sha256': digest(master),
        'original_audio_packets': len(original_audio) if original_audio is not None else None,
        'audio_unchanged': original_audio == delivered_audio if original_audio is not None else None,
        'edit_masks_sha256': mask_hash.hexdigest(), 'audit': audit.result(),
        'identity_verified': False, 'motion_verified': False, 'visual_review_passed': False,
        'background_scope': 'Original decoded RGB pixels outside the saved edit masks. Newly exposed regions inside original silhouettes are reconstructed.',
        'preview_note': 'The MP4 is a lossy viewing copy; exact-pixel verification applies to the lossless master.',
        'workflow_sha256': manifest['workflow_sha256'],
    }
    (output / 'audit.json').write_text(json.dumps(report, indent=2))
    if (not report['audit']['background_exact'] or not report['audit']['replacement_present_every_frame']
            or report['audio_unchanged'] is False):
        raise ValueError('The delivered master failed preservation or replacement-presence checks.')
    subprocess.run([binary, '-v', 'error', '-nostdin', '-copyts', '-i', str(master), '-map', '0:v:0',
                    '-map', '0:a:0?', '-c:v', 'libx264', '-crf', '16', '-preset', 'medium',
                    '-vf', ('scale=in_range=full:out_range=limited:out_color_matrix=bt709,'
                            'setparams=range=limited:colorspace=bt709:color_primaries=bt709:color_trc=bt709'),
                    '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv',
                    '-pix_fmt', 'yuv420p', '-fps_mode', 'passthrough', '-threads', '2',
                    '-c:a', 'copy', '-movflags', '+faststart',
                    str(output / 'replacement-preview.mp4')], check=True, timeout=1800)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--source', required=True)
    finish(parser.parse_args())
