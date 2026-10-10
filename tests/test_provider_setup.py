"""A verified first connection must work without losing choices or leaking keys."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omote.providers.base import Modality, ProviderInfo, ProviderRefused
from omote.providers.connections import ConnectionStore
from omote.providers.registry import ProviderRegistry, Route
from omote.providers.routing import RouteStore
from omote.routers import providers


@pytest.fixture
def setup_client(tmp_path, monkeypatch):
    live = ProviderRegistry()
    store = ConnectionStore(tmp_path / 'connections.json')
    monkeypatch.setattr(providers, 'registry', live)
    monkeypatch.setattr(providers, 'store', store)
    monkeypatch.setattr(providers.config, 'local_only', False)
    impl = SimpleNamespace(check=AsyncMock(), models=AsyncMock(return_value=[
        {'id': 'plain', 'capabilities': ['completion']},
        {'id': 'worker', 'capabilities': ['completion', 'tools']},
    ]))
    def build(conn):
        return ProviderInfo(id=conn.id, label=conn.label, modalities={Modality.CHAT}, local=conn.local), {Modality.CHAT: impl}
    monkeypatch.setattr(providers.connections, 'build', build)
    app = FastAPI()
    app.include_router(providers.router)
    with TestClient(app) as client:
        yield client, live, store, impl


def connect(client, **extra):
    return client.put('/api/providers/connections', json={
        'host_id': 'openrouter', 'api_key': 'test-secret', 'configure_chat': True, **extra,
    })


def test_key_only_setup_selects_tool_model_and_persists_it(setup_client):
    client, live, store, impl = setup_client
    response = connect(client)
    assert response.status_code == 200
    assert response.json()['chat_model'] == 'worker'
    impl.check.assert_awaited_once()
    assert 'test-secret' not in response.text
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert live.defaults.routes[Modality.CHAT].model == 'worker'
    assert RouteStore(store.path).load()[Modality.CHAT] == Route(provider='openrouter', model='worker')


def test_live_auto_router_is_preferred_but_never_invented(setup_client):
    client, _, _, impl = setup_client
    impl.models.return_value.append({'id': 'openrouter/auto'})
    assert connect(client).json()['chat_model'] == 'openrouter/auto'


def test_bad_key_cannot_replace_a_working_connection(setup_client):
    client, live, store, impl = setup_client
    assert connect(client).status_code == 200
    impl.check.side_effect = ProviderRefused('bad key')
    response = connect(client, api_key='new-secret')
    assert response.status_code == 400
    assert store.get('openrouter').api_key == 'test-secret'
    assert live.defaults.routes[Modality.CHAT].model == 'worker'
    assert 'new-secret' not in response.text


@pytest.mark.parametrize('failure,status', [(TimeoutError(), 504), (RuntimeError('test-secret in a header'), 502)])
def test_network_failure_is_actionable_and_never_exposes_key(setup_client, failure, status):
    client, live, store, impl = setup_client
    impl.check.side_effect = failure
    response = connect(client)
    assert response.status_code == status
    assert 'test-secret' not in response.text
    assert not store.path.exists()
    assert not live.list()


def test_no_models_does_not_save_a_dead_end(setup_client):
    client, _, store, impl = setup_client
    impl.models.return_value = []
    response = connect(client)
    assert response.status_code == 400
    assert 'No chat models' in response.text
    assert not store.path.exists()


def test_local_only_is_respected(setup_client, monkeypatch):
    client, _, store, _ = setup_client
    monkeypatch.setattr(providers.config, 'local_only', True)
    assert connect(client).status_code == 400
    assert not store.path.exists()
    assert connect(client, host_id='ollama').status_code == 200


def test_route_changes_and_automatic_choice_survive_reload(setup_client):
    client, live, store, _ = setup_client
    connect(client)
    changed = client.put('/api/providers/routes', json={'modality': 'chat', 'provider': 'openrouter', 'model': 'plain'})
    assert changed.status_code == 200
    assert RouteStore(store.path).load()[Modality.CHAT].model == 'plain'
    bad = client.put('/api/providers/routes', json={'modality': 'chat', 'provider': 'missing'})
    assert bad.status_code == 400
    assert live.defaults.routes[Modality.CHAT].model == 'plain'
    assert client.put('/api/providers/routes', json={'modality': 'chat', 'provider': ''}).status_code == 200
    assert RouteStore(store.path).load()[Modality.CHAT].provider == ''
    assert Modality.CHAT not in live.defaults.routes


def test_removing_default_connection_does_not_leave_stale_route(setup_client):
    client, live, store, _ = setup_client
    connect(client)
    assert client.delete('/api/providers/connections/openrouter').status_code == 200
    assert RouteStore(store.path).load()[Modality.CHAT].provider == ''
    assert not live.defaults.routes


@pytest.mark.asyncio
async def test_saved_route_is_restored_at_bootstrap(tmp_path):
    from omote.config import Config
    from omote.providers.bootstrap import bootstrap
    cfg = Config()
    cfg.connections_db = tmp_path / 'connections.json'
    cfg.ollama_url = cfg.perch_host = cfg.openai_key = cfg.anthropic_key = cfg.openwebui_url = ''
    saved = Route(provider='saved-provider', model='saved-model')
    RouteStore(cfg.connections_db).put(Modality.CHAT, saved)
    live = ProviderRegistry()
    await bootstrap(cfg, live)
    assert live.defaults.routes[Modality.CHAT] == saved


def test_corrupt_routes_do_not_stop_startup(tmp_path):
    store = RouteStore(tmp_path / 'connections.json')
    store.path.write_text(json.dumps({'unknown': {'provider': 'x'}}))
    assert store.load() == {}


@pytest.mark.asyncio
async def test_automatic_choice_overrides_environment_after_restart(tmp_path):
    from omote.config import Config
    from omote.providers.bootstrap import bootstrap
    cfg = Config()
    cfg.connections_db = tmp_path / 'connections.json'
    cfg.ollama_url = cfg.perch_host = cfg.openai_key = cfg.anthropic_key = cfg.openwebui_url = ''
    cfg.chat_provider = 'previous-provider'
    RouteStore(cfg.connections_db).put(Modality.CHAT, None)
    live = ProviderRegistry()
    await bootstrap(cfg, live)
    assert Modality.CHAT not in live.defaults.routes
