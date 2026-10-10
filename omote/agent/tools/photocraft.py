"""Scoped PhotoCraft image editing through its documented headless MCP server.

Each operation has its own engine session. Editable files are the durable state;
no orphan editor process or hidden in-memory document survives a tool call.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from omote.agent.tools.base import Assessment, Output, Tool, ToolContext, ToolError, resolve_in_root, truncate
from omote.mcp.client import MCPError, StdioServer
from omote.protocol.agent import Risk

ACTIONS = ('status', 'commands', 'inspect', 'edit')


def executable() -> str | None:
    """The operator chooses the executable, never a model tool argument."""
    return shutil.which(os.environ.get('OMOTE_PHOTOCRAFT_BIN', 'photocraft-cli'))


class PhotoCraftTool(Tool):
    name = 'photocraft'
    description = (
        'Edit layered images with the installed open-source PhotoCraft engine. '
        'Use status to check installation, commands with a filter to discover real command IDs '
        'and parameters, inspect to see a source document and preview, or edit to open a source '
        '(or create a new document), apply ordered commands, and save a separate output. '
        'Use PSD or .pcraft for editable layers; PNG/JPEG are exports. Each call uses a fresh '
        'headless session: reopen the last saved output to continue editing. Commands run through '
        'PhotoCraft MCP with explicit read/write directories, never a shell. '
        'Inspect the returned preview and warnings before claiming an edit succeeded. '
        'For interactive UI editing, use the PhotoCraft skill and a configured live MCP bridge.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': list(ACTIONS)},
            'filter': {'type': 'string', 'description': 'For commands: search command IDs or labels.'},
            'source': {'type': 'string', 'description': 'Existing document path, relative to the working directory.'},
            'output': {'type': 'string', 'description': 'For edit: a separate output file in an existing directory.'},
            'new': {
                'type': 'object',
                'description': 'Instead of source: PhotoCraft doc_new parameters (width, height, mode, depth, background, name).',
                'properties': {
                    'width': {'type': 'integer', 'minimum': 1},
                    'height': {'type': 'integer', 'minimum': 1},
                    'mode': {'type': 'string', 'enum': ['rgb', 'gray', 'cmyk', 'lab']},
                    'depth': {'type': 'integer', 'enum': [8, 16, 32]},
                    'background': {'type': 'string'}, 'name': {'type': 'string'},
                },
                'additionalProperties': False,
            },
            'commands': {
                'type': 'array', 'maxItems': 100,
                'items': {
                    'type': 'object',
                    'properties': {'id': {'type': 'string'}, 'params': {'type': 'object'}},
                    'required': ['id'], 'additionalProperties': False,
                },
            },
            'overwrite': {'type': 'boolean', 'description': 'Allow replacing an existing output, after approval.'},
        },
        'required': ['action'],
        'additionalProperties': False,
    }

    def _paths(self, args: dict[str, Any], ctx: ToolContext) -> tuple[Path | None, Path | None]:
        action = args.get('action')
        if action not in ACTIONS:
            raise ToolError(f'action must be one of {", ".join(ACTIONS)}')
        source = resolve_in_root(args['source'], ctx) if args.get('source') else None
        output = resolve_in_root(args['output'], ctx) if args.get('output') else None
        if action == 'inspect' and source is None:
            raise ToolError('source is required for inspect')
        if source is not None and not source.is_file():
            raise ToolError(f'{source}: source is not a file')
        if action == 'edit':
            if (source is not None) == ('new' in args):
                raise ToolError('edit needs exactly one of source or new')
            if 'new' in args and not isinstance(args['new'], dict):
                raise ToolError('new must be a document settings object')
            if output is None or not output.suffix:
                raise ToolError('output with a file extension is required')
            if not output.parent.is_dir():
                raise ToolError('the output directory must already exist')
            if source == output:
                raise ToolError('save a separate output to preserve the original document')
            if output.exists() and (not output.is_file() or not args.get('overwrite')):
                raise ToolError('output already exists; use a new filename or explicitly set overwrite')
            commands = args.get('commands', [])
            if not isinstance(commands, list) or len(commands) > 100:
                raise ToolError('commands must be a list of at most 100 steps')
            for step in commands:
                if (not isinstance(step, dict) or not isinstance(step.get('id'), str)
                        or not step['id'].strip() or not isinstance(step.get('params', {}), dict)):
                    raise ToolError('each command needs an id and optional params object')
                # File I/O is deliberately only through doc_open/doc_save below.
                # PhotoCraft also enforces its own automation policy on nested operations.
                if step['id'].startswith(('file.', 'prefs.', 'plugIn.', 'script.')):
                    raise ToolError('use document source/output, not filesystem, preferences, or script commands')
        return source, output

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        action = args.get('action', '')
        risk = Risk.EXECUTE if action == 'edit' else Risk.READ
        try:
            source, output = self._paths(args, ctx)
            if action == 'edit' and (args.get('overwrite') or (output is not None and output.exists())):
                risk = Risk.DESTRUCTIVE
            summary = f'PhotoCraft {action}'
            if source:
                summary += f' {source.name}'
            if output:
                summary += f' → {output}'
            if action == 'edit':
                summary += ': ' + ', '.join(s['id'] for s in args.get('commands', []))
            return Assessment(risk=risk, summary=summary)
        except (ToolError, OSError, TypeError, ValueError) as exc:
            return Assessment(risk=risk, summary='', invalid=str(exc))

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        source, output = self._paths(args, ctx)
        command = executable()
        action = args['action']
        if action == 'status':
            return Output(content=json.dumps({
                'available': bool(command), 'executable': command,
                'setup': 'Install photocraft-cli, put it on PATH, or set OMOTE_PHOTOCRAFT_BIN to its full path.',
                'documentation': 'https://github.com/storytold/photocraft/blob/main/docs/mcp.md',
            }))
        if command is None:
            raise ToolError('PhotoCraft is not installed: install photocraft-cli or set OMOTE_PHOTOCRAFT_BIN.')
        launch = ['mcp']
        if source:
            launch += ['--automation-read-root', str(source.parent)]
        if action == 'edit' and output:
            launch += ['--automation-write-root', str(output.parent)]
        server = StdioServer('photocraft', command, launch, cwd=str(ctx.cwd), timeout=300)
        results: list[str] = []
        images: list[tuple[str, str]] = []

        async def call(name: str, params: dict[str, Any]):
            if name not in {t.name for t in server.tools}:
                raise ToolError(f'installed PhotoCraft does not expose {name}; update PhotoCraft or use its listed MCP tools')
            result = await server.call(name, params)
            if result.is_error:
                raise ToolError(f'PhotoCraft {name}: {result.text}')
            results.append(f'{name}: {result.text}')
            images.extend(result.images)

        try:
            await server.start()
            if source:
                await call('doc_open', {'path': source.name})
            if action == 'commands':
                await call('command_list', {'filter': str(args.get('filter', ''))})
            else:
                if action == 'edit':
                    if source is None:
                        await call('doc_new', args['new'])
                    for step in args.get('commands', []):
                        await ctx.emit(f'PhotoCraft: {step["id"]}\n', 'stdout')
                        await call('command_run', {'id': step['id'], 'params': step.get('params', {}), 'wait': True})
                    assert output is not None
                    # Resolve again just before the write, and keep the undo snapshot.
                    self._paths(args, ctx)
                    if ctx.checkpoint:
                        ctx.checkpoint.record(output)
                    await call('doc_save', {'path': output.name})
                    if not output.is_file() or output.stat().st_size == 0:
                        raise ToolError('PhotoCraft reported a save but no nonempty output file was found')
                    results.append(f'Saved {output} ({output.stat().st_size} bytes).')
                await call('doc_inspect', {})
                preview = 'render_preview' if any(t.name == 'render_preview' for t in server.tools) else 'doc_render_preview'
                await call(preview, {'max_side': 1024})
        except (MCPError, OSError) as exc:
            raise ToolError(f'PhotoCraft: {exc}') from exc
        finally:
            await server.stop()
        content, clipped = truncate('\n'.join(results))
        return Output(content=content, images=images, truncated=clipped,
                      display={'application': 'PhotoCraft', 'action': action, 'output': str(output) if output else None})
