"""Memory management must work offline, while paused, and past the first page."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omote.config import config
from omote.memory.service import MemoryService
from omote.memory.store import MemoryStore
from omote.providers.base import Modality, ProviderInfo
from omote.providers.registry import ProviderRegistry
from omote.routers import memory


class Embedder:
    calls = 0
    fail = False

    async def embed(self, texts, model, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError('offline')
        return [[1.0, 0.5, 0.25] for _ in texts]


@pytest.fixture
def environment(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / 'memory.db')
    registry = ProviderRegistry()
    embedder = Embedder()
    registry.register(ProviderInfo(id='test', label='Test', modalities={Modality.EMBEDDING}, local=True),
                      {Modality.EMBEDDING: embedder})
    svc = MemoryService(store, registry, model='test-embed')
    monkeypatch.setattr(memory, 'service', svc)
    monkeypatch.setattr(config, 'default_user', 'alice')
    app = FastAPI()
    app.include_router(memory.router)
    with TestClient(app) as client:
        yield client, svc, embedder
    store.close()


def test_paused_memories_can_be_listed_corrected_and_exported_without_network(environment):
    client, svc, embedder = environment
    row = svc.store.add('alice', 'old fact', [1., .5, .25], subject='my project')
    svc.store.add('bob', 'private', [1., .5, .25])
    assert client.get('/api/memory').json()['memories'][0]['subject'] == 'my project'
    response = client.put(f'/api/memory/{row.id}', json={'text': 'corrected fact', 'kind': 'project', 'subject': 'my project'})
    assert response.status_code == 200
    assert not response.json()['indexed']
    assert embedder.calls == 0
    assert svc.store.index_state().pending == 1
    assert svc.store.search('alice', [1., .5, .25], min_score=0) == []
    export = client.get('/api/memory/export')
    assert export.status_code == 200
    assert export.json()['memories'][0]['text'] == 'corrected fact'
    assert len(export.json()['memories']) == 1
    assert 'vec' not in export.json()['memories'][0]
    assert 'attachment' in export.headers['content-disposition']


def test_correction_survives_embedding_failure_and_rebuilds(environment):
    client, svc, embedder = environment
    svc.set_settings('alice', enabled=True, auto_capture=False)
    response = client.post('/api/memory', json={'text': 'before', 'kind': 'project', 'subject': 'demo'})
    ident = response.json()['id']
    embedder.fail = True
    response = client.put(f'/api/memory/{ident}', json={'text': 'after', 'kind': 'project', 'subject': 'demo'})
    assert response.status_code == 200
    assert not response.json()['indexed']
    assert svc.store.get('alice', ident).text == 'after'
    assert not svc.store.search('alice', [1., .5, .25], min_score=0)
    embedder.fail = False
    assert client.post('/api/memory/index/reembed').json()['embedded'] == 1
    assert svc.store.search('alice', [1., .5, .25], min_score=0)[0].text == 'after'


def test_pages_and_export_include_every_memory(environment):
    client, svc, _ = environment
    for i in range(205):
        svc.store.add('alice', f'fact {i}', [1., .5, .25])
    first = client.get('/api/memory?limit=100').json()
    second = client.get('/api/memory?limit=100&offset=100').json()
    third = client.get('/api/memory?limit=100&offset=200').json()
    assert first['total'] == 205
    ids = [m['id'] for page in (first, second, third) for m in page['memories']]
    assert len(set(ids)) == 205
    assert len(client.get('/api/memory/export').json()['memories']) == 205
    assert client.get('/api/memory?limit=-1').status_code == 422


def test_wrong_user_and_invalid_input_are_rejected(environment):
    client, svc, _ = environment
    other = svc.store.add('bob', 'private fact', [1., .5, .25])
    assert client.put(f'/api/memory/{other.id}', json={'text': 'changed'}).status_code == 404
    assert client.delete(f'/api/memory/{other.id}').status_code == 404
    assert client.post('/api/memory', json={'text': '  '}).status_code == 422
    assert client.post('/api/memory', json={'text': 'valid', 'kind': '  '}).status_code == 422
    assert client.post('/api/memory', json={'text': 'valid'}).status_code == 409
    assert svc.store.get('bob', other.id).text == 'private fact'


def test_paused_users_are_not_reembedded(environment):
    client, svc, embedder = environment
    row = svc.store.add('alice', 'private fact', [1., .5, .25])
    svc.store.update('alice', row.id, 'corrected', kind='fact')
    assert client.post('/api/memory/index/reembed').json() == {'embedded': 0, 'failed': 0}
    assert embedder.calls == 0


async def test_revoking_consent_during_embedding_prevents_storage(environment):
    _, svc, embedder = environment
    svc.set_settings('alice', enabled=True, auto_capture=False)
    started, released = asyncio.Event(), asyncio.Event()

    async def slow(texts, model, **kwargs):
        started.set()
        await released.wait()
        return [[1., .5, .25]]

    embedder.embed = slow
    task = asyncio.create_task(svc.remember('alice', 'do not retain'))
    await started.wait()
    svc.set_settings('alice', enabled=False, auto_capture=False)
    released.set()
    assert await task is None
    assert svc.store.count('alice') == 0


async def test_model_change_during_rebuild_leaves_pending_without_looping(environment):
    _, svc, embedder = environment
    svc.set_settings('alice', enabled=True, auto_capture=False)
    memory = await svc.remember('alice', 'original')
    svc.store.update('alice', memory.id, 'corrected', kind='fact')

    async def change_model(texts, model, **kwargs):
        svc.model = 'different-embedder'
        return [[1., .5, .25]]

    embedder.embed = change_model
    assert await asyncio.wait_for(svc.reembed(), timeout=1) == {'embedded': 0, 'failed': 0}
    assert svc.store.index_state().pending == 1


async def test_pausing_during_recall_prevents_returning_memories(environment):
    _, svc, embedder = environment
    svc.set_settings('alice', enabled=True, auto_capture=False)
    await svc.remember('alice', 'a private project detail')

    async def pause(texts, model, **kwargs):
        svc.set_settings('alice', enabled=False, auto_capture=False)
        return [[1., .5, .25]]

    embedder.embed = pause
    result = await svc.recall('alice', 'project')
    assert not result.memories
    assert 'off' in result.reason
