"""Agent workflow authoring uses the same versioned workbench as the UI."""

from __future__ import annotations

import copy
import json
from typing import Any

from fastapi import HTTPException

from omote.agent.tools.base import Assessment, Output, Tool, ToolError, resolve_in_root, truncate
from omote.protocol.agent import Risk

READ = {'connections', 'nodes', 'list', 'get', 'compare', 'validate', 'status'}
WRITE = {'save', 'patch', 'run', 'cancel', 'import_outputs', 'export', 'delete'}


class ComfyWorkflowTool(Tool):
    name = 'comfy_workflow'
    description = (
        'Create, inspect, edit, validate and run ComfyUI flows using the same saved workflow/version history '
        'visible in Studio. connections lists actual ComfyUI providers; nodes searches their LIVE installed '
        'node schemas and model choices, with bounded pagination. Never invent node classes or checkpoint '
        'names. save creates or versions a workflow: supply an API prompt and/or native editor graph in '
        'fields; updates require base_revision from get. Preserve layout with graph when supplied; API-only '
        'flows support atomic patch changes to selected node inputs, with optimistic base_revision. '
        'Use get with query to inspect a subset of large flows. API edits of an editor-backed flow require '
        'explicit drop_editor_layout; the previous version remains and the official editor can rebuild layout. '
        'flows can be loaded into the official editor through the bridge. validate checks node inputs, '
        'model choices, links and cycles before run queues an exact saved revision. status returns outputs '
        'and errors; import_outputs keeps finished files in the Studio library. export writes API, native '
        'editor or both formats to a separate JSON file in this chat workspace. Runs and output imports '
        'never happen during save/validate. Inspect resulting images/video before claiming success.'
    )
    input_schema = {'type': 'object', 'required': ['action'], 'additionalProperties': False, 'properties': {
        'action': {'type': 'string', 'enum': sorted(READ | WRITE)},
        'id': {'type': 'string', 'description': 'Workflow ID, or run ID for status/cancel/import_outputs.'},
        'provider': {'type': 'string'}, 'revision': {'type': 'integer', 'minimum': 1},
        'before': {'type': 'integer', 'minimum': 1}, 'after': {'type': 'integer', 'minimum': 1},
        'query': {'type': 'string', 'maxLength': 500}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20},
        'offset': {'type': 'integer', 'minimum': 0},
        'fields': {'type': 'object', 'description': 'name, project, notes, graph (native editor JSON), prompt (API graph), base_revision for updates. Pass both graph and prompt to retain both representations; pass graph:null explicitly to discard stale layout.'},
        'base_revision': {'type': 'integer', 'minimum': 1},
        'drop_editor_layout': {'type': 'boolean', 'default': False},
        'changes': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['op', 'node'], 'properties': {'op': {'type': 'string', 'enum': ['set_input', 'remove_input', 'add_node', 'remove_node']},
                'node': {'type': 'string'}, 'input': {'type': 'string'}, 'value': {}, 'class_type': {'type': 'string'}}}},
        'path': {'type': 'string'}, 'format': {'type': 'string', 'enum': ['api', 'editor', 'bundle'], 'default': 'bundle'},
        'overwrite': {'type': 'boolean', 'default': False},
    }}

    def assess(self, args: dict[str, Any], ctx):
        action = args.get('action')
        risk = Risk.READ if isinstance(action, str) and action in READ else Risk.WRITE
        if action == 'run':
            risk = Risk.NETWORK
        elif action in ('cancel',):
            risk = Risk.EXECUTE
        elif action == 'delete' or args.get('overwrite'):
            risk = Risk.DESTRUCTIVE
        try:
            if not isinstance(action, str) or action not in READ | WRITE:
                raise ToolError('Choose a supported workflow action.')
            if action in ('get', 'patch', 'compare', 'validate', 'run', 'status', 'cancel', 'import_outputs', 'export', 'delete') and not isinstance(args.get('id'), str):
                raise ToolError('Use a workflow/run ID from list or run.')
            if action in ('nodes', 'validate', 'run') and not isinstance(args.get('provider'), str):
                raise ToolError('Choose a ComfyUI provider from connections.')
            if type(args.get('limit', 10)) is not int or not 1 <= args.get('limit', 10) <= 20 or type(args.get('offset', 0)) is not int or args.get('offset', 0) < 0:
                raise ToolError('Use limit 1–20 and a nonnegative offset.')
            if action in ('save',):
                from omote.routers.workbench import Snapshot

                body = Snapshot.model_validate(args.get('fields', {}))
                if args.get('id') and body.base_revision is None:
                    raise ToolError('An update needs base_revision from get to prevent overwriting another edit.')
            if action == 'patch':
                if type(args.get('base_revision')) is not int or args['base_revision'] < 1:
                    raise ToolError('Patch needs the base_revision returned by get.')
                changes = args.get('changes')
                if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
                    raise ToolError('Pass 1–100 node changes.')
                for change in changes:
                    if not isinstance(change, dict) or change.get('op') not in ('set_input', 'remove_input', 'add_node', 'remove_node') or not isinstance(change.get('node'), str):
                        raise ToolError('Each change needs an operation and string node ID.')
                    if change['op'] in ('set_input', 'remove_input') and not isinstance(change.get('input'), str):
                        raise ToolError('Input changes need an input name.')
                    if change['op'] == 'set_input' and 'value' not in change:
                        raise ToolError('set_input needs a value.')
                    if change['op'] == 'add_node' and (not isinstance(change.get('class_type'), str) or not isinstance(change.get('value'), dict)):
                        raise ToolError('add_node needs class_type and a value object containing the inputs.')
            if action in ('run', 'compare'):
                keys = ('before', 'after') if action == 'compare' else ('revision',)
                if any(type(args.get(key)) is not int or args[key] < 1 for key in keys):
                    raise ToolError('Use explicit positive saved revision numbers.')
            if action == 'export':
                path = resolve_in_root(args.get('path', ''), ctx)
                if path.suffix.lower() != '.json' or not path.parent.is_dir() or (path.exists() and not args.get('overwrite')):
                    raise ToolError('Choose a separate .json path in an existing directory; overwrite must be explicit.')
                if args.get('format', 'bundle') not in ('api', 'editor', 'bundle'):
                    raise ToolError('Export api, editor or bundle JSON.')
            return Assessment(risk=risk, summary=f'ComfyUI workflow: {action} {args.get("id", "")}')
        except (ToolError, ValueError, TypeError, OSError) as error:
            return Assessment(risk=risk, summary='ComfyUI workflow', invalid=str(error))

    async def run(self, args, ctx):
        from omote.routers import comfyui
        from omote.routers import workbench as wb

        check = self.assess(args, ctx)
        if check.invalid:
            raise ToolError(check.invalid)
        action, ident = args['action'], args.get('id')
        limit, offset = args.get('limit', 10), args.get('offset', 0)
        try:
            if action == 'connections':
                data = await comfyui.editors()
                # Browser URLs are setup hints, not credential-bearing API URLs.
                data = {'connections': data['editors']}
            elif action == 'nodes':
                catalog = await wb.upstream(wb.connection(args['provider']), 'object_info')
                query = str(args.get('query', '')).lower()
                names = sorted(name for name, spec in catalog.items() if query in f'{name} {spec.get("display_name", "")} {spec.get("category", "")}'.lower())
                selected, used = {}, 0
                for name in names[offset:offset + limit]:
                    size = len(json.dumps(catalog[name]).encode())
                    if selected and used + size > 48000:
                        break
                    selected[name] = catalog[name]
                    used += size
                data = {'provider': args['provider'], 'nodes': selected, 'total': len(names),
                        'next_offset': offset + len(selected) if offset + len(selected) < len(names) else None}
            elif action == 'list':
                data = wb.workflows(q=str(args.get('query', '')), limit=limit, offset=offset)
            elif action == 'get':
                data = wb.get_workflow(ident, args.get('revision'))
                if args.get('query'):
                    query = str(args['query']).lower()
                    prompt = data.get('prompt') or {}
                    matches = [(key, value) for key, value in prompt.items() if query == key.lower() or query in str(value.get('class_type', '')).lower()]
                    data = {**{key: data[key] for key in ('id', 'name', 'project', 'revision', 'notes')},
                            'prompt_nodes': dict(matches[offset:offset + limit]), 'matching_nodes': len(matches),
                            'has_editor_graph': data['graph'] is not None,
                            'note': 'This is a node subset. Use patch for changes; do not replace the complete graph with this subset.'}
            elif action == 'save':
                body = wb.Snapshot.model_validate(args['fields'])
                if ident and wb.get_workflow(ident, None)['graph'] is not None and 'graph' not in args['fields']:
                    raise ToolError('This version includes editor layout. Supply its updated graph, or graph:null explicitly; the old version is kept.')
                data = wb.save_version(ident, body) if ident else wb.create(body)
            elif action == 'patch':
                saved = wb.get_workflow(ident, None)
                if saved['revision'] != args['base_revision']:
                    raise ToolError('This workflow changed. Get the latest version before patching it.')
                if not saved['prompt']:
                    raise ToolError('Capture an API prompt in the editor before patching node inputs.')
                if saved['graph'] is not None and not args.get('drop_editor_layout'):
                    raise ToolError('This flow has editor layout. Save both updated representations, or explicitly set drop_editor_layout to rebuild layout from API data. The previous version is retained.')
                prompt = copy.deepcopy(saved['prompt'])
                for change in args['changes']:
                    node, operation = change['node'], change['op']
                    if operation == 'add_node':
                        if node in prompt:
                            raise ToolError(f'Node {node} already exists.')
                        prompt[node] = {'class_type': change['class_type'], 'inputs': change['value']}
                    elif node not in prompt:
                        raise ToolError(f'Node {node} does not exist.')
                    elif operation == 'remove_node':
                        del prompt[node]
                    elif operation == 'set_input':
                        prompt[node]['inputs'][change['input']] = change['value']
                    else:
                        if change['input'] not in prompt[node]['inputs']:
                            raise ToolError(f'Input {node}.{change["input"]} does not exist.')
                        del prompt[node]['inputs'][change['input']]
                data = wb.save_version(ident, wb.Snapshot(name=saved['name'], project=saved['project'],
                    notes='Updated through chat: ' + ', '.join(f'{change["op"]} {change["node"]}' for change in args['changes']),
                    graph=None, prompt=prompt, base_revision=args['base_revision']))
                if saved['graph'] is not None:
                    data['warning'] = 'Editor layout was explicitly discarded for this new API version. Load it in the official editor and capture it to save a fresh layout.'
            elif action == 'compare':
                data = wb.compare_versions(ident, args['before'], args['after'])
            elif action == 'validate':
                saved = wb.get_workflow(ident, args.get('revision'))
                if not saved['prompt']:
                    raise ToolError('This workflow has editor JSON only; capture/export API data in the official editor before validation.')
                data = await wb.validate(args['provider'], wb.PromptBody(prompt=saved['prompt']))
            elif action == 'run':
                data = await wb.run(wb.RunBody(workflow_id=ident, revision=args['revision'], provider=args['provider']))
            elif action == 'status':
                data = await wb.get_run(ident)
            elif action == 'cancel':
                data = await wb.cancel(ident)
            elif action == 'import_outputs':
                data = await wb.import_outputs(ident)
            elif action == 'delete':
                data = wb.delete_workflow(ident)
            elif action == 'export':
                saved = wb.get_workflow(ident, args.get('revision'))
                kind = args.get('format', 'bundle')
                value = saved if kind == 'bundle' else saved['prompt' if kind == 'api' else 'graph']
                if value is None:
                    raise ToolError(f'This version has no {kind} representation. Load API data in the editor and capture it to obtain editor JSON.')
                path = resolve_in_root(args['path'], ctx)
                if ctx.checkpoint:
                    ctx.checkpoint.record(path)
                # Exclusive creation closes a race with another writer.
                with path.open('w' if args.get('overwrite') else 'x', encoding='utf-8') as stream:
                    json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                data = {'path': str(path), 'format': kind, 'bytes': path.stat().st_size, 'workflow_id': ident, 'revision': saved['revision']}
            else:
                raise ToolError('Unknown workflow action.')
        except HTTPException as error:
            raise ToolError(str(error.detail)) from error
        except (ValueError, OSError) as error:
            raise ToolError(str(error)) from error
        content, clipped = truncate(json.dumps(data, ensure_ascii=False), 64000)
        return Output(content=content, truncated=clipped, display={'summary': f'ComfyUI: {action}'})
