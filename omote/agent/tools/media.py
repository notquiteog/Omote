"""Making pictures and video, from inside a turn.

The tools are thin — the service does the work — but two things about them are
deliberate.

**Generating is a network call, not a write.** It spends money on a hosted
provider and GPU time on a local one, and it produces a file in the media
library rather than in the working root. Grading it `NETWORK` puts it at the
same level as fetching a page: it runs unattended under `trusted`, and it is
confirmed under `ask`, which is the right place for something that can cost
real money per call.

**The model is told what it can turn before it turns anything.** `image_params`
exists because a model asked to "make it more detailed" will otherwise invent
a parameter name, have it silently dropped, and report success. Asking first
costs one step and is the difference between a considered setting and a
plausible-looking one.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from omote.agent.tools.base import Assessment, Output, Tool, ToolContext, ToolError
from omote.media.edit import EditRefused, EditUnavailable
from omote.music import midi
from omote.protocol.agent import Risk
from omote.providers.gimp import GimpError
from omote.providers.registry import NoProviderError


class _MediaTool(Tool):
    def __init__(self, service: Any) -> None:
        self.service = service


class MediaParamsTool(_MediaTool):
    name = 'media_params'
    description = (
        'Ask what an image or video generator on this machine can be told to do: which '
        'models it has, and every parameter with its range and what it means. Do this '
        'before generating with anything but a plain prompt — a parameter that is not in '
        'this list is dropped, and you will be told it worked.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'kind': {
                'type': 'string',
                'description': "'image', 'video' or 'music'. Default image.",
            },
            'provider': {'type': 'string', 'description': 'Leave empty for the configured one.'},
            'model': {'type': 'string'},
        },
    }

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        kind = str(args.get('kind') or 'image')
        if kind not in ('image', 'video', 'music'):
            return Assessment(
                risk=Risk.READ, summary='', invalid="kind must be 'image', 'video' or 'music'"
            )
        return Assessment(risk=Risk.READ, summary=f'ask what the {kind} generator can do')

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        try:
            info = await self.service.describe(
                args.get('kind', 'image') or 'image', args.get('provider'), args.get('model')
            )
        except NoProviderError as exc:
            raise ToolError(str(exc)) from exc

        lines = [
            f'{info["provider"]}{" (local)" if info["local"] else ""}, model {info["model"] or "(none chosen)"}.',
        ]
        if info['models']:
            names = ', '.join(str(m.get('id')) for m in info['models'][:40])
            lines.append(f'Models: {names}')
        lines.append('')
        for param in info['params']:
            bits = [f'{param["name"]} ({param["kind"]}'
                    + (f', {param["min"]}–{param["max"]}' if param['min'] is not None else '') + ')']
            if param['options']:
                bits.append('one of: ' + ', '.join(param['options'][:20]))
            if param['default'] is not None:
                bits.append(f'default {param["default"]}')
            if param['help']:
                bits.append(param['help'])
            lines.append('  ' + ' — '.join(bits))

        return Output(content='\n'.join(lines), display={'params': info['params'], 'models': info['models']})


class GenerateImageTool(_MediaTool):
    name = 'generate_image'
    description = (
        'Make an image from a description. `params` takes anything media_params listed for '
        'this backend — steps, cfg_scale, sampler, width, height, seed and so on. Say what '
        'you set and why when you report back: a picture with no recipe cannot be iterated on.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'prompt': {'type': 'string'},
            'provider': {'type': 'string'},
            'model': {'type': 'string'},
            'params': {
                'type': 'object',
                'description': 'Backend parameters, from media_params. Anything not in that '
                               'list is ignored and reported back to you.',
            },
        },
        'required': ['prompt'],
    }

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        prompt = (args.get('prompt') or '').strip()
        if not prompt:
            return Assessment(risk=Risk.NETWORK, summary='', invalid='a prompt is required')
        count = (args.get('params') or {}).get('n', 1)
        shown = prompt if len(prompt) <= 70 else prompt[:67] + '...'
        return Assessment(
            risk=Risk.NETWORK,
            summary=f'generate {count} image(s): {shown!r}',
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        try:
            result = await self.service.generate_image(
                args['prompt'],
                provider=args.get('provider'),
                model=args.get('model'),
                params=args.get('params') or {},
            )
        except NoProviderError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f'the image backend failed: {exc}') from exc

        media = result['media']
        if not media:
            raise ToolError('the backend returned no images')

        lines = [
            f'Generated {len(media)} image(s) on {result["provider"]} '
            f'({result["model"]}) in {result["took_ms"]}ms.'
        ]
        for item in media:
            seed = f', seed {item["seed"]}' if item.get('seed') is not None else ''
            lines.append(f'  {item["id"]}{seed} — {item["url"]}')
            if item.get('notes'):
                lines.append(f'    the backend revised the prompt to: {item["notes"]}')
        if result['ignored']:
            # Said plainly rather than swallowed. A model that thinks it set
            # the sampler and did not will report a choice it never made.
            lines.append(
                'Ignored, because this backend has no such parameter: '
                + ', '.join(result['ignored'])
            )

        return Output(content='\n'.join(lines), display={'media': media, 'provider': result['provider']})


class GenerateVideoTool(_MediaTool):
    name = 'generate_video'
    description = (
        'Start making a video. This takes minutes, so it returns a job id immediately — '
        'check it with media_job. `model` is a workflow template installed on this machine; '
        'media_params lists them.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'prompt': {'type': 'string'},
            'provider': {'type': 'string'},
            'model': {'type': 'string', 'description': 'The workflow template to run.'},
            'params': {'type': 'object'},
        },
        'required': ['prompt'],
    }

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        prompt = (args.get('prompt') or '').strip()
        if not prompt:
            return Assessment(risk=Risk.NETWORK, summary='', invalid='a prompt is required')
        shown = prompt if len(prompt) <= 70 else prompt[:67] + '...'
        return Assessment(risk=Risk.NETWORK, summary=f'generate a video: {shown!r} (minutes)')

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        try:
            job = self.service.start_video(
                args['prompt'],
                provider=args.get('provider'),
                model=args.get('model'),
                params=args.get('params') or {},
            )
        except NoProviderError as exc:
            raise ToolError(str(exc)) from exc

        return Output(
            content=(
                f'Started video job {job.id} on {job.provider}. This takes minutes. '
                f'Check it with media_job("{job.id}") — do not poll it more than once every '
                'twenty seconds or so, and tell the person it is running rather than sitting '
                'silently waiting for it.'
            ),
            display=job.to_json(),
        )


class MediaJobTool(_MediaTool):
    name = 'media_job'
    description = 'Check on a video job started with generate_video.'
    input_schema = {
        'type': 'object',
        'properties': {'job_id': {'type': 'string'}},
        'required': ['job_id'],
    }

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        return Assessment(risk=Risk.READ, summary=f'check video job {args.get("job_id", "")}')

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        job = self.service.jobs.get(args.get('job_id', ''))
        if job is None:
            raise ToolError(f'no such job: {args.get("job_id")!r}')
        data = job.to_json()
        if job.state in ('queued', 'running'):
            # `queued` and `running` are both "not finished", and they are
            # worth telling apart: a job that is still queued has not started
            # costing anything yet, and a model deciding whether to wait or
            # give up wants to know which it is. Reporting queued through the
            # catch-all below produced "queued: no reason given", which reads
            # as a failure.
            said = data['note'] or ('waiting to start' if job.state == 'queued' else 'running')
            done = f' ({round(data["progress"] * 100)}%)' if data['progress'] is not None else ''
            content = f'Not finished — {said}{done}, {data["elapsed"]}s in.'
        elif job.state == 'done':
            urls = ', '.join(m['url'] for m in data['media'])
            content = f'Finished in {data["elapsed"]}s: {urls}'
        else:
            content = f'{job.state}: {job.error or "no reason given"}'
        return Output(content=content, display=data)


class ImportWorkflowTool(_MediaTool):
    name = 'import_workflow'
    description = (
        'Install a ComfyUI graph as a video or image template. `graph` is the JSON from '
        "ComfyUI's Save (API format) — not plain Save, which writes the editor layout "
        'rather than the graph. The prompt, seed, size and sampler settings are found and '
        'turned into parameters automatically; what it found is returned.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'description': 'What to call the template.'},
            'graph': {'type': 'object', 'description': 'The API-format export, whole.'},
            'path': {
                'type': 'string',
                'description': 'A file to read the graph from instead, if you have one on disk.',
            },
        },
        'required': ['name'],
    }

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        name = (args.get('name') or '').strip()
        if not name:
            return Assessment(risk=Risk.WRITE, summary='', invalid='a name is required')
        if not args.get('graph') and not args.get('path'):
            return Assessment(risk=Risk.WRITE, summary='', invalid='pass either graph or path')
        return Assessment(risk=Risk.WRITE, summary=f'install the workflow template {name!r}')

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        from omote.agent.tools.base import resolve_in_root
        from omote.media.workflow import install
        from omote.providers.comfyui import WORKFLOW_DIR

        graph = args.get('graph')
        if not graph:
            path = resolve_in_root(args['path'], ctx)
            try:
                graph = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ToolError(f'could not read {path}: {exc}') from exc

        # A file operation, so no connection is constructed for it — see
        # `workflow.install`.
        result = install(WORKFLOW_DIR, args['name'], graph)
        tokens = ', '.join(result['tokens']) or 'none'
        warning = (
            ''
            if 'prompt' in result['tokens']
            else '\nNo prompt token was found — the importer could not identify the positive '
                 'conditioning node. Put %prompt% into the right text node yourself, or this '
                 'template will always generate the prompt it was exported with.'
        )
        return Output(
            content=f'Installed {result["id"]}. Parameters found: {tokens}.{warning}',
            display=result,
        )


class GenerateMusicTool(_MediaTool):
    name = 'generate_music'
    description = (
        'Make a piece of music from words. This takes minutes, so it returns a job id '
        'immediately — check it with media_job.\n\n'
        'Describe the genre, the instrumentation and the mood; that is what these models '
        'respond to. Set instrumental true for music with no singing: a model asked for '
        '"instrumental" in the words still writes a vocal line surprisingly often, and the '
        'flag is the reliable way to say it.\n\n'
        'For a score you will finish yourself — something to drop into FL Studio, Ableton or '
        'Logic — use compose_music instead. That writes a MIDI file, which is editable; this '
        'writes a recording, which is not.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'prompt': {'type': 'string', 'description': 'Genre, instruments, mood. In words.'},
            'provider': {'type': 'string'},
            'model': {'type': 'string'},
            'params': {
                'type': 'object',
                'description': 'bars, bpm, instrumental, n, seed — media_params lists what this '
                               'backend actually takes.',
            },
        },
        'required': ['prompt'],
    }

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        prompt = (args.get('prompt') or '').strip()
        if not prompt:
            return Assessment(risk=Risk.NETWORK, summary='', invalid='a prompt is required')
        shown = prompt if len(prompt) <= 70 else prompt[:67] + '...'
        return Assessment(risk=Risk.NETWORK, summary=f'generate music: {shown!r} (minutes)')

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        try:
            job = self.service.start_music(
                args['prompt'],
                provider=args.get('provider'),
                model=args.get('model'),
                params=args.get('params') or {},
            )
        except NoProviderError as exc:
            raise ToolError(str(exc)) from exc

        return Output(
            content=(
                f'Started music job {job.id} on {job.provider}. This takes minutes. '
                f'Check it with media_job("{job.id}") — do not poll it more than once every '
                'twenty seconds or so, and tell the person it is running rather than sitting '
                'silently waiting for it.'
            ),
            display=job.to_json(),
        )


class ComposeMusicTool(Tool):
    name = 'compose_music'

    def __init__(self, service: Any = None) -> None:
        # The media service, for the store — and optional, because composing a
        # MIDI file needs no backend at all. Without one the file is still
        # produced and just is not filed, which is the right failure for a
        # tool whose whole point is that it works on a machine with nothing
        # configured.
        self.service = service

    description = (
        'Write a score as a MIDI file, rather than a recording. This is the one to use for '
        'anything the person is going to finish themselves — a loop to drop into FL Studio, '
        'a chord progression to write a melody over, a part for another instrument.\n\n'
        'You decide the music; the file is written for you. Choose a key and a progression '
        '("A minor, ii–V–I", "C major, I–V–vi–IV") and say how many bars, and you get a chord '
        'part held a bar at a time with a bass underneath it, ending back on the key so a loop '
        'lands somewhere. Add your own notes on top with the notes parameter if you want a '
        'melody — pitch is a MIDI number where 60 is middle C, and start and length are in '
        'beats.\n\n'
        'It does not write FL Studio\'s own .flp format, which is proprietary and unreadable '
        'from outside the application, and it will not pretend to. It writes a Standard MIDI '
        'File, which FL Studio, Ableton, Logic, Reaper and Cubase all import — and it is the '
        'same either way whether the person has FL Studio or not.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'key': {
                'type': 'string',
                'description': 'e.g. "A minor", "C major". D, E, F, G, A and B, major or minor.',
            },
            'progression': {
                'type': 'string',
                'description': 'Named ("I–V–vi–IV", "ii–V–I", "12-bar blues", "turnaround") or '
                               'Roman numerals separated by hyphens ("bVII-I-V-i").',
            },
            'bars': {'type': 'integer', 'description': 'How long. Default 8.'},
            'bpm': {'type': 'number', 'description': 'Default 120.'},
            'swing': {
                'type': 'number',
                'description': '0 for straight, up to about 0.6 for swung. The off-beat lands '
                               'between the beats rather than on the next one, which is what '
                               'swing is.',
            },
            'title': {'type': 'string'},
            'notes': {
                'type': 'array',
                'description': 'Optional melody: {pitch, start, length, velocity}. Pitch is a '
                               'MIDI number (60 is middle C, 69 is A440); start and length are '
                               'in beats.',
                'items': {
                    'type': 'object',
                    'properties': {
                        'pitch': {'type': 'integer'},
                        'start': {'type': 'number'},
                        'length': {'type': 'number'},
                        'velocity': {'type': 'integer'},
                    },
                    'required': ['pitch', 'start', 'length'],
                },
            },
        },
        'required': ['key', 'progression'],
    }

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        key = str(args.get('key') or '').strip()
        progression = str(args.get('progression') or '').strip()
        if not key:
            return Assessment(risk=Risk.WRITE, summary='', invalid='key is required')
        if not progression:
            return Assessment(risk=Risk.WRITE, summary='', invalid='progression is required')
        if key not in midi.KEYS:
            return Assessment(
                risk=Risk.WRITE,
                summary='',
                invalid=f'key must be one of: {", ".join(sorted(midi.KEYS))}',
            )
        # Graded from the arguments rather than from the name: a four-bar loop
        # and a sixty-bar one are the same tool and neither is a change to
        # anything that already exists.
        return Assessment(risk=Risk.WRITE, summary=f'write a MIDI score: {key}, {progression}')

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Assessment | Output:
        key = str(args['key']).strip()
        progression = str(args['progression']).strip()
        bars = max(1, min(512, int(args.get('bars') or 8)))
        bpm = float(args.get('bpm') or 120)
        swing = max(0.0, min(0.7, float(args.get('swing') or 0.0)))
        title = str(args.get('title') or '')

        try:
            song = midi.arrange(key, progression, bars=bars, bpm=bpm, swing=swing, title=title)
        except midi.MidiError as exc:
            # Raised rather than passed back as prose: a progression that
            # cannot be written is a refusal, and a refusal in the tool result
            # is what lets the model try a different one rather than explaining
            # the format to the person.
            return Assessment(risk=Risk.WRITE, summary='', invalid=str(exc))

        melody = args.get('notes') or []
        if melody:
            part = song.track(name='melody', program=0, channel=2, volume=104)
            for item in melody:
                try:
                    part.add(
                        int(item['pitch']),
                        float(item.get('start', 0)),
                        float(item.get('length', 1)),
                        int(item.get('velocity', 96)),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    return Assessment(
                        risk=Risk.WRITE,
                        summary='',
                        invalid=f'a note needs pitch, start and length as numbers, and one of them is not: {exc}',
                    )

        data = midi.render(song)

        # Written through the media store rather than straight to a path, so
        # the result has the same sidecar as everything else the panel shows —
        # the key, the progression and the tempo — and so it can be reproduced
        # by changing one thing and running it again.
        if self.service is not None:
            media = self.service.store.add(
                data,
                kind='music',
                media_type='audio/midi',
                prompt=f'{key} {progression}',
                params={'key': key, 'progression': progression, 'bars': bars, 'bpm': bpm, 'swing': swing},
            )
            return Output(
                content=(
                    f'Wrote a {bars}-bar score in {key} ({progression}) at {bpm:g} bpm. '
                    f'{len(melody)} melody notes.\n'
                    f'  {media.id} — {media.filename}\n\n'
                    'It is a Standard MIDI File. Open it in FL Studio, Ableton, Logic, Reaper or '
                    'Cubase, and edit it there — the chords and bass are meant to be a starting '
                    'point rather than a finished arrangement.'
                ),
                display={'media': [media.to_json()], 'key': key, 'progression': progression},
            )

        return Output(content=f'Wrote {len(data)} bytes of MIDI.', display={'bytes': len(data)})


def media_tools(service: Any) -> list[Tool]:
    """The generation tools this install can actually serve.

    Nothing is offered that would fail on its first call. An install with a
    diffusion server and no video backend gets `generate_image` and not
    `generate_video`; one with neither gets none of the generators, and the
    model is never told it can make pictures on a machine that cannot.

    **`compose_music` is the exception, and it is offered on an install with
    no backend at all.** Composing a MIDI file needs nothing configured, costs
    nothing, and cannot fail on infrastructure — the only way it refuses is a
    progression that cannot be written, which is a refusal the model can act
    on rather than an error to recover from. It is also the only path to
    "make me something for FL Studio", so withholding it on a machine with no
    music model connected would leave the most reliable way to make music
    unavailable on exactly the installs that could least afford to be told no.
    """
    kinds = service.can_generate()
    tools: list[Tool] = []

    if kinds:
        tools.append(MediaParamsTool(service))
    if 'image' in kinds:
        tools.append(GenerateImageTool(service))
    if 'music' in kinds:
        tools.append(GenerateMusicTool(service))
    if 'video' in kinds:
        # The other three only mean anything with video: a video is a job, so
        # it needs something to check on, and a job needs a workflow template
        # to run — which `import_workflow` is how you get.
        tools += [GenerateVideoTool(service), MediaJobTool(service), ImportWorkflowTool(service)]

    tools.append(ComposeMusicTool(service))
    # Editing is offered when there is something here that can edit, which is
    # asked of the machine rather than of the registry — see
    # `MediaService.can_edit` for why an editor is not a modality.
    if service.can_edit():
        tools += [EditMediaTool(service), MediaEditParamsTool(service)]
    return tools


class EditMediaTool(Tool):
    name = 'edit_media'
    description = (
        'Change a picture, a video or a clip somebody already has, and keep the result '
        'alongside the original. This is editing, not generating: nothing new is invented and '
        'nothing is overwritten.\n\n'
        'Images: crop, resize, rotate, flip, sharpen, convert format. These need no extra '
        'anything — Pillow does them.\n\n'
        'Video and audio: trim, cut out the middle, join two clips together, pull frames out, '
        'change the speed, drop the sound, take the sound out on its own, burn in a subtitle '
        'track, take a still. These need ffmpeg, and `media_edit_params` will tell you if this '
        'machine has it.\n\n'
        'You name files by the id the server gave them — upload one first, or use an id from '
        'generate_image. A path will not work and there is deliberately no way to pass one.\n\n'
        'This is a write: it makes a new file. It does not modify the original and it does not '
        'delete anything, so a bad trim costs nothing but the time.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'operation': {
                'type': 'string',
                'description': 'trim, cut, concat, frames, speed, mute, audio, subtitles, poster '
                               'for video; crop, resize, rotate, flip, convert, sharpen for images.',
            },
            'inputs': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Stored media ids. Two for concat, one otherwise.',
            },
            'params': {
                'type': 'object',
                'description': 'The arguments for that operation: start and length for a trim, '
                               'factor for a speed change, and so on. media_edit_params lists them.',
            },
        },
        'required': ['operation', 'inputs'],
    }

    def __init__(self, service: Any) -> None:
        self.service = service

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        operation = str(args.get('operation') or '').strip()
        inputs = args.get('inputs') or []
        if not operation:
            return Assessment(risk=Risk.WRITE, summary='', invalid='operation is required')
        if not isinstance(inputs, list) or not inputs:
            return Assessment(risk=Risk.WRITE, summary='', invalid='inputs is required')
        # A write, because it makes a file — and the summary names the file,
        # so the approval prompt says which one.
        said = f'{operation} {inputs[0]}'
        return Assessment(risk=Risk.WRITE, summary=said[:100])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        try:
            job = self.service.edit(
                str(args['operation']),
                [str(one) for one in args['inputs']],
                params=args.get('params') or {},
            )
        except NoProviderError as exc:
            raise ToolError(str(exc)) from exc
        except (EditUnavailable, EditRefused, GimpError) as exc:
            # Turned into an `invalid` on an empty Output, which is how this
            # codebase hands a model something to try something else about. An
            # exception becomes a refused turn, and a tool result in prose
            # does not reliably get read.
            return Output(content='', display={'error': str(exc)})

        try:
            await job.task
        except asyncio.CancelledError:
            raise
        if job.state == 'failed':
            raise ToolError(job.error or 'the edit failed without saying why')

        made = [m.to_json() for m in job.media]
        lines = [f'  {m["id"]} — {m["url"]}' for m in made]
        return Output(
            content=(
                f'{job.params.get("operation")} produced {len(made)} file'
                f'{"s" if len(made) != 1 else ""}:\n' + '\n'.join(lines)
                + '\n\nThe original is untouched. Each result has the operation and its arguments in '
                'its sidecar, so you can change one thing and run it again.'
            ),
            display={'media': made, 'operation': job.params.get('operation')},
        )


class MediaEditParamsTool(Tool):
    name = 'media_edit_params'
    description = (
        'What can be done to a picture or a clip on this machine, and what each operation needs. '
        'Ask before editing: a machine with no ffmpeg cannot trim a video and saying so is better '
        'than discovering it.'
    )
    input_schema = {
        'type': 'object',
        'properties': {'kind': {'type': 'string', 'description': "'image', 'video' or 'audio'."}},
        'required': [],
    }

    def __init__(self, service: Any) -> None:
        self.service = service

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        return Assessment(risk=Risk.READ, summary='ask what can be edited on this machine')

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        editors = self.service.editors(str(args.get('kind') or ''))
        if not editors:
            return Output(
                content=(
                    'Nothing on this machine can edit files. Images need Pillow (the desktop '
                    "extra); video and audio need ffmpeg, which the video extra bundles."
                ),
                display={'editors': []},
            )
        lines = []
        for one in editors:
            lines.append(f'  {one["id"]} — {one["label"]}')
            lines.append(f'    {one["params"][0]["help"]}')
            if one['params'][0].get('options'):
                lines.append(f'    operations: {", ".join(one["params"][0]["options"])}')
        return Output(content='\n'.join(lines), display={'editors': editors})
