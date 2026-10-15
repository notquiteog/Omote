"""Studio library controls, compatibility feedback, drafts and mobile layout."""

import json

from omote.local_models.comfy import LIGHTNING, MODEL
from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace

expect = playwright.expect


def library():
    return {'assets': [{**spec.model_dump(), 'state': 'available', 'downloaded': 0, 'error': '',
                        'preflight': {'ok': True, 'errors': [], 'warnings': []}} for spec in (MODEL, LIGHTNING)],
            'directory': '/managed/models', 'paths_config': '/managed/extra_model_paths.yaml',
            'starter': {'id': 'sd-turbo-recipe', 'label': 'SD Turbo recipe'}}


def open_library(page):
    if page.viewport_size['width'] < 761:
        page.locator('#sidebar-toggle').click()
    page.locator('[data-mode="studio"]').click()
    expect(page.locator('#studio-assets')).to_be_visible()
    assert page.locator('#pane-studio').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
    page.locator('#studio-assets').click()
    expect(page.locator('#comfy-assets-dialog')).to_be_visible()


def connections(page):
    page.route('**/api/media/comfyui/editors', lambda route: route.fulfill(json={'editors': [
        {'id': 'local-comfy', 'label': 'Local ComfyUI', 'available': True, 'url': 'http://127.0.0.1:8188', 'reason': ''}]}))


def test_comfy_model_and_workflow_cancel_resume_and_remove(workspace):
    page, _ = workspace
    data = library()
    calls = []
    connections(page)

    def route(request):
        path = request.request.url.split('/api/media/comfyui/assets', 1)[1].split('?')[0]
        method = request.request.method
        calls.append((method, path))
        if method == 'GET':
            request.fulfill(json=data)
            return
        ident = path.split('/')[1]
        row = next(row for row in data['assets'] if row['id'] == ident)
        if path.endswith('/download'):
            if row['state'] == 'cancelled':
                row.update(state='ready', downloaded=row['size'])
            else:
                row.update(state='downloading', downloaded=65536)
        elif path.endswith('/cancel'):
            row['state'] = 'cancelled'
        elif method == 'DELETE':
            row.update(state='available', downloaded=0)
        request.fulfill(json={'ok': True})

    page.route('**/api/media/comfyui/assets**', route)
    open_library(page)
    for spec in (MODEL, LIGHTNING):
        card = page.locator(f'#ca-assets [data-id="{spec.id}"]')
        card.get_by_role('button', name='Download', exact=True).click()
        expect(card.get_by_role('progressbar')).to_be_visible()
        card.get_by_role('button', name='Cancel download').click()
        expect(page.locator('#ca-status')).to_contain_text('Progress kept')
        card.get_by_role('button', name='Resume download').click()
        expect(card).to_contain_text('Downloaded & verified')
        page.once('dialog', lambda dialog: dialog.accept())
        card.get_by_role('button', name='Remove', exact=True).click()
        expect(card.get_by_role('button', name='Download', exact=True)).to_be_enabled()
        assert ('POST', f'/{spec.id}/cancel') in calls


def test_live_compatibility_failure_keeps_recipe_out_of_studio(workspace):
    page, _ = workspace
    data = library()
    connections(page)
    calls = []

    def route(request):
        calls.append(request.request.method)
        if request.request.method == 'POST':
            request.fulfill(status=409, json={'detail': 'Missing KSampler node or sd_turbo.safetensors. Add the managed model paths.'})
        else:
            request.fulfill(json=data)

    page.route('**/api/media/comfyui/assets**', route)
    open_library(page)
    page.get_by_role('button', name='Check & use in Studio').click()
    expect(page.locator('#ca-status')).to_contain_text('Missing KSampler')
    expect(page.locator('#comfy-assets-dialog')).to_be_visible()
    page.locator('#ca-close').click()
    page.wait_for_timeout(2200)
    before = len(calls)
    page.wait_for_timeout(2200)
    assert len(calls) == before


def test_mobile_custom_workflow_draft_survives_polling_and_type_switch(workspace):
    page, _ = workspace
    page.set_viewport_size({'width': 390, 'height': 844})
    data = library()
    connections(page)
    submitted = []

    def route(request):
        if request.request.method == 'POST':
            body = json.loads(request.request.post_data)
            submitted.append(body)
            data['assets'].append({**data['assets'][1], **body, 'id': 'custom-test'})
            request.fulfill(json={'ok': True})
        else:
            request.fulfill(json=data)

    page.route('**/api/media/comfyui/assets**', route)
    open_library(page)
    assert page.locator('#comfy-assets-dialog').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
    page.get_by_text('Add a model or workflow URL', exact=True).click()
    form = page.locator('#ca-custom')
    form.locator('[name=kind]').select_option('workflow')
    form.locator('[name=media_kind]').select_option('video')
    form.locator('[name=label]').fill('My <workflow>')
    form.locator('[name=url]').fill('https://example.com/workflow.json')
    page.wait_for_timeout(2300)
    expect(form.locator('[name=label]')).to_have_value('My <workflow>')
    expect(form.locator('[name=filename]')).to_be_hidden()
    form.locator('[name=size]').fill('6644')
    form.locator('[name=sha256]').fill('a' * 64)
    form.get_by_role('button', name='Add to library').click()
    expect(page.locator('#ca-assets [data-id="custom-test"]')).to_contain_text('My <workflow>')
    assert page.locator('#ca-assets workflow').count() == 0
    assert submitted[0]['media_kind'] == 'video'
    assert 'filename' not in submitted[0]
