"""Re-run a pinned local replacement experiment; never counts previews as success.

Run with the repository's dev Python environment. Inputs are a reviewed pair of
character images plus source, pose and inpainting-mask videos at 24 fps. These
are produced by the native ComfyUI tracking/pose workflows saved with the test.
No external generation provider is contacted.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx


def node(kind, **inputs):
    return {'class_type': kind, 'inputs': inputs}


def graph(files, *, seed, frames=124, strength=1.0, steps=25, attention='pytorch'):
    prompt = (
        'A continuous realistic smartphone video in the same living room. Exactly two adult women aged 25 '
        'perform the two-person synchronized dance following the supplied pose control precisely. '
        'The woman on the LEFT is the golden-blonde woman from <Picture 1>; keep her face, wavy hair, '
        'feminine athletic body and fitted burgundy trouser suit with ivory blouse consistent. '
        'The woman on the RIGHT is the platinum-blonde woman from <Picture 2>; keep her distinct face, '
        'high ponytail, feminine athletic body and fitted burgundy trouser suit with ivory blouse consistent. '
        'Both women have socks on their feet. Their positions, hand gestures, footwork, body motion and '
        'timing match the provided poses exactly. Replace the original people completely. '
        'Preserve the camera viewpoint, room, sofa, staircase, pillows, blinds, carpet, sunlight and '
        'shadows of the source video. Realistic skin texture, natural hair movement and consistent '
        'window lighting. No additional people, no changes in identity, no camera movement, no text.'
    )
    workflow = {
        '1': node('UNETLoader', unet_name='minimax_h3_ref2va_pruned_bf16.safetensors', weight_dtype='default'),
        '2': node('CLIPLoader', clip_name='qwen3vl_32b_minimax_h3_bf16.safetensors', type='minimax', device='default'),
        '3': node('VAELoader', vae_name='minimax_h3_video_vae_fp16.safetensors'),
        '4': node('LoadImage', image=files['left']),
        '5': node('LoadImage', image=files['right']),
        '6': node('MiniMaxH3ReferenceToVideo', clip=['2', 0], vae=['3', 0], prompt=prompt,
                  width=576, height=1024, length=frames, ref_image_size='max',
                  **{'ref_images.ref_image_0': ['4', 0], 'ref_images.ref_image_1': ['5', 0]}),
        '7': node('ModelPatchLoader', name='minimax_h3_fun_controlnet_union_2.0_pruned_bf16.safetensors'),
        '8': node('LoadVideo', file=files['source']),
        '9': node('GetVideoComponents', video=['8', 0]),
        '10': node('LoadVideo', file=files['mask']),
        '11': node('GetVideoComponents', video=['10', 0]),
        '12': node('ImageToMask', image=['11', 0], channel='red'),
        '13': node('LoadVideo', file=files['pose']),
        '14': node('GetVideoComponents', video=['13', 0]),
        '15': node('MiniMaxH3FunControlNetApply', model=['1', 0], model_patch=['7', 0], vae=['3', 0],
                   strength=strength, start_percent=0.0, end_percent=1.0,
                   control_video=['14', 0], mask=['12', 0], source_video=['9', 0]),
        '16': node('RandomNoise', noise_seed=seed),
        '17': node('KSamplerSelect', sampler_name='res_multistep'),
        '18': node('BasicScheduler', model=['15', 0], scheduler='simple', steps=steps, denoise=1.0),
        '19': node('BasicGuider', model=['15', 0], conditioning=['6', 0]),
        '20': node('SamplerCustomAdvanced', noise=['16', 0], guider=['19', 0], sampler=['17', 0],
                   sigmas=['18', 0], latent_image=['6', 1]),
        '21': node('VAEDecode', samples=['20', 0], vae=['3', 0]),
        '22': node('SaveImage', images=['21', 0], filename_prefix=f'replacement/seed-{seed}'),
    }
    if attention == 'kitchen':
        workflow['23'] = node('ModelAttentionBackend', model=['15', 0], attention='comfy kitchen attention')
        for key in ('18', '19'):
            workflow[key]['inputs']['model'] = ['23', 0]
    elif attention != 'pytorch':
        raise ValueError('Unknown attention backend.')
    return workflow


async def render(args):
    parsed = urlparse(args.server)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('This validation harness only uses the local ComfyUI server.')
    directory = await asyncio.to_thread(Path(args.directory).resolve)
    suffix = '-kitchen' if args.attention == 'kitchen' else ''
    output = directory / f'run-{args.seed}-{args.frames}{suffix}'
    output.mkdir(exist_ok=False)
    inputs = {'left': directory / 'character-left.png', 'right': directory / 'character-right.png',
              **{key: directory / f'{key}-24-{args.frames}.mkv' for key in ('source', 'mask', 'pose')}}
    if any(not path.is_file() for path in inputs.values()):
        raise ValueError('Prepare all character, source, mask and pose inputs first.')
    manifest = {'seed': args.seed, 'frames': args.frames, 'full_clip': args.frames == 362,
                'steps': args.steps, 'control_strength': args.strength, 'attention': args.attention,
                'status': 'preparing',
                'inputs': {key: {'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                           for key, path in inputs.items()}}
    for name in ('models.json', 'pose-model.json', 'detector-model.json'):
        if (directory / name).exists():
            manifest[name] = json.loads((directory / name).read_text())

    def save():
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))

    save()
    ident = None
    async with httpx.AsyncClient(base_url=args.server, timeout=120, trust_env=False) as client:
        try:
            queue = (await client.get('/queue')).raise_for_status().json()
            if queue.get('queue_running') or queue.get('queue_pending'):
                raise ValueError('ComfyUI is busy. Finish its current work before the validation render.')
            uploaded = {}
            for name, path in inputs.items():
                with path.open('rb') as stream:
                    response = await client.post('/upload/image', files={'image': (path.name, stream)})
                response.raise_for_status()
                item = response.json()
                uploaded[name] = '/'.join(filter(None, (item.get('subfolder'), item['name'])))
            workflow = graph(uploaded, seed=args.seed, frames=args.frames, strength=args.strength,
                             steps=args.steps, attention=args.attention)
            encoded = json.dumps(workflow, sort_keys=True).encode()
            manifest['workflow_sha256'] = hashlib.sha256(encoded).hexdigest()
            (output / 'workflow.json').write_text(json.dumps(workflow, indent=2))
            response = await client.post('/prompt', json={'prompt': workflow, 'client_id': 'omote-replacement-validation'})
            if response.status_code != 200:
                (output / 'validation-error.json').write_text(response.text)
            response.raise_for_status()
            ident = response.json()['prompt_id']
            manifest.update(status='rendering', prompt_id=ident, started_at=time.time())
            save()
            started = time.monotonic()
            while time.monotonic() - started < args.timeout:
                response = await client.get('/history/' + ident)
                response.raise_for_status()
                history = response.json().get(ident)
                if history:
                    (output / 'history.json').write_text(json.dumps(history, indent=2))
                    if history.get('status', {}).get('status_str') != 'success':
                        raise RuntimeError('ComfyUI could not render this workflow. See history.json.')
                    images = history['outputs']['22']['images']
                    if len(images) != args.frames:
                        raise RuntimeError(f'Expected {args.frames} generated frames; received {len(images)}.')
                    target = output / 'frames'
                    target.mkdir()
                    for index, image in enumerate(images):
                        response = await client.get('/view', params=image)
                        response.raise_for_status()
                        (target / f'{index:05d}.png').write_bytes(response.content)
                    manifest.update(status='rendered_unreviewed', elapsed=round(time.monotonic() - started),
                                    generated_frames=len(images))
                    save()
                    await client.post('/free', json={'unload_models': True, 'free_memory': True})
                    print(json.dumps({'status': manifest['status'], 'directory': str(output),
                                      'elapsed': manifest['elapsed']}), flush=True)
                    return
                print(json.dumps({'status': 'rendering', 'seed': args.seed,
                                  'elapsed': round(time.monotonic() - started)}), flush=True)
                await asyncio.sleep(20)
            raise TimeoutError('Validation render exceeded its configured timeout.')
        except BaseException as error:
            manifest.update(status='failed', error=f'{type(error).__name__}: {error}')
            save()
            if ident:
                # Interrupt only our own currently running prompt, never a later user's.
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
    parser.add_argument('--directory', required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--frames', type=int, choices=(124, 362), default=124)
    parser.add_argument('--steps', type=int, default=25)
    parser.add_argument('--strength', type=float, default=1.0)
    parser.add_argument('--attention', choices=('pytorch', 'kitchen'), default='pytorch')
    parser.add_argument('--timeout', type=int, default=10800)
    asyncio.run(render(parser.parse_args()))
