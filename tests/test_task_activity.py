"""A task index reports work without moving context or starting any work."""
import asyncio
import threading
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from omote.agent.manager import manager
from omote.media.service import Job
from omote.routers import media, schedules


async def test_activity_keeps_active_work_and_deduplicates_scheduled_chats(monkeypatch):
    run = {'id': 'run', 'session_id': 'scheduled-chat', 'status': 'running'}
    monkeypatch.setattr(schedules, 'service', SimpleNamespace(store=SimpleNamespace(
        runs=lambda: [run], list=lambda: [])))
    chats = [dict(id=name, title=name, busy=busy, waiting_on=waiting, background=background)
             for name, busy, waiting, background in (
                 ('scheduled-chat', True, None, 0), ('idle', False, None, 0),
                 ('approval-chat', True, 'approval', 0), ('background-chat', False, None, 2))]
    monkeypatch.setattr(manager, 'list', lambda: chats)
    jobs = {str(i): Job(id=str(i), kind='image', prompt='A portrait', provider='local', model='test',
                        state='done', started_at=i) for i in range(25)}
    jobs['active'] = Job(id='active', kind='video', prompt='Older render still working', provider='local',
                         model='test', state='running', started_at=-1)
    monkeypatch.setattr(media, 'service', SimpleNamespace(jobs=jobs))
    app = FastAPI()
    app.include_router(schedules.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.get('/api/schedules')
    assert response.status_code == 200
    data = response.json()
    assert data['runs'] == [run]
    items = {item['id']: item for item in data['activity']}
    assert 'chat-scheduled-chat' not in items and 'chat-idle' not in items
    assert items['chat-approval-chat']['status'] == 'waiting'
    assert items['chat-background-chat']['status'] == 'running'
    assert items['media-active']['status'] == 'running'
    assert len([item for item in items.values() if item['status'] == 'completed']) == 20
    assert all('messages' not in item and 'params' not in item for item in items.values())


async def test_slow_editor_detection_does_not_block_other_studio_requests(monkeypatch):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    def slow_editors(kind):
        loop.call_soon_threadsafe(entered.set)
        release.wait(4)
        return []
    monkeypatch.setattr(media, 'service', SimpleNamespace(editors=slow_editors, providers=lambda kind: []))
    app = FastAPI()
    app.include_router(media.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        pending = asyncio.create_task(client.get('/api/media/editors'))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert not pending.done(), 'Editor discovery blocked the event loop'
            response = await asyncio.wait_for(client.get('/api/media/providers'), 1)
            assert response.status_code == 200
            assert not pending.done()
        finally:
            release.set()
            await pending
