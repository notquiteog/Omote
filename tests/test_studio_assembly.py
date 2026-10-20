"""Real FFmpeg output, provenance, isolation and cancellation for the finishing workflow."""
import asyncio
import io
import json
import math
import shutil
import struct
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from omote.config import config
from omote.creative import assembly
from omote.creative.project_tool import StudioProjectTool
from omote.creative.projects import Projects
from omote.media.service import Job, MediaService
from omote.protocol.agent import Risk
from omote.providers.base import Modality, ProviderInfo
from omote.providers.registry import ProviderRegistry
from omote.routers import creative, media
from tests.test_creative_library import client as client
from tests.test_creative_library import upload
from tests.test_studio_projects import create, save


@pytest.fixture
def service(client, monkeypatch):
    made = MediaService(creative.media_store, ProviderRegistry())
    monkeypatch.setattr(media, 'service', made)
    monkeypatch.setattr(config, 'local_only', False)
    return made


def still(color='red'):
    target = io.BytesIO()
    Image.new('RGB', (96, 64), color).save(target, format='PNG')
    return target.getvalue()


def plan(client, count=2):
    project = create(client, 'cinema')
    project['shots'] = project['shots'][:count]
    for shot in project['shots']:
        shot['seconds'] = 1
    return save(client, project).json()


def payload(project, ids):
    return {'base_revision': project['revision'], 'selections': [
        {'shot_id': shot['id'], 'asset_id': ident} for shot, ident in zip(project['shots'], ids, strict=True)
    ]}


def wait(client, project, job_id):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        rows = client.get(f'/api/creative/projects/{project["id"]}/exports').json()['exports']
        row = next(row for row in rows if row['job_id'] == job_id)
        if row['result']['state'] not in {'queued', 'running'}:
            return row
        time.sleep(.04)
    pytest.fail(f'Export did not finish: {rows}')


def ffmpeg_required():
    if not assembly.ffmpeg() or not shutil.which('ffprobe'):
        pytest.skip('This integration test needs FFmpeg and ffprobe.')


def probe(path):
    return json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path),
    ]))


def frame(path, at):
    data = subprocess.check_output([assembly.ffmpeg(), '-v', 'error', '-ss', str(at), '-i', str(path),
                                    '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'])
    return Image.open(io.BytesIO(data)).convert('RGB')


@pytest.mark.parametrize('ratio,size', [('16:9', (1280, 720)), ('9:16', (720, 1280))])
def test_real_export_joins_shots_in_plan_order_and_survives_restart(client, service, monkeypatch, ratio, size):
    ffmpeg_required()
    project = plan(client)
    project['aspect_ratio'] = ratio
    project = save(client, project).json()
    ids = [upload(client, still(color), f'{color}.png')['id'] for color in ('red', 'blue')]
    body = payload(project, ids)
    body['selections'].reverse()  # The saved plan determines order, not request order.
    response = client.post(f'/api/creative/projects/{project["id"]}/exports', json=body)
    assert response.status_code == 202, response.text
    row = wait(client, project, response.json()['id'])
    assert row['result']['state'] == 'done', row
    path = service.store.path(row['result']['media'][0]['id'])
    metadata = probe(path)
    video = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height']) == size and video['pix_fmt'] == 'yuv420p'
    assert float(metadata['format']['duration']) == pytest.approx(2, abs=.1)
    first, second = frame(path, .4), frame(path, 1.4)
    r, g, b = first.getpixel((size[0] // 2, size[1] // 2))
    assert r > 220 and b < 25
    r, g, b = second.getpixel((size[0] // 2, size[1] // 2))
    assert b > 220 and r < 25
    monkeypatch.setattr(media, 'service', None)
    project['name'] = 'Changed after export'
    save(client, project)
    persisted = client.get(f'/api/creative/projects/{project["id"]}/exports').json()['exports'][0]
    assert persisted['snapshot']['project']['name'] == 'River story'
    assert persisted['revision'] == row['revision'] and persisted['result']['state'] == 'done'
    assert path.is_file()


class Voice:
    output_sample_rate = 24000

    def __init__(self):
        self.calls = []

    async def models(self):
        return [{'id': 'test-voice'}]

    async def voices(self):
        return [{'id': 'calm', 'model': 'test-voice'}]

    async def synthesize(self, text, **kwargs):
        self.calls.append((text, kwargs))
        # 2.2 seconds, longer than the one-second planned shot.
        yield b''.join(struct.pack('<h', round(6000 * math.sin(i * 2 * math.pi * 330 / 24000)))
                       for i in range(52800))


def register_voice(service, voice, local=True):
    service.registry.register(ProviderInfo(id='voice', label='Test voice', modalities={Modality.TTS}, local=local),
                              {Modality.TTS: voice})


def test_narration_extends_visuals_and_captions_are_actually_burned_in(client, service):
    ffmpeg_required()
    voice = Voice()
    register_voice(service, voice)
    project = plan(client, 1)
    project['shots'][0].update(narration='Hear the whole sentence.', caption=r'Hello {world}\N' + '\nSecond line')
    project = save(client, project).json()
    body = {**payload(project, [upload(client, still('black'))['id']]),
            'audio': 'narration', 'provider': 'voice', 'model': 'test-voice', 'voice': 'calm'}
    response = client.post(f'/api/creative/projects/{project["id"]}/exports', json=body)
    assert response.status_code == 202, response.text
    row = wait(client, project, response.json()['id'])
    assert row['result']['state'] == 'done', row
    assert len(voice.calls) == 1 and voice.calls[0][0] == 'Hear the whole sentence.'
    assert voice.calls[0][1]['voice'] == 'calm'
    path = service.store.path(row['result']['media'][0]['id'])
    metadata = probe(path)
    assert float(metadata['format']['duration']) >= 2.3
    assert any(s['codec_type'] == 'audio' for s in metadata['streams'])
    image = frame(path, .5)
    assert image.crop((0, 500, 1280, 720)).convert('L').getextrema()[1] > 200
    assert image.crop((0, 0, 1280, 300)).convert('L').getextrema()[1] < 10
    assert row['result']['params']['shot_durations'][0] >= 2.3


def test_original_audio_and_short_video_hold_last_frame(client, service, tmp_path):
    ffmpeg_required()
    source = tmp_path / 'short.mp4'
    subprocess.run([assembly.ffmpeg(), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=lime:s=160x90:r=24:d=0.4',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-shortest', str(source)], check=True)
    project = plan(client, 1)
    stored = service.store.add(source.read_bytes(), kind='video', media_type='video/mp4')
    take = Job(id='take-job', kind='video', prompt='', provider='test', model='', state='done', media=[stored],
               task=SimpleNamespace(add_done_callback=lambda fn: None))
    Projects(creative.store).track(project, project['shots'][0]['id'], {}, take)
    body = {'base_revision': project['revision'], 'selections': [
        {'shot_id': project['shots'][0]['id'], 'media_id': stored.id}], 'audio': 'original'}
    response = client.post(f'/api/creative/projects/{project["id"]}/exports', json=body)
    row = wait(client, project, response.json()['id'])
    assert row['result']['state'] == 'done', row
    path = service.store.path(row['result']['media'][0]['id'])
    r, g, b = frame(path, .85).getpixel((640, 360))
    assert g > 230 and r < 25
    audio = subprocess.check_output([assembly.ffmpeg(), '-v', 'error', '-i', str(path), '-t', '0.2',
                                     '-vn', '-f', 's16le', '-ac', '1', '-'])
    assert max(abs(n[0]) for n in struct.iter_unpack('<h', audio)) > 500


def test_export_rejects_stale_cross_user_and_unrelated_media_before_voice(client, service, monkeypatch):
    voice = Voice()
    register_voice(service, voice)
    project = plan(client, 1)
    project['shots'][0]['narration'] = 'Private script'
    project = save(client, project).json()
    foreign = creative.store.add_asset('someone-else', still(), filename='private.png')
    body = {**payload(project, [foreign['id']]), 'audio': 'narration',
            'provider': 'voice', 'model': 'test-voice', 'voice': 'calm'}
    path = f'/api/creative/projects/{project["id"]}/exports'
    assert client.post(path, json=body).status_code == 400
    unrelated = service.store.add(still(), kind='image', media_type='image/png')
    body['selections'] = [{'shot_id': project['shots'][0]['id'], 'media_id': unrelated.id}]
    assert client.post(path, json=body).status_code == 400
    body['selections'] *= 2
    assert client.post(path, json=body).status_code == 400
    body['base_revision'] = 1
    assert client.post(path, json=body).status_code == 409
    assert not voice.calls and not service.jobs
    monkeypatch.setattr(config, 'default_user', 'someone-else')
    assert client.get(path).status_code == 404
    assert client.post(path, json=body).status_code == 404
    assert client.delete(path + '/made-up').status_code == 404


def test_local_only_and_explicit_voice_selection(client, service, monkeypatch):
    register_voice(service, Voice(), local=False)
    project = plan(client, 1)
    project['shots'][0]['narration'] = 'Read this'
    project = save(client, project).json()
    body = {**payload(project, [upload(client, still())['id']]), 'audio': 'narration'}
    path = f'/api/creative/projects/{project["id"]}'
    assert client.post(path + '/exports', json=body).status_code == 400
    monkeypatch.setattr(config, 'local_only', True)
    assert client.get(path + '/export-options').json()['providers'] == []
    body.update(provider='voice', model='test-voice')
    assert client.post(path + '/exports', json=body).status_code == 503
    assert client.get(path + '/export-voices?provider=voice').status_code == 503
    assert not service.jobs


async def test_cancel_terminates_and_reaps_ffmpeg_process(tmp_path, monkeypatch):
    processes = []
    created = asyncio.Event()
    original = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        created.set()
        return process
    monkeypatch.setattr(assembly.asyncio, 'create_subprocess_exec', capture)
    task = asyncio.create_task(assembly.command([sys.executable, '-c', 'import time; time.sleep(90)'], tmp_path))
    await created.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None


def test_cancel_voice_export_closes_stream_and_records_terminal_state(client, service):
    class WaitingVoice(Voice):
        closed = False

        async def synthesize(self, text, **kwargs):
            self.calls.append(text)
            try:
                yield b'\0\0'
                await asyncio.sleep(60)
            finally:
                self.closed = True
    voice = WaitingVoice()
    register_voice(service, voice)
    project = plan(client, 1)
    project['shots'][0]['narration'] = 'A long narration'
    project = save(client, project).json()
    body = {**payload(project, [upload(client, still())['id']]), 'audio': 'narration',
            'provider': 'voice', 'model': 'test-voice'}
    path = f'/api/creative/projects/{project["id"]}/exports'
    response = client.post(path, json=body)
    assert response.status_code == 202
    deadline = time.monotonic() + 3
    while not voice.calls:
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert client.delete(path + '/' + response.json()['id']).json()['cancelled']
    row = wait(client, project, response.json()['id'])
    assert row['result']['state'] == 'cancelled' and voice.closed
    assert not row['result']['media']


def test_agent_assembly_risk_matches_explicit_narration():
    tool = StudioProjectTool()
    settings = {'base_revision': 1, 'selections': [{'shot_id': 'shot', 'asset_id': 'asset'}]}
    args = {'action': 'assemble', 'id': 'project', 'export_settings': settings}
    assert tool.assess(args, None).risk == Risk.WRITE
    settings.update(audio='narration', provider='voice', model='model')
    assert tool.assess(args, None).risk == Risk.NETWORK
    assert tool.assess({'action': 'assemble', 'id': 'project'}, None).invalid
