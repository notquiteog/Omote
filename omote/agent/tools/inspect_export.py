"""Export checks apply equally to Craft apps, Adobe, ComfyUI and FFmpeg."""

import asyncio
import json
import math

from omote.agent.tools.base import Assessment, Output, Tool, ToolError, resolve_in_root
from omote.creative.exports import inspect_file
from omote.protocol.agent import Risk


class InspectExportTool(Tool):
    name = 'inspect_export'
    description = ('Verify saved creative exports instead of trusting a save reply: file size, SHA-256, '
                   'decoded image dimensions/transparency, or ffprobe video/audio streams, codec, frame rate, '
                   'duration, color tags and audio presence. Optional expected properties fail visibly on '
                   'mismatch. Set decode=true for final video/audio delivery to decode the full stream with FFmpeg '
                   '(120-second check limit); metadata alone cannot detect every corrupt frame. '
                   'Native editable projects also need reopening in their app: a hash alone does '
                   'not prove layers or timeline fidelity. This reads files; it never changes an export.')
    input_schema = {'type': 'object', 'required': ['paths'], 'additionalProperties': False, 'properties': {
        'paths': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': {'type': 'string'}},
        'decode': {'type': 'boolean', 'default': False},
        'expected': {'type': 'object', 'additionalProperties': False, 'properties': {
            'width': {'type': 'integer', 'minimum': 1}, 'height': {'type': 'integer', 'minimum': 1},
            'duration': {'type': 'number', 'minimum': 0}, 'frame_rate': {'type': 'number', 'minimum': 0},
            'codec': {'type': 'string'}, 'format': {'type': 'string'}, 'kind': {'type': 'string'},
            'alpha': {'type': 'boolean'}, 'has_transparency': {'type': 'boolean'}, 'has_audio': {'type': 'boolean'},
        }},
    }}

    def assess(self, args, ctx):
        try:
            paths = args.get('paths')
            if not isinstance(paths, list) or not 1 <= len(paths) <= 20 or any(not isinstance(path, str) for path in paths):
                raise ToolError('Pass 1–20 export paths.')
            for path in paths:
                resolve_in_root(path, ctx)
            expected = args.get('expected', {})
            if type(args.get('decode', False)) is not bool:
                raise ToolError('decode must be true or false.')
            if not isinstance(expected, dict) or set(expected) - set(self.input_schema['properties']['expected']['properties']):
                raise ToolError('Use the documented expected media properties.')
            for key, value in expected.items():
                if key in ('width', 'height', 'duration', 'frame_rate') and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ToolError('Expected dimensions, duration and frame rate must be finite nonnegative numbers.')
            return Assessment(risk=Risk.READ, summary=f'Inspect {len(paths)} creative export(s)')
        except (ToolError, TypeError, ValueError, OSError) as error:
            return Assessment(risk=Risk.READ, summary='Inspect exports', invalid=str(error))

    async def run(self, args, ctx):
        check = self.assess(args, ctx)
        if check.invalid:
            raise ToolError(check.invalid)
        results = []
        for name in args['paths']:
            path = resolve_in_root(name, ctx)
            try:
                result = await asyncio.to_thread(inspect_file, path, args.get('expected'), decode=args.get('decode', False))
            except (OSError, ValueError) as error:
                result = {'path': str(path), 'ok': False, 'verified': False, 'errors': [str(error)]}
            results.append(result)
        return Output(content=json.dumps({'ok': all(row['ok'] for row in results), 'exports': results}),
                      display={'summary': f'Checked {len(results)} creative export(s)'})
