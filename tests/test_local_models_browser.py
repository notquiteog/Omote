"""Simple desktop/mobile controls; transfers and processes are tested separately."""

import copy
import json

from omote.local_models.catalog import CATALOG, runtime_artifact
from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace

expect = playwright.expect


def library_data():
    def row(spec):
        return {**spec.model_dump(), 'state': 'available', 'downloaded': 0, 'error': '',
                'preflight': {'ok': True, 'errors': [], 'warnings': [], 'estimated_vram_bytes': spec.size + 1024**3,
                              'estimated_ram_bytes': 2 * 1024**3}}

    return {'models': [row(spec) for spec in CATALOG], 'runtime_download': row(runtime_artifact()),
            'runtime_installed': False, 'runtime': {'state': 'stopped', 'model_id': None, 'device': '',
                                                   'error': '', 'log_tail': '', 'provider_id': None},
            'directory': '/example/managed-models'}


def open_library(page):
    if page.viewport_size['width'] < 761:
        page.locator('#sidebar-toggle').click()
    page.locator('#open-system').click()
    page.locator('#open-local-models').click()
    expect(page.locator('#local-models-dialog')).to_be_visible()


def test_download_cancel_resume_start_stop_and_remove(workspace):
    page, _ = workspace
    data = library_data()
    calls = []

    def route(request):
        path = request.request.url.split('/api/local-models', 1)[1].split('?')[0]
        method = request.request.method
        calls.append((method, path))
        if method == 'GET':
            request.fulfill(json=data)
            return
        ident = path.split('/')[1]
        if path.endswith('/download'):
            row = next(row for row in [*data['models'], data['runtime_download']] if row['id'] == ident)
            if row['kind'] == 'runtime' or row['state'] == 'cancelled':
                row.update(state='ready', downloaded=row['size'])
                if row['kind'] == 'runtime':
                    data['runtime_installed'] = True
            else:
                row.update(state='downloading', downloaded=128 * 1024)
        elif path.endswith('/cancel'):
            data['models'][0]['state'] = 'cancelled'
        elif path.endswith('/start'):
            data['runtime'].update(state='running', model_id=ident, device='Vulkan0', provider_id='managed-local')
        elif path == '/runtime/stop':
            data['runtime'].update(state='stopped', model_id=None, provider_id=None)
        elif method == 'DELETE':
            data['models'][0].update(state='available', downloaded=0)
        request.fulfill(json={'ok': True})

    page.route('**/api/local-models**', route)
    open_library(page)
    page.get_by_role('button', name='Install runtime', exact=True).click()
    model = page.locator(f'.lm-card[data-id="{CATALOG[0].id}"]')
    model.get_by_role('button', name='Download', exact=True).click()
    expect(model.get_by_role('progressbar')).to_be_visible()
    model.get_by_role('button', name='Cancel download').click()
    expect(model.get_by_role('button', name='Resume', exact=True)).to_be_enabled()
    model.get_by_role('button', name='Resume', exact=True).click()
    expect(model.get_by_role('button', name='Start', exact=True)).to_be_enabled()
    model.get_by_role('button', name='Start', exact=True).click()
    expect(page.locator('.lm-runtime-state')).to_contain_text('Running on Vulkan0')
    expect(model.get_by_role('button', name='Remove', exact=True)).to_be_disabled()
    expect(model.get_by_role('button', name='Use in chat', exact=True)).to_be_enabled()
    model.get_by_role('button', name='Stop', exact=True).click()
    expect(model.get_by_role('button', name='Start', exact=True)).to_be_enabled()
    page.once('dialog', lambda dialog: dialog.accept())
    model.get_by_role('button', name='Remove', exact=True).click()
    expect(model.get_by_role('button', name='Download', exact=True)).to_be_enabled()
    assert ('POST', f'/{CATALOG[0].id}/cancel') in calls
    assert ('POST', '/runtime/stop') in calls


def test_compatibility_block_is_visible_and_cpu_is_an_explicit_choice(workspace):
    page, _ = workspace
    data = library_data()

    def route(request):
        current = copy.deepcopy(data)
        if 'mode=cpu' not in request.request.url:
            current['models'][0]['preflight'].update(ok=False, errors=['Not enough free VRAM. Choose CPU or free GPU memory.'])
        request.fulfill(json=current)

    page.route('**/api/local-models**', route)
    open_library(page)
    model = page.locator(f'.lm-card[data-id="{CATALOG[0].id}"]')
    expect(model).to_contain_text('Not enough free VRAM')
    expect(model.get_by_role('button', name='Download', exact=True)).to_be_disabled()
    page.locator('#local-models-device').select_option('cpu')
    expect(model.get_by_role('button', name='Download', exact=True)).to_be_enabled()
    expect(model).to_contain_text('RAM')


def test_mobile_custom_model_preserves_draft_and_closed_panel_stops_polling(workspace):
    page, _ = workspace
    page.set_viewport_size({'width': 390, 'height': 844})
    data = library_data()
    calls = []

    def route(request):
        calls.append(request.request.method)
        if request.request.method == 'POST':
            body = json.loads(request.request.post_data)
            row = {**copy.deepcopy(data['models'][0]), **body, 'id': 'custom-fixture'}
            data['models'].append(row)
            request.fulfill(json=row)
        else:
            request.fulfill(json=data)

    page.route('**/api/local-models**', route)
    open_library(page)
    expect(page.locator('#local-models-runtime .lm-card')).to_be_visible()
    assert page.locator('#local-models-dialog').evaluate('node => node.scrollWidth <= node.clientWidth')
    page.get_by_text('Add another GGUF model', exact=True).click()
    form = page.locator('#local-models-custom')
    form.locator('[name=label]').fill('My <model>')
    form.locator('[name=url]').fill('https://example.com/model.gguf')
    page.wait_for_timeout(2200)
    expect(form.locator('[name=label]')).to_have_value('My <model>')
    form.locator('[name=size]').fill('1024')
    form.locator('[name=sha256]').fill('a' * 64)
    form.get_by_role('button', name='Add to library').click()
    expect(page.locator('.lm-card[data-id="custom-fixture"]')).to_contain_text('My <model>')
    assert page.locator('.lm-card model').count() == 0
    page.locator('#local-models-close').click()
    before = len(calls)
    page.wait_for_timeout(2200)
    assert len(calls) == before
