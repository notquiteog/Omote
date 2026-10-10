import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omote.config import config
from omote.providers.realtime import RealtimeConfig, RealtimeSession
from omote.providers.registry import ProviderRegistry, RouteSet
from omote.routers import auth, realtime


@pytest.mark.parametrize('detection', ['server_vad', 'semantic_vad', 'none'])
async def test_ga_configuration_uses_nested_audio_and_valid_vad(detection):
    live = RealtimeSession('test-key', RealtimeConfig(turn_detection=detection, temperature=0.8))
    live._ws = AsyncMock()
    live._ws.closed = False
    await live.configure()
    message = json.loads(live._ws.send_str.call_args.args[0])
    session = message['session']
    assert session['type'] == 'realtime'
    assert session['output_modalities'] == ['audio']
    assert 'modalities' not in session
    assert 'temperature' not in session
    assert session['audio']['input']['format'] == {'type': 'audio/pcm', 'rate': 24000}
    assert session['audio']['output']['voice'] == 'marin'
    vad = session['audio']['input']['turn_detection']
    if detection == 'none':
        assert vad is None
    elif detection == 'semantic_vad':
        assert vad['type'] == detection
        assert 'silence_duration_ms' not in vad
    else:
        assert vad['silence_duration_ms'] == 500


def client(monkeypatch, *, local=False):
    monkeypatch.setattr(config, 'auth_token', 'test-token')
    monkeypatch.setattr(config, 'openai_key', '')
    monkeypatch.setattr(config, 'local_only', False)
    registry = ProviderRegistry()
    registry.set_defaults(RouteSet(local_only=local))
    monkeypatch.setattr(realtime, 'registry', registry)
    app = FastAPI()
    app.add_middleware(auth.TokenMiddleware)
    app.include_router(realtime.router)
    result = TestClient(app)
    result.cookies.set(auth.COOKIE, 'test-token')
    return result


def test_missing_key_reports_error_and_cookie_authentication_works(monkeypatch):
    with client(monkeypatch) as browser, browser.websocket_connect('/ws/realtime') as ws:
        event = ws.receive_json()
        assert event['type'] == 'realtime.error'
        assert event['fatal']
        assert 'key' in event['message']


def test_local_only_realtime_is_refused_before_network(monkeypatch):
    with client(monkeypatch, local=True) as browser, browser.websocket_connect('/ws/realtime') as ws:
        event = ws.receive_json()
        assert event['type'] == 'realtime.error'
        assert 'Local-only' in event['message']
