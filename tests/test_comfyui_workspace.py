from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omote.config import config
from omote.net.transport import Transport
from omote.providers.base import Modality, ProviderInfo
from omote.providers.comfyui import ComfyUIProvider
from omote.providers.registry import ProviderRegistry, RouteSet
from omote.routers import auth, comfyui


@pytest.fixture
def registry(monkeypatch):
    reg = ProviderRegistry()
    monkeypatch.setattr(comfyui, 'registry', reg)
    return reg


def register(registry, *, local=True, tor=False, key='', frontend=''):
    provider = ComfyUIProvider('http://localhost:8188', key, transport=Transport(tor=tor))
    registry.register(
        ProviderInfo(id='comfy', label='My ComfyUI', modalities={Modality.IMAGE, Modality.VIDEO}, local=local),
        {Modality.IMAGE: provider, Modality.VIDEO: provider},
        connection=SimpleNamespace(frontend_url=frontend),
    )


async def test_native_frontend_is_discovered_once_for_both_modalities(registry):
    register(registry)
    rows = (await comfyui.editors())['editors']
    assert len(rows) == 1
    assert rows[0]['url'] == 'http://localhost:8188/'
    assert rows[0]['available']


async def test_gateway_key_is_not_leaked_into_editor_url(registry):
    register(registry, key='secret-key')
    result = await comfyui.editors()
    assert not result['editors'][0]['available']
    assert 'secret-key' not in str(result)
    register(registry, key='secret-key', frontend='https://comfy.example/editor')
    result = await comfyui.editors()
    assert result['editors'][0]['url'] == 'https://comfy.example/editor/'
    assert 'secret-key' not in str(result)


async def test_local_only_and_tor_cannot_be_bypassed_by_the_frame(registry):
    register(registry, local=False)
    registry.set_defaults(RouteSet(local_only=True))
    assert not (await comfyui.editors())['editors'][0]['available']
    registry.set_defaults(RouteSet())
    register(registry, tor=True)
    row = (await comfyui.editors())['editors'][0]
    assert not row['url']
    assert 'Tor' in row['reason']


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///etc/passwd', 'https://user:secret@host/',
                                    'https://host/?token=secret', 'https://host/#secret', 'http://host:bad', '//host'])
def test_editor_urls_cannot_carry_credentials_or_unsafe_schemes(url):
    assert comfyui.editor_url(url) == ''


def test_editor_discovery_is_authenticated(registry, monkeypatch):
    register(registry)
    monkeypatch.setattr(config, 'auth_token', 'test-token')
    app = FastAPI()
    app.add_middleware(auth.TokenMiddleware)
    app.include_router(comfyui.router)
    with TestClient(app) as client:
        assert client.get('/api/media/comfyui/editors').status_code == 401
        response = client.get('/api/media/comfyui/editors', headers={'Authorization': 'Bearer test-token'})
        assert response.status_code == 200
        assert len(response.json()['editors']) == 1
