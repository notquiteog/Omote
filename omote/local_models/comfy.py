"""ComfyUI assets use the same persistent, verified transfer engine as GGUFs.

Weights stay in Omote's managed model directories. ComfyUI opts into these
through its extra-model-paths configuration; no existing install is modified.
Workflows are data, never custom-node installers or executable downloads.
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

from omote.local_models.catalog import GIB, Artifact
from omote.local_models.downloads import Downloads, DownloadStore, ModelError
from omote.resources import monitor

MODEL = Artifact(
    id='sd-turbo', label='SD Turbo · fast images', kind='safetensors', filename='sd_turbo.safetensors',
    url='https://huggingface.co/stabilityai/sd-turbo/resolve/b261bac6fd2cf515557d5d0707481eafa0485ec2/sd_turbo.safetensors',
    size=5214561328, sha256='3f067a1b943cf162f2b8f8588f6cf5824bd5b4c7d1d88d87164b9ca123616549', ram_bytes=8 * GIB,
    note='512 × 512, 1–4 steps. Stability AI non-commercial research license; review the model license before use.')
LIGHTNING = Artifact(
    id='sdxl-lightning-workflow', label='SDXL Lightning · editor workflow', kind='workflow', size=6644,
    url='https://huggingface.co/ByteDance/SDXL-Lightning/resolve/c9a24f48e1c025556787b0c58dd67a091ece2e44/comfyui/sdxl_lightning_workflow_full.json',
    sha256='0483c7e5cfc4b4351f96692b106d12e528aba2ca1c869b2d7f3625264fbb4870', note='Official ByteDance editor graph. Requires separate SDXL and Lightning weights; not the SD Turbo recipe.')


def starter_prompt():
    return {
        '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': MODEL.filename}},
        '2': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'a small red fox in a sunlit forest, watercolor', 'clip': ['1', 1]}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '', 'clip': ['1', 1]}},
        '4': {'class_type': 'EmptyLatentImage', 'inputs': {'width': 512, 'height': 512, 'batch_size': 1}},
        '5': {'class_type': 'KSampler', 'inputs': {'seed': 42, 'steps': 4, 'cfg': 1.0, 'sampler_name': 'euler',
              'scheduler': 'normal', 'denoise': 1.0, 'model': ['1', 0], 'positive': ['2', 0], 'negative': ['3', 0], 'latent_image': ['4', 0]}},
        '6': {'class_type': 'VAEDecode', 'inputs': {'samples': ['5', 0], 'vae': ['1', 2]}},
        '7': {'class_type': 'SaveImage', 'inputs': {'images': ['6', 0], 'filename_prefix': 'Omote'}},
    }


def workflow_data(path):
    from omote.routers.workbench import MAX_GRAPH, validate_prompt_shape

    if path.stat().st_size > MAX_GRAPH:
        raise ModelError('Workflow exceeds 4 MB.')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        json.dumps(value, allow_nan=False)
        if not isinstance(value, dict):
            raise ValueError()
        if 'nodes' in value:
            nodes = value['nodes']
            if not isinstance(nodes, list) or not 1 <= len(nodes) <= 5000 or any(
                    not isinstance(node, dict) or not isinstance(node.get('type'), str) for node in nodes):
                raise ValueError()
            return {'graph': value, 'prompt': None}
        validate_prompt_shape(value)
        # Refuse non-finite values anywhere, even in unrecognised custom inputs.
        json.dumps(value, allow_nan=False)
        return {'graph': None, 'prompt': value}
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ModelError('Use ComfyUI editor JSON or an API-format workflow with 1–5000 nodes.') from error


def validate_file(path, kind):
    if kind == 'workflow':
        workflow_data(path)
        return
    # Read only the bounded JSON header, never tensor data into RAM or pickle.
    try:
        with path.open('rb') as stream:
            length = int.from_bytes(stream.read(8), 'little')
            if not 2 <= length <= min(16 * 1024**2, path.stat().st_size - 8):
                raise ValueError()
            header = json.loads(stream.read(length))
        sizes = {'BOOL': 1, 'U8': 1, 'I8': 1, 'F8_E4M3': 1, 'F8_E5M2': 1, 'F8_E8M0': 1,
                 'I16': 2, 'U16': 2, 'F16': 2, 'BF16': 2, 'I32': 4, 'U32': 4, 'F32': 4, 'I64': 8, 'U64': 8, 'F64': 8}
        ranges = []
        for name, tensor in header.items():
            if name == '__metadata__':
                continue
            start, end = tensor['data_offsets']
            shape = tensor['shape']
            if type(start) is not int or type(end) is not int or not 0 <= start <= end:
                raise ValueError()
            if not isinstance(shape, list) or len(shape) > 64 or any(type(n) is not int or n < 0 for n in shape):
                raise ValueError()
            if math.prod(shape) * sizes[tensor['dtype']] != end - start:
                raise ValueError()
            ranges.append((start, end))
        position = 0
        for start, end in sorted(ranges):
            if start != position:
                raise ValueError()
            position = end
        if not ranges or position != path.stat().st_size - 8 - length:
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, RecursionError) as error:
        raise ModelError('Not a supported safetensors file. Pickle and executable model formats are not accepted.') from error


class ComfyAssets:
    def __init__(self, root: Path, *, allow_loopback=False):
        self.root = root.resolve()
        self.store = DownloadStore(self.root)
        self.downloads = Downloads(self.store, allow_loopback=allow_loopback)
        self.operations = asyncio.Lock()
        for spec in (MODEL, LIGHTNING):
            self.store.add(spec)
        # A JSON document is also valid YAML. Quote paths correctly on Windows.
        self.paths_config = self.root / 'extra_model_paths.yaml'
        categories = Artifact.model_fields['category'].annotation.__args__
        self.paths_config.write_text(json.dumps({'omote': {'base_path': str(self.root / 'models'),
                                                    **{name: name for name in categories}}}, indent=2))

    async def snapshot(self):
        hardware = await monitor.snapshot(self.root)
        rows = []
        for row in self.store.all():
            spec = row['spec']
            final = self.store.final(spec)
            if row['state'] == 'ready' and not final.is_file():
                self.store.update(spec.id, state='error', downloaded=0, error='File is missing. Download it again.')
                row = self.store.get(spec.id)
            part = self.store.partial(spec)
            disk = self.downloads.disk_check(spec)
            errors = [] if disk['ok'] else ['Not enough disk space, including the safety reserve.']
            warnings = []
            if spec.kind == 'safetensors':
                ram = (hardware.get('memory') or {}).get('available_bytes')
                if spec.ram_bytes and ram is not None and ram < spec.ram_bytes:
                    errors.append('Not enough available RAM for this model estimate.')
                warnings.append('Model architecture, GPU memory and required nodes are checked against your ComfyUI connection. Custom model memory requirements vary.')
            downloaded = spec.size if row['state'] == 'ready' else part.stat().st_size if part.exists() else row['downloaded']
            rows.append({**spec.model_dump(), 'state': row['state'], 'downloaded': downloaded, 'error': row['error'],
                         'preflight': {'ok': not errors, 'errors': errors, 'warnings': warnings, 'disk': disk}})
        return {'assets': rows, 'directory': str(self.root / 'models'), 'paths_config': str(self.paths_config),
                'starter': {'id': 'sd-turbo-recipe', 'label': 'SD Turbo · ready-to-use recipe'}}

    async def download(self, ident):
        async with self.operations:
            spec = self.store.get(ident)['spec']
            if spec.kind == 'workflow' and spec.size > 4 * 1024**2:
                raise ModelError('Workflow exceeds 4 MB.')
            row = next(row for row in (await self.snapshot())['assets'] if row['id'] == ident)
            if not row['preflight']['ok']:
                raise ModelError(' '.join(row['preflight']['errors']))
            if self.store.get(ident)['state'] == 'ready' and not self.store.final(spec).is_file():
                self.store.update(ident, state='available', downloaded=0)
            await self.downloads.start(ident)

    async def cancel(self, ident):
        async with self.operations:
            await self.downloads.cancel(ident)

    async def remove(self, ident):
        async with self.operations:
            await self.downloads.cancel(ident)
            spec = self.store.get(ident)['spec']
            self.store.partial(spec).unlink(missing_ok=True)
            self.store.final(spec).unlink(missing_ok=True)
            self.store.update(ident, state='available', downloaded=0, etag='', error='')

    def workflow(self, ident):
        if ident == 'sd-turbo-recipe':
            return {'graph': None, 'prompt': starter_prompt()}, 'SD Turbo'
        row = self.store.get(ident)
        if row['state'] != 'ready' or row['spec'].kind != 'workflow':
            raise ModelError('Download and verify the workflow first.')
        return workflow_data(self.store.final(row['spec'])), row['spec'].label

    async def close(self):
        await self.downloads.close()
        self.store.db.close()
