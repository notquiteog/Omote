from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from omote.agent.tools.base import ToolContext, ToolError
from omote.agent.tools.photocraft import PhotoCraftTool
from omote.mcp.client import StdioServer
from omote.protocol.agent import Risk

FIXTURE = Path(__file__).parent / 'fixtures' / 'photocraft_mcp.py'


@pytest.fixture
def ctx(tmp_path):
    async def noop(*args):
        return None
    return ToolContext(root=tmp_path, cwd=tmp_path, emit=noop, ask=noop, session_id='photo-test')


@pytest.fixture
def tool(monkeypatch):
    from omote.agent.tools import photocraft
    monkeypatch.setattr(photocraft, 'executable', lambda: sys.executable)
    def build(name, command, args, **kwargs):
        return StdioServer(name, command, [str(FIXTURE), *args], **kwargs)
    monkeypatch.setattr(photocraft, 'StdioServer', build)
    return PhotoCraftTool()


async def test_missing_install_has_actionable_status(ctx, monkeypatch):
    monkeypatch.setattr('omote.agent.tools.photocraft.executable', lambda: None)
    tool = PhotoCraftTool()
    assert '"available": false' in (await tool.run({'action': 'status'}, ctx)).content
    with pytest.raises(ToolError, match='not installed'):
        await tool.run({'action': 'commands'}, ctx)


async def test_edit_uses_real_stdio_protocol_scopes_and_reaps_child(ctx, tool):
    source = ctx.root / 'original 日本.psd'
    source.write_bytes(b'original')
    outdir = ctx.root / 'exports'
    outdir.mkdir()
    result = await tool.run({'action': 'edit', 'source': source.name,
                             'commands': [{'id': 'layer.new.layer', 'params': {'name': 'Ink'}}],
                             'output': 'exports/edited.psd'}, ctx)
    assert source.read_bytes() == b'original'
    assert (outdir / 'edited.psd').read_bytes() == b'fixture-output'
    assert result.images == [('aGVsbG8=', 'image/png')]
    assert str(source.parent) in result.content and str(outdir) in result.content
    pid = int(re.search(r'"pid": (\d+)', result.content)[1])
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_new_doc_and_read_only_inspection(ctx, tool):
    result = await tool.run({'action': 'edit', 'new': {'width': 10, 'height': 10}, 'output': 'new.png'}, ctx)
    assert 'doc_new' in result.content
    inspect = await tool.run({'action': 'inspect', 'source': 'new.png'}, ctx)
    assert '"write_root": "None"' in inspect.content
    assert inspect.images


async def test_failed_command_never_saves_output(ctx, tool):
    with pytest.raises(ToolError, match='requested failure'):
        await tool.run({'action': 'edit', 'new': {}, 'output': 'bad.png',
                        'commands': [{'id': 'filter.fail'}]}, ctx)
    assert not (ctx.root / 'bad.png').exists()


@pytest.mark.parametrize('output', ['../escape.png', '/etc/escape.png'])
def test_escape_rejected_before_approval(ctx, output):
    assert PhotoCraftTool().assess({'action': 'edit', 'new': {}, 'output': output}, ctx).invalid


def test_symlink_escape_and_overwrite_review(ctx, tmp_path):
    outside = tmp_path.parent / f'{tmp_path.name}-outside'
    outside.mkdir()
    (ctx.root / 'link').symlink_to(outside, target_is_directory=True)
    tool = PhotoCraftTool()
    assert tool.assess({'action': 'edit', 'new': {}, 'output': 'link/out.png'}, ctx).invalid
    (ctx.root / 'out.png').write_bytes(b'existing')
    args = {'action': 'edit', 'new': {}, 'output': 'out.png'}
    assert tool.assess(args, ctx).invalid
    assert tool.assess({**args, 'overwrite': True}, ctx).risk is Risk.DESTRUCTIVE


def test_original_never_overwritten_or_arbitrary_file_commands_run(ctx):
    (ctx.root / 'original.psd').write_bytes(b'original')
    tool = PhotoCraftTool()
    assert tool.assess({'action': 'edit', 'source': 'original.psd', 'output': 'original.psd', 'overwrite': True}, ctx).invalid
    assert tool.assess({'action': 'edit', 'new': {}, 'output': 'new.psd',
                        'commands': [{'id': 'file.open', 'params': {'path': '/etc/passwd'}}]}, ctx).invalid


def test_media_toolset_exposes_photocraft():
    from omote.agent.runtime import resolve_toolset
    assert 'photocraft' in resolve_toolset(['media'])
