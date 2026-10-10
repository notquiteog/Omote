"""Character application across real schema names and concurrent library edits."""

from urllib.parse import parse_qs, urlparse

import pytest

from omote.media.params import Param
from tests.test_creative_browser import PNG, library
from tests.test_creative_browser import creative_workspace as creative_workspace

playwright = pytest.importorskip('playwright.sync_api')


def describe_info(name, *, maximum=1, model='single'):
    return {
        'provider': 'image-test', 'local': True, 'model': model,
        'providers': [{'id': 'image-test', 'label': 'Test generator', 'local': True}],
        'models': [{'id': model}],
        'params': [Param('prompt', 'Prompt', 'text', group='prompt').to_json(),
                   Param('mask', 'Mask', 'image', group='prompt').to_json(),
                   Param('last_frame', 'Last frame', 'image', group='prompt').to_json(),
                   *([Param(name, 'Character reference', 'image', group='prompt', max_images=maximum).to_json()] if name else [])],
    }


def reference(page, url, name='ref.png'):
    response = page.request.post(url + '/api/media/upload', data=PNG, headers={'Content-Type': 'image/png'})
    assert response.ok, response.text()
    return response.json()['id']


def recipe(refs):
    return {'character': {'id': 'character', 'name': 'Hana'}, 'prompt': 'Red scarf and silver pin',
            'negative_prompt': 'different clothes', 'reference_media_ids': refs}


def apply(page, value):
    page.evaluate("async value => { const studio = await import('/static/studio.js'); await studio.applyCharacter(value); }", value)


def capture_jobs(page):
    requests = []

    def handler(route):
        if route.request.method == 'POST':
            requests.append(route.request.post_data_json)
            route.fulfill(json={'id': 'preview-job', 'state': 'queued'})
        else:
            route.fulfill(json={'jobs': []})
    page.route('**/api/media/jobs', handler)
    return requests


@pytest.mark.parametrize('field', ['input_reference', 'first_frame_image', 'input_image', 'image_prompt', 'first_frame'])
def test_character_maps_to_actual_single_image_schema_not_mask_or_last_frame(creative_workspace, field):
    page, url, _ = creative_workspace
    page.route('**/api/media/describe?*', lambda route: route.fulfill(json=describe_info(field)))
    refs = [reference(page, url), reference(page, url)]
    page.locator('[data-mode="studio"]').click()
    apply(page, recipe(refs))
    playwright.expect(page.locator('#studio-character')).to_contain_text('1 selected for Character reference')
    requests = capture_jobs(page)
    page.locator('#studio-make').click()
    page.wait_for_timeout(100)
    assert requests[-1]['params'] == {field: refs[0]}
    assert page.locator('.param-image').last.locator('input[type=file]').get_attribute('multiple') is None


def test_multi_image_cap_and_manual_changes_survive_schema_refresh(creative_workspace):
    page, url, _ = creative_workspace
    def describe(route):
        model = parse_qs(urlparse(route.request.url).query).get('model', ['multi'])[0]
        route.fulfill(json=describe_info('image', maximum=1 if model == 'single' else 3, model=model))
    page.route('**/api/media/describe?*', describe)
    refs = [reference(page, url) for _ in range(4)]
    page.locator('[data-mode="studio"]').click()
    apply(page, recipe(refs))
    images = page.locator('.param-image').last
    playwright.expect(images.locator('.thumb')).to_have_count(3)
    # Remove first reference manually. A model refresh must not put it back.
    images.locator('.thumb .x').first.click()
    page.locator('#studio-model').fill('refreshed')
    page.locator('#studio-model').dispatch_event('change')
    playwright.expect(page.locator('#studio-character')).to_contain_text('2 selected')
    requests = capture_jobs(page)
    page.locator('#studio-make').click()
    page.wait_for_timeout(100)
    assert requests[-1]['params']['image'] == ','.join(refs[1:3])
    # The same parameter name on a single-image provider cannot receive a list.
    page.locator('#studio-model').fill('single')
    page.locator('#studio-model').dispatch_event('change')
    playwright.expect(page.locator('#studio-character')).to_contain_text('1 selected')
    page.locator('#studio-make').click()
    page.wait_for_timeout(100)
    assert requests[-1]['params']['image'] == refs[1]


def test_reference_reapplies_on_different_parameter_name_and_notice_tracks_no_input(creative_workspace):
    page, url, _ = creative_workspace
    def describe(route):
        model = parse_qs(urlparse(route.request.url).query).get('model', ['first'])[0]
        field = None if model == 'none' else 'input_reference' if model == 'second' else 'image'
        route.fulfill(json=describe_info(field, model=model))
    page.route('**/api/media/describe?*', describe)
    refs = [reference(page, url)]
    page.locator('[data-mode="studio"]').click()
    apply(page, recipe(refs))
    page.locator('#studio-model').fill('second')
    page.locator('#studio-model').dispatch_event('change')
    playwright.expect(page.locator('.param-image').last.locator('img')).to_have_attribute('src', f'/api/media/{refs[0]}/file')
    requests = capture_jobs(page)
    page.locator('#studio-make').click()
    page.wait_for_timeout(100)
    assert requests[-1]['params'] == {'input_reference': refs[0]}
    page.locator('#studio-model').fill('none')
    page.locator('#studio-model').dispatch_event('change')
    playwright.expect(page.locator('#studio-character')).to_contain_text('no character image input')


def test_editing_character_refetches_worker_added_references_before_saving(creative_workspace):
    page, url, _ = creative_workspace
    created = page.request.post(url + '/api/creative/characters', data={'name': 'Hana'}).json()
    library(page)
    playwright.expect(page.locator('.character-card')).to_have_count(1)
    # A worker changes the same character after the list loaded.
    response = page.request.post(url + '/api/creative/assets/upload', params={
        'filename': 'new-reference.png', 'character_id': created['id'],
    }, data=PNG, headers={'Content-Type': 'image/png'})
    assert response.ok, response.text()
    asset = response.json()
    response = page.request.put(url + f'/api/creative/characters/{created["id"]}',
                                data={'name': 'Hana', 'reference_asset_ids': [asset['id']]})
    assert response.ok, response.text()
    page.locator('.character-card').get_by_role('button', name='Edit', exact=True).click()
    playwright.expect(page.locator(f'#character-references input[value="{asset["id"]}"]')).to_be_checked()
    page.locator('#character-form [name=name]').fill('Hana updated')
    page.get_by_role('button', name='Save character', exact=True).click()
    playwright.expect(page.locator('#character-dialog')).not_to_be_visible()
    saved = page.request.get(url + f'/api/creative/characters/{created["id"]}').json()
    assert saved['reference_asset_ids'] == [asset['id']]


def test_reference_sheet_switches_music_to_image(creative_workspace):
    page, url, _ = creative_workspace
    page.route('**/api/media/describe?*', lambda route: route.fulfill(json=describe_info('image')))
    page.locator('[data-mode="studio"]').click()
    page.locator('.studio-kind [data-kind=music]').click()
    refs = [reference(page, url)]
    page.evaluate("async value => { const studio = await import('/static/studio.js'); await studio.applyCharacter(value, 'Reference sheet'); }", recipe(refs))
    requests = capture_jobs(page)
    page.locator('#studio-make').click()
    page.wait_for_timeout(100)
    assert requests[-1]['kind'] == 'image'
    assert 'Reference sheet' in requests[-1]['prompt']


def test_schema_multiplicity_is_explicit():
    assert Param('image', 'Starting frame', 'image').to_json()['max_images'] == 1
    assert Param('image', 'Multiple refs', 'image', max_images=None).to_json()['max_images'] is None
    assert Param('image', 'Up to three', 'image', max_images=3).to_json()['max_images'] == 3
    assert 'max_images' not in Param('prompt', 'Prompt').to_json()


async def test_real_adapter_schemas_declare_supported_reference_multiplicity():
    from omote.providers.google_media import GoogleMediaProvider
    from omote.providers.ideogram import IdeogramProvider
    from omote.providers.openai_compat import OpenAICompatProvider
    from omote.providers.openrouter import _image_params
    from omote.providers.runway import RunwayProvider
    from omote.providers.sora import SoraProvider

    for provider, model, field, expected in [
        (OpenAICompatProvider('http://unused.test/v1'), 'gpt-image-1', 'image', None),
        (GoogleMediaProvider(kind='image'), 'gemini-image', 'image', None),
        (GoogleMediaProvider(kind='video'), 'veo', 'image', 1),
        (IdeogramProvider(), '', 'style_reference_images', None),
        (RunwayProvider(), 'gen4_image', 'reference_images', 3),
        (RunwayProvider(), 'gen4_turbo', 'image', 1),
        (SoraProvider(), 'sora-2', 'input_reference', 1),
    ]:
        params = {param.name: param.to_json() for param in await provider.describe(model)}
        assert params[field]['max_images'] == expected
    params = {param.name: param.to_json() for param in _image_params({'input_references': {'max': 7}})}
    assert params['image']['max_images'] == 7
