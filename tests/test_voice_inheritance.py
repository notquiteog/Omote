"""Calls share the conversation, including its approval futures and lifetime."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omote.agent.approval import Mode
from omote.agent.runtime import build_session
from omote.providers.base import Modality, ProviderInfo, StreamDone, StreamText, StreamToolUse
from omote.providers.registry import ProviderRegistry, Route, RouteSet
from omote.routers import voice as voice_router
from omote.sessions import SessionStore
from tests.test_agent import ScriptedProvider
from tests.test_voice import FakeSTT, FakeTTS, build


@pytest.mark.parametrize('mode', [Mode.UNRESTRICTED, Mode.ASK])
def test_talk_uses_parent_model_root_policy_and_screen_approval(tmp_path, monkeypatch, mode):
    provider = ScriptedProvider([
        [StreamToolUse(id='write', name='write_file', input={'path': 'voice.txt', 'content': 'ok'}),
         StreamDone(stop_reason='tool_use')], [StreamText(text='Done.'), StreamDone()],
    ])
    registry = ProviderRegistry()
    registry.register(ProviderInfo(id='speech', label='Speech', modalities={Modality.STT, Modality.TTS}),
                      {Modality.STT: FakeSTT(), Modality.TTS: FakeTTS(delay=0)})
    registry.set_defaults(RouteSet(routes={kind: Route(provider='speech', model='fake')
                                        for kind in (Modality.STT, Modality.TTS)}))
    # No default chat provider: the conversation already has one.
    monkeypatch.setattr(voice_router, 'registry', registry)
    agents = {}
    monkeypatch.setattr(voice_router, 'manager', SimpleNamespace(get=agents.get))

    @asynccontextmanager
    async def lifespan(app):
        parent = build_session(root=tmp_path, provider=provider, model='parent-model', mode=mode,
                               store=SessionStore(tmp_path / 'sessions'), hooks=[])
        await parent.start()
        agents[parent.id] = parent
        yield
        await parent.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(voice_router.router)

    @app.post('/approve')
    async def approve():
        return {'ok': next(iter(agents.values())).approve('write')}

    with TestClient(app) as client:
        parent = next(iter(agents.values()))
        with client.websocket_connect('/ws/voice') as ws:
            ws.send_json({'type': 'voice.start', 'agent_session_id': parent.id, 'model': 'wrong-model'})
            ready = ws.receive_json()
            assert ready['type'] == 'voice.ready'
            assert ready['session'] == parent.id and ready['model'] == 'parent-model'
            assert ready['policy'] == parent.policy.describe()
            ws.send_json({'type': 'voice.text', 'text': 'Create voice.txt'})
            waiting = False
            while True:
                msg = ws.receive()
                if 'text' not in msg:
                    continue
                import json
                event = json.loads(msg['text'])
                assert event['type'] != 'voice.error', event
                if event['type'] == 'voice.waiting':
                    waiting = True
                    assert mode is Mode.ASK
                    assert not (tmp_path / 'voice.txt').exists()
                    assert client.post('/approve').json()['ok']
                if event['type'] == 'speech.stopped':
                    break
            assert waiting == (mode is Mode.ASK)
            ws.send_json({'type': 'voice.stop'})
        assert not parent.closed
        assert (tmp_path / 'voice.txt').read_text() == 'ok'
        assert provider.seen[0].model == 'parent-model'
        assert parent.policy.mode is mode
        assert parent.system_prompt != provider.seen[0].system  # Spoken style is per turn.
        client.portal.call(parent.submit, 'A typed follow-up')
        client.portal.call(parent.wait_for_turn)
        from omote.voice.pipeline import VoiceConfig
        assert VoiceConfig().system_prompt not in provider.seen[-1].system


async def test_hanging_up_while_parent_is_busy_does_not_cancel_typed_work(tmp_path):
    started = asyncio.Event()
    released = asyncio.Event()

    class Provider:
        async def stream(self, request):
            started.set()
            await released.wait()
            yield StreamText(text='Typed answer.')
            yield StreamDone()

    parent = build_session(root=tmp_path, provider=Provider(), model='test', mode=Mode.ASK,
                           store=SessionStore(tmp_path / 'sessions'), hooks=[])
    await parent.start()
    voice, _, _ = build(parent.provider)
    voice.agent, voice.owns_agent = parent, False
    try:
        parent.submit('Typed task')
        await started.wait()
        await voice.say('Spoken task')
        await asyncio.sleep(.02)
        await voice.close()
        assert parent.busy and not parent.closed
        released.set()
        await parent.wait_for_turn()
    finally:
        await parent.close()


async def test_screen_answer_clears_the_voice_question(tmp_path):
    from omote.protocol.agent import QuestionResolved
    from omote.protocol.voice import VoiceAnswered, VoiceWaiting
    provider = ScriptedProvider([
        [StreamToolUse(id='question', name='ask_user', input={'question': 'Which colour?', 'options': ['Blue']}),
         StreamDone(stop_reason='tool_use')], [StreamText(text='Blue it is.'), StreamDone()],
    ])
    parent = build_session(root=tmp_path, provider=provider, model='test', mode=Mode.UNRESTRICTED,
                           store=SessionStore(tmp_path / 'sessions'), hooks=[])
    await parent.start()
    voice, events, _ = build(provider, tts=FakeTTS(delay=0))
    voice.agent, voice.owns_agent = parent, False
    waiting = asyncio.Event()
    emit = voice.emit_event

    async def observe(event):
        await emit(event)
        if isinstance(event, VoiceWaiting):
            waiting.set()

    voice.emit_event = observe
    try:
        await voice.say('Choose a colour')
        await asyncio.wait_for(waiting.wait(), 2)
        assert parent.answer(voice._awaiting.id, 'Blue')
        assert await voice.settle()
        assert voice._awaiting is None
        assert any(isinstance(e, VoiceAnswered) and e.heard == 'on screen' for e in events)
        resolved = []
        async for event in parent.events():
            if isinstance(event, QuestionResolved):
                resolved.append(event)
                break
        assert resolved[0].answered
    finally:
        await voice.close()
        await parent.close()


@pytest.mark.parametrize('cookie_auth', [False, True])
def test_unknown_parent_does_not_start_an_unrelated_call(monkeypatch, cookie_auth):
    monkeypatch.setattr(voice_router, 'manager', SimpleNamespace(get=lambda _: None))
    app = FastAPI()
    app.include_router(voice_router.router)
    client = TestClient(app)
    if cookie_auth:
        from omote.config import config
        from omote.routers.auth import COOKIE
        monkeypatch.setattr(config, 'auth_token', 'test-token')
        client.cookies.set(COOKIE, 'test-token')
    with client, client.websocket_connect('/ws/voice') as ws:
        ws.send_json({'type': 'voice.start', 'agent_session_id': 'missing'})
        event = ws.receive_json()
        assert event['type'] == 'voice.error' and event['fatal']
        assert ws.receive()['code'] == 4404
