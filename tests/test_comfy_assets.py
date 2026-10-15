"""ComfyUI asset persistence, content validation and live server compatibility."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from omote.config import config
from omote.local_models.catalog import GIB, Artifact
from omote.local_models.comfy import ComfyAssets, starter_prompt, validate_file, workflow_data
from omote.local_models.downloads import ModelError
from omote.media import workflow
from omote.providers.comfyui import ComfyUIProvider
from omote.routers import comfy_assets, local_models, workbench
from omote.routers.auth import TokenMiddleware
from tests.test_local_models import source as source


def weights():
    data = b'\0\0' * 1024**2
    header = json.dumps({'weight': {'dtype': 'F16', 'shape': [1024**2], 'data_offsets': [0, len(data)]}}).encode()
    return len(header).to_bytes(8, 'little') + header + data


@pytest.mark.parametrize('kind', ['safetensors', 'workflow'])
async def test_real_cancel_resume_survives_restart(tmp_path, source, kind):
    source['data'] = weights() if kind == 'safetensors' else json.dumps({**starter_prompt(), '99': {
        'class_type': 'Note', 'inputs': {'text': 'x' * 1024**2}}}).encode()
    spec = Artifact(id='custom-test', label='Test', kind=kind, url=source['url'], size=len(source['data']),
                    sha256=hashlib.sha256(source['data']).hexdigest())
    first = ComfyAssets(tmp_path, allow_loopback=True)
    first.store.add(spec)
    await first.download(spec.id)
    async with asyncio.timeout(5):
        while not first.store.partial(spec).exists() or first.store.partial(spec).stat().st_size < 65536:  # noqa: ASYNC110 — observe real transfer progress
            await asyncio.sleep(.005)
    await first.cancel(spec.id)
    offset = first.store.partial(spec).stat().st_size
    await first.close()
    second = ComfyAssets(tmp_path, allow_loopback=True)
    try:
        await second.download(spec.id)
        await second.downloads.task
        assert second.store.get(spec.id)['state'] == 'ready'
        assert second.store.final(spec).read_bytes() == source['data']
        assert source['requests'][-1]['Range'] == f'bytes={offset}-'
        assert second.downloads.resumed_from == offset
        await second.remove(spec.id)
        assert not second.store.final(spec).exists()
        assert second.store.get(spec.id)['state'] == 'available'
    finally:
        await second.close()


@pytest.mark.parametrize('kind,body', [('workflow', b'{"hello":"world"}'), ('workflow', b'{"nodes":[]}'),
                                      ('safetensors', b'<html>not a model</html>')])
async def test_verified_checksum_does_not_replace_format_validation(tmp_path, source, kind, body):
    source['data'] = body
    spec = Artifact(id='bad', label='Bad', kind=kind, url=source['url'], size=len(body), sha256=hashlib.sha256(body).hexdigest())
    manager = ComfyAssets(tmp_path, allow_loopback=True)
    try:
        manager.store.add(spec)
        await manager.download(spec.id)
        await manager.downloads.task
        assert manager.store.get(spec.id)['state'] == 'error'
        assert not manager.store.final(spec).exists()
        assert manager.store.get(spec.id)['downloaded'] == 0
    finally:
        await manager.close()


def test_safetensors_offsets_and_workflow_bounds(tmp_path):
    path = tmp_path / 'model'
    path.write_bytes(weights())
    validate_file(path, 'safetensors')
    path.write_bytes(weights()[:-1])
    with pytest.raises(ModelError, match='safetensors'):
        validate_file(path, 'safetensors')
    path.write_text(json.dumps({'nodes': [{'id': 1, 'type': 'KSampler'}]}))
    assert workflow_data(path)['graph']['nodes'][0]['type'] == 'KSampler'
    path.write_bytes(b' ' * (4 * 1024**2 + 1))
    with pytest.raises(ModelError, match='4 MB'):
        workflow_data(path)


async def test_paths_config_is_portable_and_missing_files_recover(tmp_path):
    manager = ComfyAssets(tmp_path)
    try:
        spec = manager.store.get('sd-turbo')['spec']
        manager.store.update(spec.id, state='ready', downloaded=spec.size)
        data = await manager.snapshot()
        assert next(row for row in data['assets'] if row['id'] == spec.id)['state'] == 'error'
        paths = json.loads(manager.paths_config.read_text())['omote']
        assert paths['base_path'] == str(tmp_path / 'models')
        assert paths['loras'] == 'loras'
        assert manager.store.final(spec).parent == tmp_path / 'models' / 'checkpoints'
    finally:
        await manager.close()


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'data_dir', tmp_path)
    monkeypatch.setattr(config, 'auth_token', '')

    @asynccontextmanager
    async def lifespan(app):
        app.state.manager = ComfyAssets(tmp_path / 'managed', allow_loopback=True)
        monkeypatch.setattr(local_models, 'service', SimpleNamespace(comfy=app.state.manager))
        yield
        await app.state.manager.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(TokenMiddleware)
    app.include_router(comfy_assets.router)
    with TestClient(app) as client:
        yield client, app.state.manager


def catalog_for(prompt):
    catalog = {}
    for node in prompt.values():
        rules = {}
        for field in node['inputs']:
            rules[field] = [['sd_turbo.safetensors']] if field == 'ckpt_name' else ['*']
        catalog[node['class_type']] = {'input': {'required': rules}, 'output': ['*', '*', '*']}
    return catalog


def test_live_compatibility_and_use_preserve_recipe_defaults(api, monkeypatch):
    client, _ = api
    catalog = catalog_for(starter_prompt())
    monkeypatch.setattr(workbench, 'connection', lambda _: object())

    async def upstream(impl, path):
        return catalog if path == 'object_info' else {'devices': [{'type': 'cuda', 'vram_free': 12 * GIB}]}

    monkeypatch.setattr(workbench, 'upstream', upstream)
    url = '/api/media/comfyui/assets/sd-turbo-recipe'
    assert client.get(url + '/compatibility?provider=test').json()['valid']
    response = client.post(url + '/use', json={'provider': 'test'})
    assert response.status_code == 200, response.text
    template, overrides = workflow.load(workflow.user_directory() / (response.json()['model'] + '.json'))
    assert overrides['params']['cfg']['default'] == 1
    assert overrides['params']['steps']['default'] == 4
    assert overrides['params']['seed']['default'] == 42
    assert template['2']['inputs']['text'] == '%prompt%'
    catalog['CheckpointLoaderSimple']['input']['required']['ckpt_name'] = [[]]
    assert not client.get(url + '/compatibility?provider=test').json()['valid']
    assert client.post(url + '/use', json={'provider': 'test'}).status_code == 409
    del catalog['KSampler']
    assert any('KSampler' in error for error in client.get(url + '/compatibility?provider=test').json()['errors'])


def test_low_vram_is_not_reported_ready(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(workbench, 'connection', lambda _: object())

    async def upstream(impl, path):
        return catalog_for(starter_prompt()) if path == 'object_info' else {'devices': [{'type': 'cuda', 'vram_free': GIB}]}

    monkeypatch.setattr(workbench, 'upstream', upstream)
    data = client.get('/api/media/comfyui/assets/sd-turbo-recipe/compatibility?provider=test').json()
    assert not data['valid']
    assert any('GPU' in error for error in data['errors'])


def test_api_auth_private_urls_filename_collisions_and_limits(api, monkeypatch):
    client, manager = api
    prefix = '/api/media/comfyui/assets'
    monkeypatch.setattr(config, 'auth_token', 'test-secret')
    assert client.get(prefix).status_code == 401
    monkeypatch.setattr(config, 'auth_token', '')
    body = {'label': 'Test', 'kind': 'workflow', 'url': 'https://127.0.0.1/data', 'size': 2, 'sha256': '0' * 64}
    assert client.post(prefix, json=body).status_code == 400
    body['size'] = 4 * 1024**2 + 1
    assert client.post(prefix, json=body).status_code == 400
    body.update(kind='safetensors', size=16, filename='../escape.safetensors')
    assert client.post(prefix, json=body).status_code == 422
    body.update(filename='sd_turbo.safetensors', url='https://huggingface.co/example')

    async def allowed(_):
        return None

    monkeypatch.setattr(manager.downloads, 'validate_url', allowed)
    assert client.post(prefix, json=body).status_code == 400


def seed_workflow(client, manager, value, *, media_kind='image'):
    data = json.dumps(value).encode()
    spec = Artifact(id='custom-workflow', label='Custom workflow', kind='workflow', media_kind=media_kind,
                    url='https://example.com/workflow.json', size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def store():
        manager.store.add(spec)
        manager.store.final(spec).write_bytes(data)
        manager.store.update(spec.id, state='ready', downloaded=spec.size)

    client.portal.call(store)
    return '/api/media/comfyui/assets/' + spec.id


def test_native_workflow_exports_even_with_missing_nodes_then_saves_to_workbench(api, monkeypatch):
    client, manager = api
    graph = {'nodes': [{'id': 1, 'type': 'CustomSampler'}], 'links': []}
    url = seed_workflow(client, manager, graph)
    monkeypatch.setattr(workbench, 'connection', lambda _: object())
    catalog = {}

    async def upstream(impl, path):
        return catalog

    monkeypatch.setattr(workbench, 'upstream', upstream)
    assert not client.get(url + '/compatibility?provider=test').json()['valid']
    assert client.post(url + '/use', json={'provider': 'test'}).status_code == 409
    response = client.get(url + '/file')
    assert response.json() == graph
    assert 'attachment' in response.headers['content-disposition']
    catalog['CustomSampler'] = {}
    response = client.post(url + '/use', json={'provider': 'test'})
    assert response.status_code == 200, response.text
    assert response.json()['workbench_id']
    assert any('no executable API prompt' in warning for warning in response.json()['warnings'])


def test_custom_video_recipe_keeps_zero_cfg_negative_and_seed(api, monkeypatch):
    client, manager = api
    prompt = starter_prompt()
    prompt['5']['inputs']['cfg'] = 0
    prompt['3']['inputs']['text'] = 'blurry, text, watermark'
    url = seed_workflow(client, manager, prompt, media_kind='video')
    monkeypatch.setattr(workbench, 'connection', lambda _: object())

    async def upstream(impl, path):
        return catalog_for(prompt)

    monkeypatch.setattr(workbench, 'upstream', upstream)
    result = client.post(url + '/use', json={'provider': 'test'})
    assert result.status_code == 200, result.text
    assert result.json()['kind'] == 'video'
    _, sidecar = workflow.load(workflow.user_directory() / (result.json()['model'] + '.json'))
    assert sidecar['params']['cfg'] == {'default': 0, 'minimum': 0}
    assert sidecar['params']['negative']['default'] == 'blurry, text, watermark'
    assert sidecar['params']['seed']['default'] == 42


async def test_user_workflows_override_bundled_and_appear_to_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'data_dir', tmp_path)
    workflow.install(workflow.user_directory(), 'managed-example', starter_prompt(), overrides={'kind': 'image'})
    provider = ComfyUIProvider('http://127.0.0.1:1')
    assert any(row['id'] == 'managed-example' for row in await provider.models())
    assert provider._template_path('managed-example').parent == workflow.user_directory()
    with pytest.raises(FileNotFoundError):
        provider._template_path('../managed-example')
