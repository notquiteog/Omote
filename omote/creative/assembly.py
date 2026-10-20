"""Local, cancellable Studio finishing. Only explicitly selected narration uses a provider."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import tempfile
import time
import uuid
import wave
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from omote.config import config
from omote.creative.projects import Id, Record
from omote.media.edit import ffmpeg
from omote.media.service import Job
from omote.providers.base import Modality
from omote.providers.registry import Route, RouteSet

log = logging.getLogger(__name__)
SIZES = {'16:9': (1280, 720), '9:16': (720, 1280), '1:1': (720, 720), '4:5': (720, 900)}


class Selection(Record):
    shot_id: Id
    media_id: Id | None = None
    asset_id: Id | None = None

    @model_validator(mode='after')
    def one_source(self):
        if bool(self.media_id) == bool(self.asset_id):
            raise ValueError('Choose one generated take or library asset for each shot.')
        return self


class Assembly(Record):
    base_revision: int = Field(ge=1)
    selections: list[Selection] = Field(min_length=1, max_length=32)
    captions: bool = True
    audio: Literal['original', 'silent', 'narration'] = 'original'
    provider: str = Field(default='', max_length=200)
    model: str = Field(default='', max_length=300)
    voice: str = Field(default='', max_length=200)


def sources(projects, user, project, body, media_store):
    """A media ID must belong to this project's shot; a library ID must belong to this user."""
    chosen = {s.shot_id: s for s in body.selections}
    if len(chosen) != len(body.selections) or set(chosen) != {s['id'] for s in project['shots']}:
        raise ValueError('Choose exactly one image or video for every shot in this plan.')
    allowed = {(t['shot_id'], m['id']) for t in projects.takes(user, project['id'])
               for m in t['result'].get('media', []) if t['result']['state'] == 'done'}
    result = []
    for shot in project['shots']:
        choice = chosen[shot['id']]
        if choice.media_id:
            if (shot['id'], choice.media_id) not in allowed:
                raise ValueError(f'Choose a take belonging to “{shot["title"]}”.')
            media = media_store.get(choice.media_id)
            path = media_store.path(choice.media_id)
            mime = media.media_type if media else ''
        else:
            asset = projects.library.get_asset(user, choice.asset_id)
            path = projects.library.asset_path(user, choice.asset_id)
            mime = asset['media_type'] if asset else ''
        if not path or not path.is_file() or not mime.startswith(('image/', 'video/')):
            raise ValueError(f'The image or video for “{shot["title"]}” is missing. Choose another.')
        result.append((path.resolve(), mime))
    return result


async def command(args, cwd, *, check=True):
    """Keep cancellation attached to the process, including while draining its pipes."""
    process = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    drain = asyncio.create_task(process.communicate())
    try:
        async with asyncio.timeout(900):
            _, error = await asyncio.shield(drain)
    except BaseException:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(asyncio.shield(drain), 3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await drain
        else:
            await drain
        raise
    if check and process.returncode:
        detail = error.decode(errors='replace').strip()[-1500:]
        raise ValueError(f'Video export could not finish: {detail}')
    return process.returncode == 0


def caption_file(path, text, seconds, width, height):
    # ASS markup must never interpret a caption as override tags or drawing commands.
    safe = text.replace('\\', '＼').replace('{', '｛').replace('}', '｝')
    safe = safe.replace('\r\n', '\n').replace('\r', '\n').replace('\n', r'\N')
    safe = ''.join(c for c in safe if ord(c) >= 32)
    end = math.ceil(seconds * 100)
    stamp = f'{end // 360000}:{end // 6000 % 60:02}:{end // 100 % 60:02}.{end % 100:02}'
    size = round(min(width, height) / 23)
    margin = round(min(width, height) * .07)
    path.write_text(
        f'[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n'
        '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, '
        'BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, '
        'Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
        f'Style: Default,DejaVu Sans,{size},&H00FFFFFF,&H00FFFFFF,&H00181818,&H80000000,'
        f'0,0,0,0,100,100,0,0,1,2,1,2,{margin},{margin},{margin},1\n'
        '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
        f'Dialogue: 0,0:00:00.00,{stamp},Default,,0,0,0,,{safe}\n', encoding='utf-8',
    )


async def speech(provider, text, model, voice, target):
    rate = provider.output_sample_rate
    if not isinstance(rate, int) or not 8000 <= rate <= 192000:
        raise ValueError('The voice provider did not declare a usable audio sample rate.')
    count = 0
    # PCM streams may split samples between chunks. wave writes their bytes unchanged.
    with wave.open(str(target), 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        async with asyncio.timeout(180):
            async with contextlib.aclosing(provider.synthesize(
                text, model=model, voice=voice, fmt='pcm16', sample_rate=rate,
            )) as stream:
                async for chunk in stream:
                    count += len(chunk)
                    if count > rate * 2 * 120:
                        raise ValueError('A voiceover exceeded two minutes. Shorten this shot’s script.')
                    audio.writeframesraw(chunk)
    if not count or count % 2:
        raise ValueError('The voice provider returned empty or incomplete audio.')
    return count / (rate * 2)


def start(service, projects, user, project, body):
    binary = ffmpeg()
    if not binary:
        raise ValueError('Video export needs FFmpeg. Ask the agent to install the video tools, then try again.')
    if sum(j.model == 'studio-export' and j.state in {'queued', 'running'} for j in service.jobs.values()) >= 2:
        raise ValueError('Two videos are already exporting. Wait for one to finish or cancel it.')
    inputs = sources(projects, user, project, body, service.store)
    tts = None
    if body.audio == 'narration':
        if not any(s['narration'].strip() for s in project['shots']):
            raise ValueError('Add a voiceover script to at least one shot first.')
        if not body.provider or not body.model:
            raise ValueError('Choose a voice provider and model before generating narration.')
        tts, _, _ = service.registry.resolve(
            Modality.TTS, RouteSet(routes={Modality.TTS: Route(body.provider, body.model)},
                                   local_only=config.local_only),
        )
    job = Job(id=uuid.uuid4().hex[:16], kind='video', prompt=project['name'],
              provider='ffmpeg', model='studio-export',
              params={'project_id': project['id'], 'revision': project['revision'], **body.model_dump()})
    # Register provenance before scheduling any work or making a provider request.
    projects.track_export(project, {'project': project, 'settings': body.model_dump()}, job)
    service._prune()
    service.jobs[job.id] = job
    job.task = asyncio.create_task(render(job, service.store, binary, project, body, inputs, tts))

    def done(_):
        if job.state in {'queued', 'running'}:  # cancellation before the coroutine first runs
            job.state = 'cancelled'
            job.finished_at = time.time()
        projects.finish_export(job)
    job.task.add_done_callback(done)
    return job


async def render(job, store, binary, project, body, inputs, tts):
    try:
        job.state = 'running'
        width, height = SIZES[project['aspect_ratio']]
        durations = []
        with tempfile.TemporaryDirectory(prefix='omote-studio-') as directory:
            scratch = Path(directory)
            base = [binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-filter_threads', '1']
            for index, (shot, (path, mime)) in enumerate(zip(project['shots'], inputs, strict=True)):
                job.note = f'Shot {index + 1} of {len(inputs)} · {shot["title"]}'
                seconds = float(shot['seconds'])
                audio_path = None
                if tts and shot['narration'].strip():
                    job.note += ' · Generating narration'
                    audio_path = scratch / f'voice-{index}.wav'
                    spoken = await speech(tts, shot['narration'], body.model, body.voice, audio_path)
                    seconds = max(seconds, math.ceil((spoken + .15) * 30) / 30)
                durations.append(seconds)
                image = mime.startswith('image/')
                args = base + (['-loop', '1', '-framerate', '30'] if image else [])
                args += ['-protocol_whitelist', 'file,pipe', '-i', str(path)]
                has_audio = False
                if body.audio == 'original' and not image:
                    has_audio = await command(base + ['-protocol_whitelist', 'file,pipe', '-i', str(path),
                        '-map', '0:a:0', '-frames:a', '1', '-f', 'null', '-'], scratch, check=False)
                if audio_path:
                    args += ['-i', str(audio_path)]
                elif not has_audio:
                    args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
                filters = (f'setpts=PTS-STARTPTS,scale=max(2\\,trunc(iw*sar/2)*2):ih,setsar=1,'
                           f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                           f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,'
                           f'tpad=stop_mode=clone:stop_duration={seconds:.6f}')
                if body.captions and shot['caption'].strip():
                    caption_file(scratch / f'caption-{index}.ass', shot['caption'], seconds, width, height)
                    filters += f',subtitles=caption-{index}.ass'
                args += ['-map', '0:v:0', '-map', '0:a:0' if has_audio else '1:a:0',
                         '-vf', filters, '-af', 'asetpts=PTS-STARTPTS,aresample=48000,apad',
                         '-t', f'{seconds:.6f}', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
                         '-pix_fmt', 'yuv420p', '-threads', '2', '-c:a', 'pcm_s16le', '-ar', '48000',
                         '-ac', '2', '-map_metadata', '-1', f'clip-{index}.mkv']
                job.note = f'Rendering shot {index + 1} of {len(inputs)} · {shot["title"]}'
                await command(args, scratch)
                job.progress = (index + 1) / (len(inputs) + 1)
            job.note = 'Joining shots and saving your video'
            (scratch / 'clips.txt').write_text(''.join(f"file 'clip-{i}.mkv'\n" for i in range(len(inputs))))
            await command(base + ['-f', 'concat', '-safe', '1', '-i', 'clips.txt',
                                  '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                                  '-movflags', '+faststart', '-map_metadata', '-1', 'finished.mp4'], scratch)
            job.params['shot_durations'] = durations
            job.media = [store.add((scratch / 'finished.mp4').read_bytes(), kind='video', media_type='video/mp4',
                                  prompt=project['name'], provider='ffmpeg', model='studio-export', params=job.params)]
        job.progress = 1
        job.state = 'done'
        job.note = f'Ready · {sum(durations):g} seconds'
    except asyncio.CancelledError:
        job.state = 'cancelled'
        job.note = 'Export cancelled'
        raise
    except Exception as exc:
        log.exception('Studio export %s failed', job.id)
        job.state = 'failed'
        job.error = str(exc)
    finally:
        job.finished_at = time.time()
