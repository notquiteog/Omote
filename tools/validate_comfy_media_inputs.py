"""Exercise real ComfyUI media inputs without downloading or loading any models.

Run with Omote's Python environment and an already-running ComfyUI:
    python tools/validate_comfy_media_inputs.py --url http://127.0.0.1:8188 --output data/validation/comfy-inputs

Uses native LoadImage/LoadVideo/LoadAudio and save nodes. Three independent runs
check distinct character images, decoded frame counts and audible output. This
is transport/codec validation, not a character-replacement or lip-sync benchmark.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops  # noqa: E402

from omote.media.edit import ffmpeg  # noqa: E402
from omote.media.workflow import install  # noqa: E402
from omote.providers.comfyui import ComfyUIProvider  # noqa: E402


def encode(binary, *args):
    return subprocess.run([binary, '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)],
                          check=True, capture_output=True, timeout=60).stdout


async def validate(url: str, directory: Path):
    binary = ffmpeg()
    if not binary:
        raise RuntimeError('Set up video export in Studio first: FFmpeg is required.')
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    first, second = directory / 'first.png', directory / 'second.jpg'
    Image.new('RGB', (96, 64), (220, 30, 40)).save(first)
    Image.new('RGB', (96, 64), (20, 60, 220)).save(second)
    source, speech = directory / 'source.mp4', directory / 'audio.wav'
    encode(binary, '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=24:duration=1',
           '-c:v', 'libx264', '-pix_fmt', 'yuv420p', source)
    with wave.open(str(speech), 'wb') as wav:
        wav.setparams((1, 2, 24000, 24000, 'NONE', 'not compressed'))
        wav.writeframes(b''.join(struct.pack('<h', round(8000 * math.sin(2 * math.pi * 440 * i / 24000)))
                                 for i in range(24000)))
    graph = {
        '1': {'class_type': 'LoadImage', 'inputs': {'image': first.name}},
        '2': {'class_type': 'LoadImage', 'inputs': {'image': second.name}},
        '3': {'class_type': 'LoadVideo', 'inputs': {'file': source.name}},
        '4': {'class_type': 'LoadAudio', 'inputs': {'audio': speech.name}},
        '5': {'class_type': 'GetVideoComponents', 'inputs': {'video': ['3', 0]}},
        '6': {'class_type': 'CreateVideo', 'inputs': {'images': ['5', 0], 'fps': ['5', 2], 'audio': ['4', 0]}},
        '7': {'class_type': 'SaveVideo', 'inputs': {'video': ['6', 0], 'filename_prefix': 'omote-io/video', 'format': 'auto'}},
        '8': {'class_type': 'SaveImage', 'inputs': {'images': ['1', 0], 'filename_prefix': 'omote-io/first'}},
        '9': {'class_type': 'SaveImage', 'inputs': {'images': ['2', 0], 'filename_prefix': 'omote-io/second'}},
    }
    installed = install(directory / 'workflows', 'io-validation', graph)

    class ValidationProvider(ComfyUIProvider):
        def _template_path(self, model):
            if model != 'io-validation':
                raise ValueError(model)
            return Path(installed['path'])

    provider = ValidationProvider(url, timeout=90, poll_interval=0.2)
    await provider.check()
    report = {'scope': 'native media upload, decoding and collection; no generation model', 'runs': []}
    for seed in (17, 29, 41):
        run_dir = directory / f'run-{seed}'
        run_dir.mkdir(exist_ok=True)
        async with asyncio.timeout(120):
            results = await provider.generate('', model='io-validation', seed=seed,
                                              image=first.read_bytes(), image_2=second.read_bytes(),
                                              video=source.read_bytes(), audio=speech.read_bytes())
        checked = {}
        for result in results:
            name = Path(result.meta['filename']).name
            dest = run_dir / name
            dest.write_bytes(result.data)
            if name.startswith(('first_', 'second_')):
                original = first if name.startswith('first_') else second
                with Image.open(original) as expected, Image.open(dest) as actual:
                    difference = ImageChops.difference(expected.convert('RGB'), actual.convert('RGB'))
                    error = max(channel[1] for channel in difference.getextrema())
                assert error <= 1, f'{name}: image changed or was bound to the wrong input'
                checked[original.stem] = {'max_pixel_error': error, 'file': str(dest)}
            elif dest.suffix == '.mp4':
                frames = encode(binary, '-i', dest, '-map', '0:v:0', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1')
                count = len(frames) / (96 * 64 * 3)
                assert count == 24, f'Expected 24 frames, received {count}'
                pcm = encode(binary, '-i', dest, '-map', '0:a:0', '-ac', 1, '-ar', 24000, '-f', 's16le', 'pipe:1')
                samples = struct.unpack(f'<{len(pcm) // 2}h', pcm)
                seconds = len(samples) / 24000
                rms = math.sqrt(sum(s * s for s in samples) / len(samples))
                crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:], strict=False))
                frequency = crossings / seconds
                assert 0.99 <= seconds <= 1.1 and rms > 1000 and 400 < frequency < 480
                checked['video'] = {'frames': count, 'audio_seconds': seconds, 'audio_rms': rms,
                                    'audio_hz': frequency, 'file': str(dest)}
        assert set(checked) == {'first', 'second', 'video'}, f'Missing or unexpected outputs: {checked}'
        report['runs'].append({'seed': seed, 'passed': True, 'checks': checked})
        (directory / 'report.json').write_text(json.dumps(report, indent=2))
        print(f'Run {seed}: two distinct images, 24 video frames, audible 440 Hz track passed.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(validate(args.url, args.output))
