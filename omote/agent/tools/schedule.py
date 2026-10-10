"""Let an explicitly requested follow-up become visible, durable scheduled work."""

import asyncio
import json
from pathlib import Path

from omote.agent.tools.base import Assessment, Output, Tool, ToolError
from omote.protocol.agent import Risk


class ScheduleTool(Tool):
    name = 'schedule'
    description = (
        'Manage scheduled tasks only when the user asks for scheduled or recurring work. '
        'Create uses this project and this session’s tools, with Ask first permissions. '
        'Tasks run locally while Omote is running. Dates need a timezone offset; '
        'daily/weekly schedules need an IANA timezone. Open Scheduled to inspect results, edit permissions, or stop runs. '
        'Never schedule unsolicited follow-ups. This tool cannot increase permissions or change the project.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': ['list', 'create', 'pause', 'resume', 'delete']},
            'id': {'type': 'string'},
            'task': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'prompt': {'type': 'string'},
                    'cadence': {'type': 'string', 'enum': ['manual', 'once', 'interval', 'daily', 'weekly']},
                    'timezone': {'type': 'string'},
                    'at': {'type': 'string'},
                    'weekdays': {'type': 'array', 'items': {'type': 'integer'}},
                    'interval_minutes': {'type': 'integer'},
                    'start_at': {'type': 'string'},
                },
                'required': ['name', 'prompt', 'cadence'],
                'additionalProperties': False,
            },
        },
        'required': ['action'],
        'additionalProperties': False,
    }

    def assess(self, args, ctx):
        action = args.get('action')
        valid = action in ('list', 'create', 'pause', 'resume', 'delete')
        return Assessment(
            Risk.READ if action == 'list' else Risk.EXECUTE,
            f'{action} scheduled work in {ctx.root}',
            invalid=None if valid else 'Unknown schedule action.',
        )

    async def run(self, args, ctx):
        from omote.agent.manager import manager
        from omote.routers.schedules import service
        from omote.scheduling import Schedule

        if service is None:
            raise ToolError('Start the Omote daemon to use Scheduled tasks.')
        root = str(await asyncio.to_thread(Path(ctx.root).resolve))
        if args['action'] == 'list':
            jobs = [job for job in service.store.list() if job['spec']['root'] == root]
            return Output(json.dumps(jobs, ensure_ascii=False))
        if args['action'] == 'create':
            source = manager.get(ctx.session_id)
            if source is None:
                raise ToolError('Create scheduled work from a main Omote chat, or use the Scheduled panel.')
            inherited_tools = [name for name in source.tools if name != 'schedule']
            if not inherited_tools:
                raise ToolError(
                    'This chat has no tools to give a scheduled worker. Use Scheduled to choose its permissions.'
                )
            allowed = {'name', 'prompt', 'cadence', 'timezone', 'at', 'weekdays', 'interval_minutes', 'start_at'}
            task = args.get('task') or {}
            if not isinstance(task, dict) or set(task) - allowed:
                raise ToolError('Use task name, prompt, cadence and timing fields only.')
            try:
                spec = Schedule(
                    **task,
                    root=root,
                    mode='ask',
                    tools=inherited_tools,
                    provider=str(getattr(source.provider, 'provider_id', '') or ''),
                    model=source.model,
                )
                job = service.store.save(spec)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            return Output(json.dumps(job, ensure_ascii=False))
        job = service.store.get(args.get('id', ''))
        if not job or job['spec']['root'] != root:
            raise ToolError('No such scheduled task in this project.')
        try:
            if args['action'] == 'delete':
                service.store.delete(job['id'])
                return Output('Scheduled task deleted. Its chats remain in history.')
            spec = Schedule(**{**job['spec'], 'enabled': args['action'] == 'resume'})
            # Resuming a task with broader tools or permissions requires the UI.
            source = manager.get(ctx.session_id)
            if args['action'] == 'resume' and (
                spec.mode != 'ask' or not spec.tools or source is None or not set(spec.tools) <= set(source.tools)
            ):
                raise ToolError('Resume this task in Scheduled after reviewing its tools and permissions.')
            return Output(json.dumps(service.store.save(spec, job['id']), ensure_ascii=False))
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
