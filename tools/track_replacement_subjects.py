"""Track each generated character before pixel-preserving replacement finishing."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np
from PIL import Image


def node(kind, **inputs):
    return {'class_type': kind, 'inputs': inputs}


async def track(args):
    parsed = urlparse(args.server)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Use the local validation ComfyUI server.')
    run, directory = Path(args.run), Path(args.directory)
    manifest = json.loads((run / 'manifest.json').read_text())
    count = manifest['generated_frames']
    binary = shutil.which('ffmpeg')
    if not binary:
        raise RuntimeError('FFmpeg is required for validation.')
    clip = run / 'generated.mkv'
    if not clip.exists():
        process = await asyncio.create_subprocess_exec(
            binary, '-v', 'error', '-nostdin', '-framerate', '24', '-start_number', '0',
            '-i', str(run / 'frames/%05d.png'), '-frames:v', str(count), '-an', '-c:v', 'ffv1',
            '-level', '3', '-pix_fmt', 'bgr0', '-threads', '2', str(clip),
        )
        try:
            async with asyncio.timeout(300):
                if await process.wait():
                    raise RuntimeError('Could not encode the generated tracking input.')
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    boxes = []
    for side in ('left', 'right'):
        with Image.open(directory / f'source-mask-{side}/00000.png') as mask:
            array = np.array(mask.convert('L')) >= 128
        yy, xx = np.where(array)
        if not len(xx):
            raise ValueError('Missing first-frame source subject.')
        h, w = array.shape
        x, y = max(0, int(xx.min()) - 32), max(0, int(yy.min()) - 32)
        boxes.append({'x': x, 'y': y, 'width': min(w, int(xx.max()) + 33) - x,
                      'height': min(h, int(yy.max()) + 33) - y})
    ident = None
    async with httpx.AsyncClient(base_url=args.server, timeout=120, trust_env=False) as client:
        try:
            queue = (await client.get('/queue')).raise_for_status().json()
            if queue.get('queue_running') or queue.get('queue_pending'):
                raise ValueError('ComfyUI is busy.')
            with clip.open('rb') as stream:
                response = await client.post('/upload/image', files={'image': (f'{run.name}.mkv', stream)})
            response.raise_for_status()
            filename = response.json()['name']
            g = {
                '1': node('LoadVideo', file=filename),
                '2': node('GetVideoComponents', video=['1', 0]),
                '3': node('CheckpointLoaderSimple', ckpt_name='sam3.1_multiplex_fp16.safetensors'),
                '4': node('ImageFromBatch', image=['2', 0], batch_index=0, length=1),
                '5': node('PrimitiveBoundingBox', **boxes[0]),
                '6': node('PrimitiveBoundingBox', **boxes[1]),
                '7': node('SAM3_Detect', model=['3', 0], image=['4', 0], bboxes=['5', 0],
                          threshold=.5, refine_iterations=3, individual_masks=True),
                '8': node('SAM3_Detect', model=['3', 0], image=['4', 0], bboxes=['6', 0],
                          threshold=.5, refine_iterations=3, individual_masks=True),
                '9': node('BatchMasksNode', **{'masks.mask0': ['7', 0], 'masks.mask1': ['8', 0]}),
                '10': node('SAM3_VideoTrack', model=['3', 0], images=['2', 0], initial_mask=['9', 0],
                           detection_threshold=.5, max_objects=2, detect_interval=1),
                '11': node('SAM3_TrackToMask', track_data=['10', 0], object_indices='0'),
                '12': node('SAM3_TrackToMask', track_data=['10', 0], object_indices='1'),
                '13': node('MaskToImage', mask=['11', 0]),
                '14': node('MaskToImage', mask=['12', 0]),
                '15': node('SaveImage', images=['13', 0], filename_prefix=f'replacement/{run.name}/left'),
                '16': node('SaveImage', images=['14', 0], filename_prefix=f'replacement/{run.name}/right'),
            }
            (run / 'tracking-workflow.json').write_text(json.dumps(g, indent=2))
            response = await client.post('/prompt', json={'prompt': g, 'client_id': 'omote-replacement-tracking'})
            response.raise_for_status()
            ident = response.json()['prompt_id']
            start = time.monotonic()
            while time.monotonic() - start < 1800:
                history = (await client.get('/history/' + ident)).raise_for_status().json().get(ident)
                if history:
                    (run / 'tracking-history.json').write_text(json.dumps(history, indent=2))
                    if history.get('status', {}).get('status_str') != 'success':
                        raise RuntimeError('Generated-subject tracking failed; see tracking-history.json.')
                    for key, side in [('15', 'left'), ('16', 'right')]:
                        images = history['outputs'][key]['images']
                        if len(images) != count:
                            raise RuntimeError('Generated tracking returned the wrong frame count.')
                        target = run / f'target-mask-{side}'
                        target.mkdir(exist_ok=False)
                        for index, item in enumerate(images):
                            response = await client.get('/view', params=item)
                            response.raise_for_status()
                            (target / f'{index:05d}.png').write_bytes(response.content)
                    await client.post('/free', json={'unload_models': True, 'free_memory': True})
                    print(f'Tracked two generated subjects across {count} frames.', flush=True)
                    return
                print(f'Tracking generated subjects: {time.monotonic() - start:.0f}s', flush=True)
                await asyncio.sleep(15)
            raise TimeoutError('Generated tracking timed out.')
        except BaseException:
            if ident:
                try:
                    queue = (await client.get('/queue')).json()
                    if any(row[1] == ident for row in queue.get('queue_running', [])):
                        await client.post('/interrupt', json={'prompt_id': ident})
                    await client.post('/queue', json={'delete': [ident]})
                except (httpx.HTTPError, ValueError):
                    pass
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--directory', required=True)
    asyncio.run(track(parser.parse_args()))
