"""Optional Git probes must not freeze chat and settings (Windows issue #1)."""
import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI

from omote.agent import git as core
from omote.agent.runtime import build_session
from omote.agent.tools.git import GitTool
from omote.protocol.agent import TextDelta
from omote.providers.base import StreamDone, StreamText
from omote.routers import git, providers
from tests.test_agent import ScriptedProvider, drain
from tests.test_git import ctx_for


@pytest.mark.parametrize('entry', ['commit_bar', 'agent_tool'])
async def test_chat_and_provider_settings_respond_while_git_detection_is_stalled(tmp_path, monkeypatch, entry):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()

    def slow_probe(path):
        loop.call_soon_threadsafe(entered.set)
        release.wait(4)
        return False

    monkeypatch.setattr(core, 'is_repo', slow_probe)
    app = FastAPI()
    app.include_router(git.router)
    app.include_router(providers.router)
    session = build_session(root=tmp_path, model='local-test',
                            provider=ScriptedProvider([[StreamText(text='Still responding'), StreamDone()]]))
    session.store = None
    await session.start()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        pending = asyncio.create_task(
            client.get('/api/git/status', params={'root': str(tmp_path)}) if entry == 'commit_bar'
            else GitTool().run({'action': 'status'}, ctx_for(tmp_path)),
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert not pending.done(), 'The event loop was blocked until the Git probe returned'
            response = await asyncio.wait_for(client.get('/api/providers'), 1)
            assert response.status_code == 200
            session.submit('Can you still answer?')
            events = await drain(session, timeout=1)
            assert any(isinstance(event, TextDelta) and event.text == 'Still responding' for event in events)
            assert not pending.done()
        finally:
            release.set()
            response = await pending
            await session.close()
        if entry == 'commit_bar':
            assert response.status_code == 400
            assert 'not a git repository' in response.json()['detail']
        else:
            assert response.display['repo'] is False


async def test_chat_workspace_does_not_require_git_to_be_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(core.shutil, 'which', lambda name: None)
    assert core.is_repo(tmp_path) is False
    app = FastAPI()
    app.include_router(git.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.get('/api/git/status', params={'root': str(tmp_path)})
    assert response.status_code == 400
    assert 'not a git repository' in response.json()['detail']
