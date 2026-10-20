"""Production plans, user isolation, render provenance and media input contracts."""
import json
import time
from types import SimpleNamespace

import pytest

from omote.config import config
from omote.creative.project_tool import StudioProjectTool
from omote.creative.projects import Project, Projects
from omote.creative.store import CreativeStore
from omote.media.params import Param
from omote.media.service import MediaService
from omote.protocol.agent import Risk
from omote.providers.base import GeneratedMedia, Modality, ProviderInfo
from omote.providers.fal import FalProvider
from omote.providers.openapi_form import params_from_openapi
from omote.providers.registry import ProviderRegistry
from omote.providers.replicate import ReplicateProvider
from omote.routers import creative, media
from tests.test_creative_library import PNG, upload
from tests.test_creative_library import client as client

VIDEO = b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom'


def create(client, template='cinema'):
    response = client.post('/api/creative/projects', json={'template': template, 'name': 'River story', 'brief': 'A traveler finds a river.'})
    assert response.status_code == 201, response.text
    return response.json()


def document(project):
    return {k: project[k] for k in Project.model_fields}


def save(client, project):
    return client.put('/api/creative/projects/' + project['id'], json={
        'document': document(project), 'base_revision': project['revision']})


@pytest.mark.parametrize('template', ['cinema', 'remix', 'scene', 'marketing', 'effects', 'faceless'])
def test_templates_persist_across_restarts_and_do_not_generate(client, template):
    project = create(client, template)
    assert project['template'] == template and project['shots']
    reopened = Projects(CreativeStore(creative.store.root))
    assert reopened.get('owner', project['id']) == project
    assert reopened.takes('owner', project['id']) == []
    assert not creative.media_store.list()


def test_optimistic_updates_and_user_isolation(client, monkeypatch):
    project = create(client)
    project['name'] = 'Edited by the agent'
    response = save(client, project)
    assert response.status_code == 200 and response.json()['revision'] == 2
    project['name'] = 'Stale browser draft'
    assert save(client, project).status_code == 409
    assert client.get('/api/creative/projects/' + project['id']).json()['name'] == 'Edited by the agent'
    monkeypatch.setattr(config, 'default_user', 'other')
    assert client.get('/api/creative/projects').json()['projects'] == []
    for path in ('', '/takes'):
        assert client.get('/api/creative/projects/' + project['id'] + path).status_code == 404
    assert save(client, project).status_code == 404
    assert client.delete('/api/creative/projects/' + project['id']).status_code == 404


def test_prepare_composes_direction_and_references_without_narration_in_image(client):
    asset = upload(client)
    character = client.post('/api/creative/characters', json={'name': 'Aya', 'appearance_prompt': 'Green jacket',
        'negative_prompt': 'red coat', 'reference_asset_ids': [asset['id']]}).json()
    project = create(client, 'faceless')
    project.update(character_id=character['id'], reference_asset_ids=[asset['id']], direction={'lens': '35mm natural', 'lighting': 'Golden hour'})
    project['shots'][0].update(narration='Narration belongs on the audio track.', caption='A river.')
    project = save(client, project).json()
    result = client.post(f"/api/creative/projects/{project['id']}/shots/{project['shots'][0]['id']}/prepare").json()
    assert 'Green jacket' in result['prompt'] and '35mm natural' in result['prompt']
    assert 'Golden hour' in result['prompt']
    assert 'Narration belongs' not in result['prompt']
    assert result['narration'].startswith('Narration belongs')
    assert result['negative_prompt'] == 'red coat'
    assert len(result['reference_media_ids']) == 1
    assert creative.media_store.path(result['reference_media_ids'][0]).read_bytes() == PNG


def test_foreign_assets_invalid_scene_and_duplicate_ids_cannot_be_saved(client):
    other = creative.store.add_asset('other', PNG, filename='private.png')
    project = create(client, 'scene')
    project['reference_asset_ids'] = [other['id']]
    assert save(client, project).status_code == 400
    project['reference_asset_ids'] = []
    project['stage']['objects'][0]['scale'] = [0, 1, 1]
    assert save(client, project).status_code == 400
    project['stage']['objects'][0]['scale'] = [1, 1, 1]
    project['shots'].append(project['shots'][0])
    assert save(client, project).status_code == 400
    assert client.get('/api/creative/projects/' + project['id']).json()['revision'] == 1


class Generator:
    def __init__(self, video=True):
        self.video = video
        self.seen = None

    async def models(self, kind=''):
        return [{'id': 'fake-model'}]

    async def describe(self, model=''):
        return [Param('image_urls', 'References', 'image', max_images=4, multiple=True),
                *([Param('video_url', 'Source clip', 'video')] if self.video else []),
                Param('duration', 'Duration', 'int')]

    async def generate(self, prompt, **kwargs):
        self.seen = (prompt, kwargs)
        return [GeneratedMedia(data=VIDEO, media_type='video/mp4', seed=12)]


def generator(monkeypatch, impl):
    registry = ProviderRegistry()
    registry.register(ProviderInfo(id='fake', label='Local fake', local=True, modalities={Modality.VIDEO}), {Modality.VIDEO: impl})
    service = MediaService(creative.media_store, registry)
    monkeypatch.setattr(media, 'service', service)
    return service


def test_remix_requires_video_capability_before_a_render_starts(client, monkeypatch):
    impl = Generator(video=False)
    service = generator(monkeypatch, impl)
    project = create(client, 'remix')
    project['source_asset_id'] = upload(client, VIDEO, 'source.mp4')['id']
    project = save(client, project).json()
    path = f"/api/creative/projects/{project['id']}/shots/{project['shots'][0]['id']}/run"
    response = client.post(path, json={'base_revision': project['revision'], 'kind': 'video'})
    assert response.status_code == 400 and 'video input' in response.json()['detail']
    assert not service.jobs and impl.seen is None
    assert client.post(path, json={'base_revision': 1, 'kind': 'video'}).status_code == 409


def test_remix_render_keeps_source_bytes_and_persists_take_snapshot(client, monkeypatch):
    impl = Generator()
    generator(monkeypatch, impl)
    project = create(client, 'remix')
    project.update(source_asset_id=upload(client, VIDEO, 'motion.mp4')['id'],
                   reference_asset_ids=[upload(client)['id']], remix='motion')
    project = save(client, project).json()
    path = f"/api/creative/projects/{project['id']}/shots/{project['shots'][0]['id']}/run"
    response = client.post(path, json={'base_revision': project['revision'], 'kind': 'video', 'params': {'duration': 5}})
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 3
    while True:
        takes = client.get(f"/api/creative/projects/{project['id']}/takes").json()['takes']
        if takes and takes[0]['result']['state'] in ('done', 'failed'):
            break
        assert time.monotonic() < deadline, takes
        time.sleep(.01)
    assert takes[0]['result']['state'] == 'done', takes
    assert impl.seen[1]['video_url'] == VIDEO
    assert impl.seen[1]['image_urls'] == [PNG]
    assert 'Transfer the motion' in impl.seen[0]
    take = client.get(f"/api/creative/projects/{project['id']}/takes").json()['takes'][0]
    assert take['result']['state'] == 'done' and take['result']['media']
    assert take['snapshot']['source_media_id'] and take['revision'] == 2
    # The provenance survives a daemon restart and later edits to the plan.
    monkeypatch.setattr(media, 'service', None)
    project['shots'][0]['prompt'] = 'A different shot'
    save(client, project)
    persisted = client.get(f"/api/creative/projects/{project['id']}/takes").json()['takes'][0]
    assert persisted['result']['state'] == 'done' and persisted['snapshot'] == take['snapshot']
    assert client.delete('/api/creative/projects/' + project['id']).status_code == 200
    assert creative.media_store.get(take['result']['media'][0]['id'])


def test_media_schema_distinguishes_arrays_video_audio_and_nullable_inputs():
    params = params_from_openapi({'components': {'schemas': {'Input': {'properties': {
        'video_url': {'anyOf': [{'type': 'string', 'format': 'uri'}, {'type': 'null'}]},
        'audio_url': {'type': 'string', 'format': 'uri'},
        'image_urls': {'type': 'array', 'items': {'type': 'string', 'format': 'uri'}, 'maxItems': 4},
    }}}}})
    by_name = {p.name: p for p in params}
    assert by_name['video_url'].kind == 'video' and by_name['audio_url'].kind == 'audio'
    assert by_name['image_urls'].multiple and by_name['image_urls'].max_images == 4


@pytest.mark.parametrize('provider', [FalProvider, ReplicateProvider])
async def test_aggregators_encode_each_reference_and_source_mime(provider):
    impl = provider()
    async def fields(_):
        return {'image_urls', 'video_url', 'prompt'}
    impl._field_names = fields
    result = await impl._input('Transform', 'example', {'image_urls': [PNG], 'video_url': VIDEO})
    assert result['image_urls'][0].startswith('data:image/png;base64,')
    assert result['video_url'].startswith('data:video/mp4;base64,')


async def test_agent_can_discover_create_and_update_real_plans(client):
    tool = StudioProjectTool()
    ctx = SimpleNamespace()
    assert tool.assess({'action': 'list'}, ctx).risk == Risk.READ
    assert tool.assess({'action': 'update', 'id': 'x'}, ctx).risk == Risk.WRITE
    result = await tool.run({'action': 'create', 'name': 'A product', 'template': 'marketing'}, ctx)
    project = json.loads(result.content)
    doc = document(project)
    doc['shots'][0]['prompt'] = 'Close-up of a ceramic cup beside a sunlit window.'
    result = await tool.run({'action': 'update', 'id': project['id'], 'base_revision': 1, 'document': doc}, ctx)
    assert json.loads(result.content)['revision'] == 2
    assert client.get('/api/creative/projects/' + project['id']).json()['shots'][0]['prompt'].startswith('Close-up')


def test_imported_plan_gets_new_identity_and_validates_references(client):
    original = create(client, 'marketing')
    response = client.post('/api/creative/projects/import', json={'document': document(original)})
    assert response.status_code == 201 and response.json()['id'] != original['id']
    invalid = document(original)
    invalid['source_asset_id'] = 'missing'
    assert client.post('/api/creative/projects/import', json={'document': invalid}).status_code == 400


def test_source_upload_checks_media_type_and_video_signature(client, monkeypatch):
    # Mount the real upload route with its own media service.
    generator(monkeypatch, Generator())
    client.app.include_router(media.router)
    uploaded = client.post('/api/media/upload', content=VIDEO, headers={'Content-Type': 'video/mp4'})
    assert uploaded.status_code == 200 and uploaded.json()['kind'] == 'video'
    assert client.post('/api/media/upload', content=PNG, headers={'Content-Type': 'video/mp4'}).status_code == 400
    service = media.service
    with pytest.raises(ValueError, match='stored image reference'):
        service._load_images([Param('image', 'Image', 'image')], {'image': uploaded.json()['id']})


def test_changed_identity_reference_requires_reprepare(client, monkeypatch):
    from omote.creative.projects import bind_references
    project = create(client)
    reference = upload(client)['id']
    project['reference_asset_ids'] = [reference]
    project = save(client, project).json()
    prepared = client.post(f"/api/creative/projects/{project['id']}/shots/{project['shots'][0]['id']}/prepare").json()
    with pytest.raises(ValueError, match='references changed'):
        bind_references(prepared, [Param('image', 'Image', 'image')], {'image': 'stale-reference'})


async def test_agent_render_is_network_gated_and_records_a_take(client, monkeypatch):
    import asyncio

    impl = Generator()
    service = generator(monkeypatch, impl)
    project = create(client)
    tool = StudioProjectTool()
    args = {'action': 'render', 'id': project['id'], 'shot_id': project['shots'][0]['id'],
            'base_revision': project['revision'], 'kind': 'video', 'provider': 'fake', 'model': 'fake-model'}
    assert tool.assess(args, SimpleNamespace()).risk == Risk.NETWORK
    assert tool.assess({'action': 'render', 'id': 'x', 'shot_id': 'y'}, SimpleNamespace()).invalid
    result = json.loads((await tool.run(args, SimpleNamespace())).content)
    await service.jobs[result['id']].task
    await asyncio.sleep(0)  # Completion callback persists the take.
    takes = json.loads((await tool.run({'action': 'takes', 'id': project['id']}, SimpleNamespace())).content)['takes']
    assert takes[0]['result']['state'] == 'done'
    assert takes[0]['snapshot']['title'] == project['shots'][0]['title']
