"""Managed weights and workflow documents, without modifying a ComfyUI install."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from omote.local_models.catalog import GIB, Artifact
from omote.local_models.downloads import ModelError
from omote.media import workflow
from omote.routers import local_models, workbench

router = APIRouter(prefix='/api/media/comfyui/assets')


def live():
    return local_models.live().comfy


class NewAsset(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    kind: Literal['safetensors', 'workflow'] = 'safetensors'
    media_kind: Literal['image', 'video'] = 'image'
    category: Literal['checkpoints', 'loras', 'vae', 'text_encoders', 'diffusion_models', 'controlnet', 'clip_vision', 'upscale_models'] = 'checkpoints'
    filename: str = Field(default='', pattern=r'^(?:[a-zA-Z0-9][a-zA-Z0-9._-]{0,149}\.safetensors)?$')
    url: str = Field(max_length=2048)
    size: int = Field(gt=0, le=1024**4)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('')
async def library(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await live().snapshot()


@router.post('')
async def add(body: NewAsset):
    manager = live()
    if body.kind == 'workflow' and body.size > workbench.MAX_GRAPH:
        raise HTTPException(400, 'Workflow exceeds 4 MB.')
    spec = Artifact(id='comfy-' + uuid.uuid4().hex[:16], **body.model_dump())
    try:
        await manager.downloads.validate_url(spec.url)
        async with manager.operations:
            target = manager.store.final(spec)
            if any(manager.store.final(row['spec']) == target for row in manager.store.all()):
                raise ModelError('That model filename is already managed. Choose a different filename.')
            manager.store.add(spec)
        return spec.model_dump()
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error


@router.get('/paths-config')
def paths_config():
    return FileResponse(live().paths_config, media_type='application/yaml', filename='omote-model-paths.yaml')


async def operation(ident, action):
    try:
        await getattr(live(), action)(ident)
        return {'ok': True}
    except KeyError as error:
        raise HTTPException(404, 'No such managed ComfyUI asset.') from error
    except (ModelError, OSError) as error:
        raise HTTPException(409, str(error)) from error


@router.post('/{ident}/download')
async def download(ident: str):
    return await operation(ident, 'download')


@router.post('/{ident}/cancel')
async def cancel(ident: str):
    return await operation(ident, 'cancel')


@router.delete('/{ident}')
async def remove(ident: str):
    return await operation(ident, 'remove')


@router.get('/{ident}/file')
async def export_workflow(ident: str):
    saved_workflow(ident)
    if ident == 'sd-turbo-recipe':
        from fastapi.responses import JSONResponse

        from omote.local_models.comfy import starter_prompt

        return JSONResponse(starter_prompt(), headers={'Content-Disposition': 'attachment; filename="sd-turbo-recipe.json"'})
    spec = live().store.get(ident)['spec']
    return FileResponse(live().store.final(spec), media_type='application/json', filename=f'{ident}.json')


def saved_workflow(ident):
    try:
        return live().workflow(ident)
    except KeyError as error:
        raise HTTPException(404, 'No such managed workflow.') from error
    except (ModelError, OSError) as error:
        raise HTTPException(409, str(error)) from error


async def inspect(ident, provider):
    impl = workbench.connection(provider)
    catalog = await workbench.upstream(impl, 'object_info')
    if not isinstance(catalog, dict):
        raise HTTPException(502, 'ComfyUI returned an invalid node catalog.')
    if ident != 'sd-turbo-recipe':
        try:
            row = live().store.get(ident)
        except KeyError as error:
            raise HTTPException(404, 'No such managed asset.') from error
        spec = row['spec']
        if spec.kind == 'safetensors':
            classes = {'checkpoints': ('CheckpointLoaderSimple', 'ckpt_name'), 'loras': ('LoraLoader', 'lora_name'),
                       'vae': ('VAELoader', 'vae_name'), 'text_encoders': ('CLIPLoader', 'clip_name'),
                       'diffusion_models': ('UNETLoader', 'unet_name'), 'controlnet': ('ControlNetLoader', 'control_net_name'),
                       'clip_vision': ('CLIPVisionLoader', 'clip_name'), 'upscale_models': ('UpscaleModelLoader', 'model_name')}
            node, field = classes[spec.category]
            rule = catalog.get(node, {}).get('input', {}).get('required', {}).get(field, [[]])
            choices = rule[0] if rule and isinstance(rule[0], list) else []
            filename = spec.filename or f'{spec.id}.safetensors'
            found = filename in choices
            return {'valid': found, 'errors': [] if found else [f'{filename} is not visible to this ComfyUI connection. Add the managed model paths and refresh.'],
                    'warnings': ['Visibility confirms the loader can find this file; architecture support is validated by ComfyUI when loading it.']}
    data, _ = saved_workflow(ident)
    if data['prompt'] is not None:
        report = workbench.inspect_prompt(data['prompt'], catalog)
        if ident == 'sd-turbo-recipe':
            stats = await workbench.upstream(impl, 'system_stats')
            devices = stats.get('devices', []) if isinstance(stats, dict) else []
            free = [item['vram_free'] for item in devices if isinstance(item.get('vram_free'), int) and item.get('type') != 'cpu']
            if free and max(free) < 6 * GIB:
                report['errors'].append('SD Turbo estimates at least 6 GiB free GPU memory at 512 × 512. Free GPU memory before running.')
            elif not free:
                report['warnings'].append('GPU memory is unknown or this connection uses CPU. GPU execution is not confirmed.')
            report['valid'] = not report['errors']
        return report
    missing = sorted({node['type'] for node in data['graph']['nodes']} - set(catalog) - {'Note', 'Reroute', 'PrimitiveNode'})
    return {'valid': not missing, 'errors': [f'Missing node type: {name}' for name in missing],
            'warnings': ['Editor JSON has no executable API prompt. Open it in ComfyUI to resolve model selections, then export API format for Studio.']}


@router.get('/{ident}/compatibility')
async def compatibility(ident: str, provider: str):
    return await inspect(ident, provider)


class UseWorkflow(BaseModel):
    provider: str


@router.post('/{ident}/use')
async def use(ident: str, body: UseWorkflow):
    data, label = saved_workflow(ident)
    report = await inspect(ident, body.provider)
    if not report['valid']:
        raise HTTPException(409, ' '.join(report['errors']))
    if data['prompt'] is None:
        result = workbench.create(workbench.Snapshot(name=label, graph=data['graph']))
        return {'workbench_id': result['id'], 'warnings': report['warnings']}
    # Preserve the downloaded recipe's defaults when tokenising it. For Turbo,
    # falling back to a generic CFG=7 or twenty steps changes the actual model.
    template, _ = workflow.tokenise(data['prompt'])
    params = {}
    for node_id, node in template.items():
        for key, value in node['inputs'].items():
            if isinstance(value, str) and value.startswith('%') and value.endswith('%'):
                name = value[1:-1]
                original = data['prompt'][node_id]['inputs'][key]
                if name in workflow.TOKENS and name != 'prompt' and not isinstance(original, (list, dict)):
                    patch = {'default': original}
                    param = workflow.TOKENS[name]
                    if isinstance(original, (int, float)) and not isinstance(original, bool):
                        if param.minimum is not None and original < param.minimum:
                            patch['minimum'] = original
                        if param.maximum is not None and original > param.maximum:
                            patch['maximum'] = original
                    params[name] = patch
    kind = 'image' if ident == 'sd-turbo-recipe' else live().store.get(ident)['spec'].media_kind
    result = workflow.install(workflow.user_directory(), 'managed-' + ident, data['prompt'],
                              overrides={'label': label, 'kind': kind, 'params': params})
    return {'model': result['id'], 'provider': body.provider, 'kind': kind, 'warnings': report['warnings']}
