"""Lazy agent access to Studio production plans, using the same API validation."""
from __future__ import annotations

import json

from fastapi import HTTPException

from omote.agent.tools.base import Assessment, Output, Tool, ToolContext, ToolError
from omote.protocol.agent import Risk


class StudioProjectTool(Tool):
    name = 'studio_project'
    description = (
        'Plan and edit cinematic stories, video remixes, 3D blocking scenes, product campaigns, visual effects '
        'and narrated/faceless shorts. Plans are local and editable in Studio Projects. '
        'catalog returns templates, direction choices and the full project JSON schema. '
        'create makes an editable starter plan; get/list find real IDs; update replaces the full document '
        'using base_revision (never guess a revision). stage stores editable primitive objects, camera and light. '
        'prepare returns a shot prompt and owned reference media IDs without generating. '
        'render starts ONE selected shot, requires base_revision and kind, and records a take. It can cost money. '
        'Inspect media_params first and supply only supported params. takes checks saved results; never blindly retry an interrupted render. '
        'export_options lists local export availability and voice providers. assemble joins one selected take or owned '
        'library asset per shot into a local MP4 with optional captions. export_settings needs base_revision, '
        'selections [{shot_id, media_id OR asset_id}], captions boolean and audio original/silent/narration. '
        'Narration also needs an explicitly chosen provider/model/voice and can cost money. '
        'exports checks finished videos; cancel_export stops a job_id. setup_video downloads and checks local video '
        'export tools when missing; track its job with media_job. Never claim a plan or preview is a finished video.'
    )
    input_schema = {
        'type': 'object', 'required': ['action'], 'properties': {
            'action': {'type': 'string', 'enum': ['catalog', 'list', 'get', 'create', 'update', 'prepare', 'render', 'takes',
                                               'export_options', 'assemble', 'exports', 'cancel_export', 'setup_video']},
            'id': {'type': 'string'}, 'shot_id': {'type': 'string'},
            'template': {'type': 'string'}, 'name': {'type': 'string'}, 'brief': {'type': 'string'},
            'base_revision': {'type': 'integer'}, 'document': {'type': 'object'},
            'kind': {'type': 'string', 'enum': ['image', 'video']},
            'provider': {'type': 'string'}, 'model': {'type': 'string'}, 'params': {'type': 'object'},
            'export_settings': {'type': 'object'}, 'job_id': {'type': 'string'},
        },
    }

    def assess(self, args: dict, ctx: ToolContext) -> Assessment:
        action = args.get('action')
        invalid = None
        if action not in {'catalog', 'list', 'get', 'create', 'update', 'prepare', 'render', 'takes',
                          'export_options', 'assemble', 'exports', 'cancel_export', 'setup_video'}:
            invalid = 'Choose a supported action.'
        elif action in {'get', 'update', 'prepare', 'render', 'takes', 'export_options', 'assemble', 'exports', 'cancel_export'} and not args.get('id'):
            invalid = 'An existing project ID is required.'
        elif action in {'prepare', 'render'} and not args.get('shot_id'):
            invalid = 'An existing shot ID is required.'
        elif action == 'render' and (not args.get('base_revision') or args.get('kind') not in {'image', 'video'}):
            invalid = 'Rendering needs a saved base_revision and image/video kind.'
        elif action == 'cancel_export' and not args.get('job_id'):
            invalid = 'An export job ID is required.'
        elif action == 'assemble':
            from omote.creative.assembly import Assembly
            try:
                Assembly.model_validate(args.get('export_settings', {}))
            except ValueError as exc:
                invalid = str(exc)
        network = action == 'render' or (action == 'assemble' and isinstance(args.get('export_settings'), dict)
                                         and args['export_settings'].get('audio') == 'narration')
        return Assessment(risk=Risk.EXECUTE if action == 'setup_video' else Risk.NETWORK if network else Risk.WRITE if action in {'create', 'update', 'prepare', 'assemble', 'cancel_export'} else Risk.READ,
                          summary=f'Studio project: {action}', invalid=invalid)

    async def run(self, args: dict, ctx: ToolContext) -> Output:
        from omote.creative.projects import Project
        from omote.routers import creative
        try:
            action, ident = args.get('action'), args.get('id', '')
            if action == 'catalog':
                result = {**await creative.project_catalog(), 'project_schema': Project.model_json_schema()}
            elif action == 'list':
                result = await creative.projects()
            elif action == 'get':
                result = await creative.get_project(ident)
            elif action == 'create':
                result = await creative.create_project(creative.NewProject(
                    template=args.get('template', 'cinema'), name=args.get('name', ''), brief=args.get('brief', '')))
            elif action == 'update':
                result = await creative.save_project(ident, creative.SaveProject(
                    base_revision=args.get('base_revision'), document=args.get('document', {})))
            elif action == 'takes':
                result = await creative.project_takes(ident)
            elif action == 'export_options':
                result = await creative.export_options(ident)
            elif action == 'exports':
                result = await creative.project_exports(ident)
            elif action == 'assemble':
                result = await creative.assemble_project(ident, creative.Assembly.model_validate(args.get('export_settings', {})))
            elif action == 'cancel_export':
                result = await creative.cancel_export(ident, args.get('job_id', ''))
            elif action == 'setup_video':
                from omote.routers.media import setup_video
                result = await setup_video()
            elif action == 'render':
                result = await creative.run_project_shot(ident, args.get('shot_id', ''), creative.ProjectRun(
                    base_revision=args.get('base_revision'), kind=args.get('kind'),
                    provider=args.get('provider'), model=args.get('model'), params=args.get('params', {})))
            elif action == 'prepare':
                result = await creative.prepare_shot(ident, args.get('shot_id', ''))
            else:
                raise ToolError('Unknown Studio project action.')
        except HTTPException as exc:
            raise ToolError(str(exc.detail)) from exc
        except (ValueError, TypeError) as exc:
            raise ToolError(str(exc)) from exc
        return Output(content=json.dumps(result, ensure_ascii=False))
