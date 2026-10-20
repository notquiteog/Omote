"""Managed model actions share the same service as the UI and normal approvals."""

import json
from typing import Any

from omote.agent.tools.base import Assessment, Output, Tool, ToolContext, ToolError
from omote.protocol.agent import Risk


class LocalModelsTool(Tool):
    name = 'local_models'
    description = ('List managed GGUF or ComfyUI models/workflows and compatibility/disk checks; download, resume or cancel verified model/runtime '
                   'downloads; start or stop the owned local GPU/CPU runtime; remove model files. '
                   'Call list first for IDs, sizes and requirements. Download cancellation keeps partial data for resume. '
                   'Prepare automatically installs the runtime, downloads/resumes the model and starts it. '
                   'Use prepare for a complete setup; start needs existing files. No cloud fallback. Never stops external servers.')
    input_schema = {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['list', 'prepare', 'cancel_setup', 'download', 'cancel', 'start', 'stop', 'remove']},
        'id': {'type': 'string', 'description': 'Managed artifact ID from list, required except list/stop/cancel_setup.'},
        'mode': {'type': 'string', 'enum': ['auto', 'gpu', 'cpu'], 'default': 'auto'},
        'library': {'type': 'string', 'enum': ['chat', 'comfyui'], 'default': 'chat',
                    'description': 'ComfyUI supports list/download/cancel/remove; use Studio for live workflow checks and generation.'},
    }, 'required': ['action'], 'additionalProperties': False}

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        action = args.get('action')
        risks = {'list': Risk.READ, 'download': Risk.NETWORK, 'cancel': Risk.EXECUTE,
                 'start': Risk.EXECUTE, 'stop': Risk.EXECUTE, 'prepare': Risk.EXECUTE,
                 'cancel_setup': Risk.EXECUTE, 'remove': Risk.DESTRUCTIVE}
        invalid = None
        if not isinstance(action, str) or action not in risks or args.get('mode', 'auto') not in ('auto', 'gpu', 'cpu'):
            invalid = 'Use a supported action and auto/gpu/cpu mode.'
        elif action not in ('list', 'stop', 'cancel_setup') and (not isinstance(args.get('id'), str) or not args['id']):
            invalid = 'Use an artifact ID from local_models list.'
        elif args.get('library', 'chat') not in ('chat', 'comfyui'):
            invalid = 'Choose the chat or comfyui library.'
        elif args.get('library') == 'comfyui' and action in ('start', 'stop', 'prepare', 'cancel_setup'):
            invalid = 'ComfyUI assets use your configured ComfyUI runtime. Start/stop manages the chat runtime only.'
        risk = risks.get(action, Risk.EXECUTE) if isinstance(action, str) else Risk.EXECUTE
        return Assessment(risk=risk, summary=f'{action} managed local model {args.get("id", "")}'.strip(),
                          invalid=invalid)

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        from omote.local_models.downloads import ModelError
        from omote.routers.local_models import service

        if service is None:
            raise ToolError('Managed local models are unavailable. Start the Omote daemon or use System → Local models.')
        action, ident, mode = args['action'], args.get('id'), args.get('mode', 'auto')
        try:
            if args.get('library') == 'comfyui':
                if action != 'list':
                    if action not in ('download', 'cancel', 'remove'):
                        raise ModelError('Use list/download/cancel/remove for ComfyUI assets.')
                    await getattr(service.comfy, action)(ident)
                return Output(content=json.dumps(await service.comfy.snapshot()), display={'summary': f'{action} ComfyUI assets'})
            if action == 'download':
                await service.download(ident, mode)
            elif action == 'cancel':
                await service.cancel(ident)
            elif action == 'start':
                await service.start(ident, mode)
            elif action == 'stop':
                await service.stop()
            elif action == 'prepare':
                await service.prepare(ident, mode)
            elif action == 'cancel_setup':
                await service.cancel_setup()
            elif action == 'remove':
                await service.remove(ident)
            data = await service.snapshot(mode)
        except (ModelError, KeyError, OSError) as error:
            raise ToolError(str(error)) from error
        return Output(content=json.dumps(data), display={'summary': f'{action} managed local models'})
