"""Interrupting or timing out an encode sends its actual MCP request ID."""

import asyncio
import contextlib
import json
import sys

import pytest

from omote.mcp.client import MCPError, StdioServer

SERVER = r'''
import json,pathlib,sys
marker=pathlib.Path(sys.argv[1])
for line in sys.stdin:
    message=json.loads(line)
    method=message.get('method')
    if method=='notifications/cancelled':
        marker.write_text(json.dumps(message['params']))
        continue
    if 'id' not in message:continue
    if method=='tools/call':continue
    result={'protocolVersion':'2024-11-05','serverInfo':{'name':'encode-fixture','version':'1'},'capabilities':{'tools':{}}} if method=='initialize' else {'tools':[]}
    print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}),flush=True)
'''


@pytest.mark.parametrize('use_timeout', [False, True])
async def test_native_export_interrupt_and_timeout_notify_server(tmp_path, use_timeout):
    marker = tmp_path / 'cancelled.json'
    script = tmp_path / 'server.py'
    script.write_text(SERVER)
    server = StdioServer('encode', sys.executable, [str(script), str(marker)], timeout=5)
    try:
        await server.start()
        if use_timeout:
            server.timeout = .05
            with pytest.raises(MCPError, match='timed out'):
                await server.call('encode', {})
        else:
            request = asyncio.create_task(server.call('encode', {}))
            await asyncio.sleep(.05)
            request.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await request
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(.01)
        cancelled = json.loads(marker.read_text())
        assert isinstance(cancelled['requestId'], int) and cancelled['requestId'] > 0
        assert not server._pending
    finally:
        await server.stop()
    assert not server.running
