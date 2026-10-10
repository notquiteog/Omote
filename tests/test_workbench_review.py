"""Regression cases found in the independent workflow/import review."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from omote.config import config
from omote.routers import creative, workbench
from tests.test_workbench import GRAPH, PREFIX, PROMPT, Response, history, save, start
from tests.test_workbench import bench as bench


def test_historical_version_retains_name_project_and_run_snapshot(bench):
    client, _socket, *_ = bench
    original = save(client, name='Original name', project='Original project')
    response = client.post(f"{PREFIX}/workflows/{original['id']}/versions", json={
        'name': 'New name', 'project': 'New project', 'prompt': PROMPT, 'base_revision': 1,
    })
    assert response.status_code == 200
    old = client.get(f"{PREFIX}/workflows/{original['id']}?revision=1").json()
    assert (old['name'], old['project']) == ('Original name', 'Original project')
    newest = client.get(f"{PREFIX}/workflows/{original['id']}").json()
    assert (newest['name'], newest['project']) == ('New name', 'New project')
    run = client.post(f'{PREFIX}/runs', json={
        'provider': 'comfy', 'workflow_id': original['id'], 'revision': 1,
    }).json()
    with workbench.database() as db:
        saved = json.loads(workbench.owned_run(db, run['id'])['snapshot'])
    assert (saved['name'], saved['project'], saved['revision']) == ('Original name', 'Original project', 1)


def test_legacy_database_migration_preserves_all_work_and_backfills_available_metadata(bench):
    client, *_ = bench
    graph, prompt = json.dumps(GRAPH), json.dumps(PROMPT)
    run_snapshot = json.dumps({'name': 'Original run name', 'project': 'Original run project'})
    with sqlite3.connect(config.data_dir / 'workbench.db') as db:
        db.executescript("""
            CREATE TABLE workflows(id TEXT PRIMARY KEY,user TEXT,name TEXT,project TEXT,updated REAL);
            CREATE TABLE versions(workflow_id TEXT REFERENCES workflows(id) ON DELETE CASCADE,
                revision INTEGER,created REAL,notes TEXT,graph TEXT,prompt TEXT,PRIMARY KEY(workflow_id,revision));
            CREATE TABLE runs(id TEXT PRIMARY KEY,user TEXT,provider TEXT,prompt_id TEXT,
                workflow_id TEXT,revision INTEGER,created REAL,snapshot TEXT,imports TEXT DEFAULT '{}');
        """)
        db.execute('INSERT INTO workflows VALUES(?,?,?,?,?)', ('legacy', 'alice', 'Current legacy name', 'Current legacy project', 1))
        db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?)', ('legacy', 1, 1, 'Keep these notes', graph, prompt))
        db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)',
                   ('legacy-run', 'alice', 'comfy', 'prompt', 'legacy', 1, 1, run_snapshot, '{"k":"asset-kept"}'))
    response = client.get(f'{PREFIX}/workflows/legacy?revision=1')
    assert response.status_code == 200
    result = response.json()
    assert (result['name'], result['project']) == ('Current legacy name', 'Current legacy project')
    assert result['graph'] == GRAPH and result['prompt'] == PROMPT
    assert result['notes'] == 'Keep these notes'
    for _ in range(2):
        with workbench.database() as db:
            old = db.execute('SELECT * FROM versions').fetchone()
            assert (old['graph'], old['prompt']) == (graph, prompt)
            run = workbench.owned_run(db, 'legacy-run')
            assert run['snapshot'] == run_snapshot
            assert run['imports'] == '{"k":"asset-kept"}'
            assert run['import_lease'] is None and run['import_until'] is None
            assert db.execute('PRAGMA user_version').fetchone()[0] == 1
    assert client.post(f'{PREFIX}/workflows/legacy/versions', json={
        'name': 'Next', 'project': 'Next project', 'prompt': PROMPT, 'base_revision': 1,
    }).status_code == 200
    assert client.get(f'{PREFIX}/workflows/legacy?revision=1').json()['name'] == 'Current legacy name'


def test_huge_json_integer_returns_validation_error_instead_of_500(bench):
    client, *_ = bench
    prompt = {'1': {'class_type': 'Source', 'inputs': {'seed': 10**400, 'mode': 'fast'}}}
    response = client.post(f'{PREFIX}/connections/comfy/validate', json={'prompt': prompt})
    assert response.status_code == 200
    assert not response.json()['valid']
    assert 'finite number' in response.json()['errors'][0]


def test_comfy_error_keeps_actionable_node_validation_details(bench):
    client, socket, *_ = bench
    socket.routes[('GET', '/object_info')] = Response({
        'error': {'type': 'prompt_outputs_failed_validation', 'message': 'Prompt outputs failed validation'},
        'node_errors': {'17': {'errors': [{'message': 'Model file is missing', 'details': 'checkpoint_name'}]}},
    }, 400)
    response = client.get(f'{PREFIX}/connections/comfy/nodes')
    assert response.status_code == 502
    assert 'Model file is missing' in response.json()['detail']
    assert 'checkpoint_name' in response.json()['detail']
    assert len(response.json()['detail']) < 4100
    assert 'secret-server-key' not in response.text


def slow_import(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    created = []

    class Download(Response):
        async def iter_chunked(self, _size):
            started.set()
            await release.wait()
            yield b'fixture-image'

    class Socket:
        def session(self, _timeout):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def get(self, *_args, **_kwargs):
            return Download(b'')

    impl = SimpleNamespace(transport=Socket(), base_url='http://fixture.invalid', _headers=lambda: {})

    async def state(_row):
        return impl, {'outputs': {'1': {'images': [{'filename': 'result.png'}]}}}, 'done'

    class Library:
        def add_asset(self, user, _data, **kwargs):
            result = {'id': f'asset-{len(created)+1}', 'user': user, **kwargs}
            created.append(result)
            return result

    monkeypatch.setattr(workbench, 'run_state', state)
    monkeypatch.setattr(creative, 'store', Library())
    return started, release, created


async def test_concurrent_output_import_has_one_owner_and_one_asset(bench, monkeypatch):
    client, *_ = bench
    _, run = start(client)
    started, release, created = slow_import(monkeypatch)
    first = asyncio.create_task(workbench.import_outputs(run['id']))
    await asyncio.wait_for(started.wait(), 2)
    try:
        with pytest.raises(HTTPException, match='in progress') as rejected:
            await workbench.import_outputs(run['id'])
        assert rejected.value.status_code == 409
    finally:
        release.set()
    assert (await first)['asset_ids'] == ['asset-1']
    assert (await workbench.import_outputs(run['id']))['asset_ids'] == ['asset-1']
    assert len(created) == 1
    assert created[0]['user'] == 'alice'
    with workbench.database() as db:
        row = workbench.owned_run(db, run['id'])
    assert row['import_lease'] is None
    assert list(json.loads(row['imports']).values()) == ['asset-1']


async def test_interrupted_owner_cannot_add_an_asset_after_lease_replacement(bench, monkeypatch):
    client, *_ = bench
    _, run = start(client)
    started, release, created = slow_import(monkeypatch)
    first = asyncio.create_task(workbench.import_outputs(run['id']))
    await asyncio.wait_for(started.wait(), 2)
    with workbench.database() as db:
        db.execute('UPDATE runs SET import_lease=?,import_until=? WHERE id=?', ('new-worker', time.time()+300, run['id']))
    release.set()
    with pytest.raises(HTTPException, match='interrupted'):
        await first
    assert created == []
    with workbench.database() as db:
        assert workbench.owned_run(db, run['id'])['import_lease'] == 'new-worker'


async def test_cancellation_releases_import_lease_and_retry_can_complete(bench, monkeypatch):
    client, *_ = bench
    _, run = start(client)
    started, release, created = slow_import(monkeypatch)
    first = asyncio.create_task(workbench.import_outputs(run['id']))
    await asyncio.wait_for(started.wait(), 2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    with workbench.database() as db:
        assert workbench.owned_run(db, run['id'])['import_lease'] is None
    assert created == []
    release.set()
    assert (await workbench.import_outputs(run['id']))['asset_ids'] == ['asset-1']


def test_expired_import_claim_is_recoverable_after_worker_crash(bench, monkeypatch):
    client, socket, *_ = bench
    _, run = start(client)
    history(socket)
    socket.routes[('GET', '/view')] = Response(b'fixture-image')

    class Library:
        def add_asset(self, *_args, **_kwargs):
            return {'id': 'recovered-asset'}

    monkeypatch.setattr(creative, 'store', Library())
    with workbench.database() as db:
        db.execute('UPDATE runs SET import_lease=?,import_until=? WHERE id=?', ('dead-worker', time.time()-1, run['id']))
    response = client.post(f"{PREFIX}/runs/{run['id']}/import")
    assert response.status_code == 200
    assert response.json()['asset_ids'] == ['recovered-asset']
