"""Schedule persistence, real agent approvals, catch-up, and civil-time behavior."""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omote.agent.approval import Mode
from omote.agent.runtime import build_session
from omote.providers.base import StreamDone, StreamText, StreamToolUse
from omote.routers import schedules
from omote.scheduling import Schedule, Scheduler, ScheduleStore, next_time
from tests.test_agent import ScriptedProvider


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


def spec(tmp_path, **fields):
    return Schedule(name='Project check', prompt='Inspect the project and report.', root=str(tmp_path), **fields)


@pytest.mark.parametrize(
    'zone,after,want',
    [
        ('Asia/Tokyo', '2026-10-09T23:00:00+00:00', '2026-10-10T00:00:00+00:00'),
        ('Asia/Kolkata', '2026-10-09T00:00:00+00:00', '2026-10-09T03:30:00+00:00'),
        ('Asia/Shanghai', '2026-10-09T00:00:00+00:00', '2026-10-09T01:00:00+00:00'),
        ('Europe/Paris', '2026-10-09T00:00:00+00:00', '2026-10-09T07:00:00+00:00'),
    ],
)
def test_daily_uses_chosen_timezone(tmp_path, zone, after, want):
    assert next_time(spec(tmp_path, timezone=zone), epoch(after)) == epoch(want)


def test_dst_missing_and_repeated_times_execute_once(tmp_path):
    spring = spec(tmp_path, timezone='America/New_York', at='02:30')
    assert next_time(spring, epoch('2026-03-08T00:00:00-05:00')) == epoch('2026-03-08T03:30:00-04:00')
    fall = spec(tmp_path, timezone='America/New_York', at='01:30')
    first = next_time(fall, epoch('2026-11-01T00:00:00-04:00'))
    assert first == epoch('2026-11-01T01:30:00-04:00')
    assert next_time(fall, first) == epoch('2026-11-02T01:30:00-05:00')


def test_weekdays_interval_once_and_manual(tmp_path):
    friday = epoch('2026-10-09T12:00:00+00:00')
    assert next_time(spec(tmp_path, cadence='weekly', weekdays=[0]), friday) == epoch('2026-10-12T09:00:00+00:00')
    assert next_time(spec(tmp_path, cadence='interval', interval_minutes=15), friday) == friday + 900
    assert next_time(spec(tmp_path, cadence='manual'), friday) is None
    assert next_time(spec(tmp_path, enabled=False), friday) is None
    once = spec(tmp_path, cadence='once', start_at='2026-10-10T09:00:00+09:00')
    assert next_time(once, friday) == epoch('2026-10-10T00:00:00+00:00')
    assert next_time(once, epoch('2026-10-11T00:00:00Z')) is None


@pytest.mark.parametrize(
    'fields',
    [
        {'timezone': 'unknown/zone'},
        {'at': '25:00'},
        {'at': '9:00'},
        {'weekdays': [8]},
        {'interval_minutes': 0},
        {'cadence': 'once'},
        {'cadence': 'once', 'start_at': '2026-10-10T09:00'},
        {'mode': 'unrestricted'},
        {'prompt': ' '},
        {'max_runtime_minutes': 0},
    ],
)
def test_bad_schedule_is_rejected(tmp_path, fields):
    with pytest.raises(ValueError):
        Schedule(**{**spec(tmp_path).model_dump(), **fields})


def test_persistence_claim_and_restart_are_not_replayed(tmp_path):
    path = tmp_path / 'schedules.db'
    store = ScheduleStore(path)
    job = store.save(spec(tmp_path, cadence='interval'), now=1000)
    run = store.claim(job['id'], now=10000)
    assert store.get(job['id'])['next_run'] == 13600
    with pytest.raises(ValueError, match='already running'):
        store.claim(job['id'], manual=True)
    assert len(store.runs()) == 1
    store.close()
    store = ScheduleStore(path)
    store.recover()
    assert store.run(run['id'])['status'] == 'interrupted'
    assert not store.get(job['id'])['spec']['enabled']
    assert store.get(job['id'])['next_run'] is None
    store.close()


def test_edit_keeps_due_time_pause_does_not_kill_run_and_delete_requires_stop(tmp_path):
    store = ScheduleStore(tmp_path / 'schedules.db')
    original = spec(tmp_path, cadence='interval')
    job = store.save(original, now=1000)
    edited = store.save(original.model_copy(update={'name': 'Updated'}), job['id'], now=2000)
    assert edited['next_run'] == job['next_run']
    run = store.claim(job['id'], manual=True, now=2000)
    store.save(original.model_copy(update={'enabled': False}), job['id'], now=3000)
    assert store.run(run['id'])['status'] == 'starting'
    with pytest.raises(ValueError, match='Stop'):
        store.delete(job['id'])
    store.update_run(run['id'], status='completed')
    store.delete(job['id'])
    assert not store.list() and not store.runs()
    store.close()


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():  # noqa: ASYNC110 — bounded condition polling in tests
            await asyncio.sleep(0.01)


async def test_real_agent_run_waits_for_approval_then_records_output(tmp_path):
    store = ScheduleStore(tmp_path / 'schedules.db')
    provider = ScriptedProvider(
        [
            [
                StreamToolUse(id='write', name='write_file', input={'path': 'result.txt', 'content': 'finished'}),
                StreamDone(stop_reason='tool_use'),
            ],
            [StreamText(text='Result is ready.'), StreamDone()],
        ]
    )
    sessions = []

    async def create(value):
        session = build_session(root=tmp_path, provider=provider, model='fake', mode=Mode(value.mode))
        await session.start()
        sessions.append(session)
        return session

    scheduler = Scheduler(store, create)
    job = store.save(spec(tmp_path, cadence='manual'))
    run = scheduler.launch(job['id'])
    await until(lambda: store.run(run['id'])['status'] == 'waiting')
    assert not (tmp_path / 'result.txt').exists()
    assert sessions[0].approve('write')
    await until(lambda: not scheduler.active)
    saved = store.run(run['id'])
    assert saved['status'] == 'completed' and saved['summary'] == 'Result is ready.'
    assert saved['session_id'] == sessions[0].id
    assert (tmp_path / 'result.txt').read_text() == 'finished'
    await sessions[0].close()
    await scheduler.stop()
    store.close()


async def test_stop_pending_run_and_provider_failure(tmp_path):
    store = ScheduleStore(tmp_path / 'schedules.db')
    gate = asyncio.Event()

    async def create(value):
        await gate.wait()
        raise RuntimeError('private-key-never-display')

    scheduler = Scheduler(store, create)
    job = store.save(spec(tmp_path, cadence='manual'))
    run = scheduler.launch(job['id'])
    await scheduler.stop_run(run['id'])
    assert store.run(run['id'])['status'] == 'cancelled'
    run = scheduler.launch(job['id'])
    gate.set()
    await until(lambda: not scheduler.active)
    assert store.run(run['id'])['status'] == 'failed'
    assert 'private-key' not in store.run(run['id'])['error']
    await scheduler.stop()
    store.close()


async def test_two_runners_cannot_recover_the_same_database(tmp_path):
    one = ScheduleStore(tmp_path / 'schedules.db')
    two = ScheduleStore(tmp_path / 'schedules.db')

    async def create(value):
        raise AssertionError('No task should run')

    scheduler = Scheduler(one, create)
    scheduler.start()
    with pytest.raises(RuntimeError, match='already using'):
        Scheduler(two, create).start()
    await scheduler.stop()
    one.close()
    two.close()


def test_api_crud_validation_and_run_history(tmp_path, monkeypatch):
    store = ScheduleStore(tmp_path / 'schedules.db')
    scheduler = Scheduler(store, None)
    monkeypatch.setattr(schedules, 'service', scheduler)
    app = FastAPI()
    app.include_router(schedules.router)
    with TestClient(app) as client:
        response = client.post('/api/schedules', json=spec(tmp_path, cadence='manual').model_dump(mode='json'))
        assert response.status_code == 200
        job = response.json()
        assert client.get('/api/schedules').json()['schedules'][0]['id'] == job['id']
        assert client.put('/api/schedules/' + job['id'], json={**job['spec'], 'enabled': False}).status_code == 200
        assert client.get('/api/schedules/' + job['id'] + '/runs').json() == {'runs': []}
        assert client.post('/api/schedules', json={**job['spec'], 'root': str(tmp_path / 'missing')}).status_code == 400
        assert client.post('/api/schedules', json={**job['spec'], 'timezone': 'invalid'}).status_code == 422
        assert client.delete('/api/schedules/' + job['id']).status_code == 200
        assert client.delete('/api/schedules/' + job['id']).status_code == 404
    store.close()


async def test_background_worker_finishes_before_run_and_is_reported(tmp_path):
    store = ScheduleStore(tmp_path / 'schedules.db')
    provider = ScriptedProvider(
        [
            [
                StreamToolUse(
                    id='build', name='shell', input={'command': 'sleep 0.15; echo built', 'background': True}
                ),
                StreamDone(stop_reason='tool_use'),
            ],
            [StreamText(text='Build started.'), StreamDone()],
            [StreamText(text='Background build completed.'), StreamDone()],
        ]
    )
    sessions = []

    async def create(value):
        session = build_session(root=tmp_path, provider=provider, model='fake', mode=Mode.TRUSTED)
        await session.start()
        sessions.append(session)
        return session

    async def finish(session):
        await session.wait_for_turn()
        await session.close()

    scheduler = Scheduler(store, create, finish_session=finish)
    job = store.save(spec(tmp_path, cadence='manual', mode='trusted'))
    run = scheduler.launch(job['id'])
    await until(lambda: not scheduler.active)
    result = store.run(run['id'])
    assert result['status'] == 'completed'
    assert 'Background build completed.' in result['summary']
    assert sessions[0].closed and not sessions[0].tasks.running
    await scheduler.stop()
    store.close()


async def test_stop_waiting_approval_closes_worker_without_writing(tmp_path):
    store = ScheduleStore(tmp_path / 'schedules.db')
    provider = ScriptedProvider(
        [
            [
                StreamToolUse(id='write', name='write_file', input={'path': 'never.txt', 'content': 'no'}),
                StreamDone(stop_reason='tool_use'),
            ]
        ]
    )
    sessions = []

    async def create(value):
        session = build_session(root=tmp_path, provider=provider, model='fake', mode=Mode.ASK)
        await session.start()
        sessions.append(session)
        return session

    async def finish(session):
        await session.close()

    scheduler = Scheduler(store, create, finish_session=finish)
    job = store.save(spec(tmp_path, cadence='manual'))
    run = scheduler.launch(job['id'])
    await until(lambda: store.run(run['id'])['status'] == 'waiting')
    await scheduler.stop_run(run['id'])
    assert store.run(run['id'])['status'] == 'cancelled'
    assert sessions[0].closed and not (tmp_path / 'never.txt').exists()
    await scheduler.stop()
    store.close()


async def test_schedule_tool_cannot_expand_project_tools_or_permissions(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from omote.agent.manager import manager
    from omote.agent.tools.base import ToolContext, ToolError
    from omote.agent.tools.schedule import ScheduleTool

    store = ScheduleStore(tmp_path / 'schedules.db')
    monkeypatch.setattr(schedules, 'service', Scheduler(store, None))
    source = SimpleNamespace(
        tools={'read_file': None, 'schedule': None}, provider=SimpleNamespace(provider_id='chosen'), model='selected'
    )
    monkeypatch.setattr(manager, 'get', lambda ident: source)
    ctx = ToolContext(root=tmp_path, cwd=tmp_path, session_id='main', emit=None, ask=None)
    tool = ScheduleTool()
    task = {'name': 'Check', 'prompt': 'Read notes.', 'cadence': 'manual'}
    output = await tool.run({'action': 'create', 'task': task}, ctx)
    job = json.loads(output.content)
    assert job['spec']['tools'] == ['read_file']
    assert job['spec']['mode'] == 'ask' and job['spec']['root'] == str(tmp_path)
    assert job['spec']['provider'] == 'chosen' and job['spec']['model'] == 'selected'
    with pytest.raises(ToolError):
        await tool.run({'action': 'create', 'task': {**task, 'mode': 'trusted'}}, ctx)
    broader = store.save(Schedule(**{**job['spec'], 'tools': ['shell'], 'enabled': False}))
    with pytest.raises(ToolError, match='reviewing'):
        await tool.run({'action': 'resume', 'id': broader['id']}, ctx)
    other = store.save(Schedule(**{**job['spec'], 'root': str(tmp_path / 'other')}))
    with pytest.raises(ToolError, match='this project'):
        await tool.run({'action': 'delete', 'id': other['id']}, ctx)
    source.tools = {'schedule': None}
    with pytest.raises(ToolError, match='no tools'):
        await tool.run({'action': 'create', 'task': task}, ctx)
    store.close()


async def test_resume_keeps_saved_provider_and_emits_readable_history(tmp_path, monkeypatch):
    from omote.agent.manager import SessionManager
    from omote.routers import agent
    from omote.sessions import SessionStore, Stored

    store = SessionStore(tmp_path / 'chats')
    store.save(
        Stored(
            id='saved123',
            root=str(tmp_path),
            provider='original-provider',
            model='original-model',
            messages=[{'role': 'assistant', 'content': [{'type': 'text', 'text': 'Previous completed result.'}]}],
        )
    )
    manager = SessionManager()
    manager._store = store
    monkeypatch.setattr(agent, 'manager', manager)
    provider = ScriptedProvider([])
    resolved = []

    async def resolve(provider_id, model):
        resolved.append((provider_id, model))
        return provider, model, provider_id

    monkeypatch.setattr(agent, '_resolve_chat', resolve)
    result = await agent.resume_session('saved123')
    assert resolved == [('original-provider', 'original-model')]
    session = manager.get(result['session']['id'])
    async for event in session.events():
        assert event.type == 'session.started'
        assert event.restored_messages[0]['content'][0]['text'] == 'Previous completed result.'
        break
    await manager.close_all()
