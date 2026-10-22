"""Separate reference fields and audio uploads through the real Studio UI."""
import io
import wave

from omote.creative.projects import Project
from omote.media.params import Param
from tests.test_creative_library import PNG
from tests.test_studio_projects_browser import open_projects
from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace

expect = playwright.expect


def test_project_keeps_two_character_inputs_separate_and_accepts_speech(workspace, tmp_path):
    page, url = workspace
    params = [Param('image', 'First character', 'image', group='prompt'),
              Param('image_2', 'Second character', 'image', group='prompt'),
              Param('video', 'Source clip', 'video', group='prompt'),
              Param('audio', 'Speech track', 'audio', group='prompt')]
    page.route('**/api/media/describe?**', lambda route: route.fulfill(json={
        'provider': 'local-test', 'local': True, 'model': 'two-characters',
        'providers': [{'id': 'local-test', 'label': 'Local ComfyUI', 'local': True}],
        'models': [{'id': 'two-characters'}], 'params': [p.to_json() for p in params],
    }))
    assets = []
    for filename in ('first.png', 'second.png'):
        response = page.request.post(url + '/api/creative/assets/upload?filename=' + filename,
                                     data=PNG, headers={'Content-Type': 'image/png'})
        assert response.status == 201
        assets.append(response.json()['id'])
    source = page.request.post(url + '/api/creative/assets/upload?filename=source.mp4',
                               data=b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom',
                               headers={'Content-Type': 'video/mp4'})
    assert source.status == 201
    project = page.request.post(url + '/api/creative/projects', data={
        'template': 'remix', 'name': 'Two dancers', 'brief': 'Two distinct dancers.'}).json()
    document = {k: project[k] for k in Project.model_fields}
    document['reference_asset_ids'] = assets
    document['source_asset_id'] = source.json()['id']
    assert page.request.put(url + '/api/creative/projects/' + project['id'], data={
        'document': document, 'base_revision': project['revision']}).ok
    open_projects(page)
    page.locator('.project-recent').filter(has_text='Two dancers').click()
    page.locator('.project-shot').first.get_by_role('button', name='Prepare video', exact=True).click()
    first = page.locator('.param-image').filter(has=page.get_by_text('First character', exact=True))
    second = page.locator('.param-image').filter(has=page.get_by_text('Second character', exact=True))
    for row in (first, second):
        expect(row.locator('.thumb img')).to_have_count(1)
        expect(row.locator('input[type=file]')).to_be_disabled()
        expect(row.locator('.hint')).to_have_text('From your project')
    assert first.locator('.thumb img').get_attribute('src') != second.locator('.thumb img').get_attribute('src')
    expect(page.locator('#studio-make')).to_be_enabled()
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setparams((1, 2, 24000, 2400, 'NONE', 'not compressed'))
        wav.writeframes(b'\0\0' * 2400)
    speech = page.locator('.param-image').filter(has=page.get_by_text('Speech track', exact=True))
    speech.locator('input[type=file]').set_input_files({'name': 'speech.wav', 'mimeType': 'audio/wav', 'buffer': audio.getvalue()})
    expect(speech.locator('audio')).to_have_count(1)
    calls = []

    def capture(route):
        calls.append(route.request.post_data_json)
        route.fulfill(status=400, json={'detail': 'Test captured; no generation was started.'})

    page.route('**/api/creative/projects/*/shots/*/run', capture)
    page.locator('#studio-make').click()
    expect(page.locator('#studio-status')).to_contain_text('Test captured')
    sent = calls[0]['params']
    assert sent['image'] != sent['image_2']
    assert ',' not in sent['image'] and ',' not in sent['image_2']
    assert sent['audio'] and sent['audio'] not in (sent['image'], sent['image_2'])
    assert sent['video'] and len({sent[name] for name in ('image', 'image_2', 'audio', 'video')}) == 4
    page.screenshot(path=str(tmp_path / 'studio-media-desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('#studio-generate').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
    page.screenshot(path=str(tmp_path / 'studio-media-mobile.png'), full_page=True)
