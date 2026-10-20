"""The same local setup actions offered by Studio and browser settings."""
import json

from fastapi import HTTPException

from omote.agent.tools.base import Assessment, Output, Tool, ToolError
from omote.protocol.agent import Risk


class SetupLocalTool(Tool):
    name = 'setup_local'
    description = ('Install and configure ComfyUI or Playwright without terminal commands. '
                   'ComfyUI installs a private runtime, detects the device, connects Studio and managed model folders, '
                   'and restarts with Omote. Playwright installs and tests a version-matched browser. '
                   'Start is asynchronous; poll status until ready or failed. Cancel keeps reusable downloads. '
                   'Stop only stops Omote-owned ComfyUI, and refuses while creations are queued/running.')
    input_schema = {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['status', 'start', 'cancel', 'stop']},
        'target': {'type': 'string', 'enum': ['comfyui', 'playwright']},
        'engine': {'type': 'string', 'enum': ['chromium', 'firefox', 'webkit']},
        'device': {'type': 'string', 'enum': ['auto', 'cpu']},
    }, 'required': ['action'], 'additionalProperties': False}

    def assess(self, args, ctx):
        action = args.get('action')
        valid = isinstance(action, str) and action in ('status', 'start', 'cancel', 'stop')
        if action != 'status':
            valid = valid and args.get('target') in ('comfyui', 'playwright')
        if action == 'stop':
            valid = valid and args.get('target') == 'comfyui'
        return Assessment(risk=Risk.READ if action == 'status' else Risk.EXECUTE,
                          summary=f'{action} local tools', invalid=None if valid else 'Choose a supported setup action and target.')

    async def run(self, args, ctx):
        from omote.routers import setup
        try:
            if args['action'] == 'status':
                result = await setup.status()
            elif args['action'] == 'start':
                result = await setup.start(args['target'], setup.SetupRequest(
                    engine=args.get('engine', 'chromium'), device=args.get('device', 'auto')))
            elif args['action'] == 'cancel':
                result = await setup.cancel(args['target'])
            else:
                result = await setup.stop()
        except (HTTPException, ValueError) as error:
            raise ToolError(str(getattr(error, 'detail', error))) from error
        # Logs are diagnostics, not instructions for the agent.
        if isinstance(result, dict):
            result = {k: ({a: b for a, b in v.items() if a != 'log'} if isinstance(v, dict) else v)
                      for k, v in result.items() if k != 'log'}
        return Output(content=json.dumps(result))
