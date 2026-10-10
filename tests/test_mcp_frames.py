"""MCP media messages exceed a terminal-sized line; malformed frames fail fast."""

import asyncio
import sys
from pathlib import Path

import pytest

from omote.mcp.client import MCPError, StdioServer

FIXTURE = Path(__file__).parent / 'fixtures' / 'mcp_server.py'


async def test_large_mcp_result_survives_stdio():
    server = StdioServer('frame-test', sys.executable, [str(FIXTURE)], timeout=3)
    try:
        await server.start()
        result = await server.call('echo', {'text': 'x' * 180_000})
        assert result.text == 'echo: ' + 'x' * 180_000
    finally:
        await server.stop()


async def test_oversize_mcp_line_fails_without_waiting_for_timeout(monkeypatch):
    monkeypatch.setattr('omote.mcp.client.MAX_STDIO_LINE_BYTES', 4096)
    server = StdioServer('frame-test', sys.executable, [str(FIXTURE)], timeout=30)
    try:
        await server.start()
        with pytest.raises(MCPError, match='maximum 4096'):
            await asyncio.wait_for(server.call('echo', {'text': 'x' * 100_000}), timeout=3)
    finally:
        await server.stop()


async def test_json_nonobject_does_not_kill_reader(tmp_path):
    fixture = tmp_path / 'messages.py'
    fixture.write_text(
        'import json, sys\n'
        'for line in sys.stdin:\n'
        ' p=json.loads(line)\n'
        ' if "id" in p:\n'
        '  print("[]", flush=True)\n'
        '  print(json.dumps({"id": []}), flush=True)\n'
        '  print(json.dumps({"jsonrpc":"2.0","id":p["id"],"result":{"capabilities":{},"tools":[]}}), flush=True)\n'
    )
    server = StdioServer('frame-test', sys.executable, [str(fixture)], timeout=3)
    try:
        await server.start()
        assert server.running
    finally:
        await server.stop()
