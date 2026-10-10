"""Agent access to the same visible creative library used by Studio."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from omote.agent.tools.base import Assessment, Output, Tool, ToolContext, ToolError
from omote.protocol.agent import Risk

READ_ACTIONS = {'list_characters', 'get_character', 'recipe', 'list_assets', 'get_asset'}
WRITE_ACTIONS = {'save_character', 'update_character', 'prepare', 'import_generated', 'update_asset',
                 'delete_character', 'delete_asset'}


class CreativeLibraryTool(Tool):
    name = 'creative_library'
    description = (
        'Find and reuse the user’s saved story characters, reference pictures, videos and 3D assets. '
        'List or get records, read a character recipe, save or update character identity and consistency '
        'prompts, import an existing generated media_id, or correct/delete owned library records. '
        'prepare copies a character’s image references to the generation media store and returns prompt, '
        'negative_prompt, reference_media_ids and image (the first compatible media ID); it never calls '
        'a model. Then use media_params and generate_image/video with only parameters the provider supports. '
        'Identity references guide consistency but cannot guarantee identical results across models. '
        'GLB/GLTF/OBJ/STL assets can be managed and viewed; this tool does not claim to generate 3D meshes. '
        'All stored data is visible and editable in Studio’s Library. Never invent existing IDs.'
    )
    input_schema = {
        'type': 'object', 'required': ['action'],
        'properties': {
            'action': {'type': 'string', 'enum': sorted(READ_ACTIONS | WRITE_ACTIONS)},
            'id': {'type': 'string', 'description': 'Existing character or asset ID, for actions on one item.'},
            'q': {'type': 'string'}, 'project': {'type': 'string'},
            'kind': {'type': 'string', 'enum': ['', 'image', 'video', 'model']},
            'character_id': {'type': 'string'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
            'offset': {'type': 'integer', 'minimum': 0},
            'fields': {
                'type': 'object',
                'description': 'Character: name, description, appearance_prompt, negative_prompt, consistency_notes, '
                               'project, tags, reference_asset_ids. Asset: name, description, project, tags, character_ids. '
                               'import_generated: media_id plus optional name, project, tags, character_ids.',
            },
        },
    }

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        action = args.get('action')
        invalid = None
        if action not in READ_ACTIONS | WRITE_ACTIONS:
            invalid = 'Choose a supported creative library action.'
        elif action not in {'list_characters', 'list_assets', 'save_character', 'import_generated'} and not args.get('id'):
            invalid = 'This action needs an existing id.'
        return Assessment(risk=Risk.READ if action in READ_ACTIONS else Risk.WRITE,
                          summary=f'Creative library: {action}', invalid=invalid)

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        from omote.routers import creative

        action, ident = args.get('action'), str(args.get('id') or '')
        try:
            store, user = creative._store(), creative._user()
            options = {'q': str(args.get('q') or ''), 'project': str(args.get('project') or ''),
                       'offset': max(0, int(args.get('offset') or 0)),
                       'limit': max(1, min(50, int(args.get('limit') or 20)))}
            if action == 'list_characters':
                result = store.list_characters(user, **options)
                result['characters'] = [{k: c[k] for k in ('id', 'name', 'project', 'tags', 'reference_asset_ids')}
                                        for c in result['characters']]
            elif action == 'get_character':
                result = creative._require(store.get_character(user, ident))
            elif action == 'recipe':
                result = creative._require(store.recipe(user, ident))
            elif action == 'list_assets':
                result = store.list_assets(user, kind=str(args.get('kind') or ''),
                                           character_id=str(args.get('character_id') or ''), **options)
                result['assets'] = [{k: a[k] for k in ('id', 'name', 'kind', 'project', 'tags', 'character_ids', 'url')}
                                    for a in result['assets']]
            elif action == 'get_asset':
                result = creative._require(store.get_asset(user, ident))
            elif action == 'prepare':
                result = await creative.prepare_character(ident)
            elif action in {'save_character', 'update_character'}:
                fields = args.get('fields') or {}
                if action == 'update_character':
                    existing = creative._require(store.get_character(user, ident))
                    fields = {**{k: existing[k] for k in creative.CharacterBody.model_fields}, **fields}
                body = creative.CharacterBody.model_validate(fields)
                result = creative._require(store.save_character(user, body.model_dump(),
                                                                 ident if action == 'update_character' else None))
            elif action == 'import_generated':
                result = await creative.from_media(creative.MediaImportBody.model_validate(args.get('fields') or {}))
            elif action == 'update_asset':
                body = creative.AssetPatch.model_validate(args.get('fields') or {})
                result = creative._require(store.update_asset(user, ident, body.model_dump(exclude_unset=True)))
            elif action == 'delete_character':
                result = await creative.delete_character(ident)
            elif action == 'delete_asset':
                result = await creative.delete_asset(ident)
            else:
                raise ToolError('Unknown creative library action.')
        except HTTPException as exc:
            raise ToolError(str(exc.detail)) from exc
        except (ValueError, ValidationError, TypeError) as exc:
            raise ToolError(str(exc)) from exc
        return Output(content=json.dumps(result, ensure_ascii=False), display={'creative': result})


def creative_tools() -> list[Tool]:
    return [CreativeLibraryTool()]
