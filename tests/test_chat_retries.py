"""Transient upstream errors must not discard a tool-assisted conversation."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from omote.providers.base import ChatRequest, StreamText
from omote.providers.openai_compat import OpenAICompatProvider


@asynccontextmanager
async def server(statuses, *, stream_error=False, tool_first=False):
    requests = []
    called = asyncio.Event()

    async def handle(request):
        requests.append(await request.json())
        called.set()
        status = statuses.pop(0) if statuses else 200
        if status != 200:
            return web.json_response({'error': 'upstream unavailable'}, status=status)
        if tool_first and len(requests) == 1:
            chunk = {'choices': [{'delta': {'tool_calls': [{
                'index': 0, 'id': 'write-once', 'function': {'name': 'write_file',
                'arguments': json.dumps({'path': 'once.txt', 'content': 'written once'})},
            }]}, 'finish_reason': 'tool_calls'}]}
            return web.Response(text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n',
                                content_type='text/event-stream')
        chunk = {'error': {'message': 'late upstream error', 'code': 502}} if stream_error else {
            'choices': [{'delta': {'content': 'OK'}, 'finish_reason': 'stop'}]}
        return web.Response(text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n',
                            content_type='text/event-stream')

    app = web.Application()
    app.router.add_post('/chat/completions', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield OpenAICompatProvider(f'http://127.0.0.1:{port}'), requests, called
    finally:
        await runner.cleanup()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr('omote.providers.openai_compat.CHAT_RETRY_DELAYS', (0, 0))


@pytest.mark.parametrize('status', [502, 503, 504])
async def test_transient_failure_retries_the_same_request(status):
    async with server([status, status, 200]) as (provider, requests, _):
        events = [e async for e in provider.stream(ChatRequest(model='test', messages=[]))]
    assert [e.text for e in events if isinstance(e, StreamText)] == ['OK']
    assert len(requests) == 3
    assert requests[0] == requests[1] == requests[2]


@pytest.mark.parametrize('status', [400, 401, 403, 429])
async def test_client_and_rate_limit_failures_are_not_retried(status):
    async with server([status]) as (provider, requests, _):
        with pytest.raises(RuntimeError, match=f'HTTP {status}'):
            _ = [e async for e in provider.stream(ChatRequest(model='test', messages=[]))]
    assert len(requests) == 1


async def test_retries_are_bounded_and_report_a_retryable_failure():
    async with server([502, 502, 502, 200]) as (provider, requests, _):
        with pytest.raises(ConnectionError, match='HTTP 502'):
            _ = [e async for e in provider.stream(ChatRequest(model='test', messages=[]))]
    assert len(requests) == 3


async def test_an_opened_stream_is_never_replayed():
    async with server([], stream_error=True) as (provider, requests, _):
        with pytest.raises(RuntimeError, match='late upstream error'):
            _ = [e async for e in provider.stream(ChatRequest(model='test', messages=[]))]
    assert len(requests) == 1


async def test_interrupt_during_backoff_stops_retries(monkeypatch):
    monkeypatch.setattr('omote.providers.openai_compat.CHAT_RETRY_DELAYS', (60, 60))
    async with server([502, 200]) as (provider, requests, called):
        async def collect():
            return [e async for e in provider.stream(ChatRequest(model='test', messages=[]))]
        task = asyncio.create_task(collect())
        await called.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(requests) == 1


async def test_retry_after_a_tool_does_not_execute_the_tool_twice(tmp_path):
    from omote.agent.approval import Mode
    from omote.agent.runtime import build_session
    from omote.protocol.agent import ToolCompleted
    from omote.sessions import SessionStore
    from tests.test_agent import drain

    async with server([200, 502, 200], tool_first=True) as (provider, requests, _):
        session = build_session(root=tmp_path, provider=provider, model='test', mode=Mode.UNRESTRICTED,
                                store=SessionStore(tmp_path / 'transcripts'), hooks=[])
        await session.start()
        try:
            session.submit('Write the file, then report the result.')
            events = await drain(session)
        finally:
            await session.close()
    results = [event for event in events if isinstance(event, ToolCompleted)]
    assert len(results) == 1 and results[0].result.ok
    assert (tmp_path / 'once.txt').read_text(encoding='utf-8') == 'written once'
    assert len(requests) == 3 and requests[1] == requests[2]
    assert any(m.get('role') == 'tool' for m in requests[-1]['messages'])
