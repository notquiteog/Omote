"""Scheduled work through the actual workspace and a local fake model."""
import pytest

from tests import test_onboarding_browser, test_workspace_browser
from tests.test_onboarding_browser import use_fake
from tests.test_openrouter import KEY

workspace = test_workspace_browser.workspace
chat_provider = test_onboarding_browser.chat_provider
playwright = pytest.importorskip('playwright.sync_api')
expect = playwright.expect


def new_task(page, name='朝の確認'):
    page.locator('#open-schedules').click()
    page.locator('#schedule-new').click()
    page.locator('#schedule-name').fill(name)
    page.locator('#schedule-prompt').fill('Review my project and report the results.')
    page.locator('#schedule-cadence').select_option('manual')
    page.locator('#schedule-save').click()
    expect(page.locator('#schedule-status')).to_contain_text('Task saved')


def test_create_edit_pause_persist_delete(workspace):
    page, url = workspace
    new_task(page)
    card = page.locator('.schedule-card')
    expect(card).to_contain_text('朝の確認')
    card.get_by_role('button', name='Edit', exact=True).click()
    page.locator('#schedule-cadence').select_option('weekly')
    page.locator('#schedule-timezone').fill('Asia/Tokyo')
    page.get_by_text('Model and tools', exact=True).click()
    page.locator('#schedule-tools').fill('read_file, glob, read_file')
    for checkbox in page.locator('#schedule-days input').all():
        checkbox.uncheck()
    page.locator('#schedule-save').click()
    expect(page.locator('#schedule-status')).to_contain_text('Choose at least one weekday')
    page.locator('#schedule-days input[value="0"]').check()
    page.locator('#schedule-save').click()
    expect(card).to_contain_text('Mon · 09:00 · Asia/Tokyo')
    card.get_by_role('button', name='Pause', exact=True).click()
    expect(card.locator('.pill')).to_have_text('paused')
    page.reload()
    page.locator('#open-schedules').click()
    expect(card.locator('.pill')).to_have_text('paused')
    saved = page.request.get(url + '/api/schedules').json()['schedules'][0]['spec']
    assert saved['timezone'] == 'Asia/Tokyo'
    assert saved['tools'] == ['read_file', 'glob']
    page.on('dialog', lambda dialog: dialog.accept())
    card.get_by_role('button', name='Delete', exact=True).click()
    expect(page.locator('#schedule-list')).to_contain_text('No scheduled tasks yet')


def test_run_result_refreshes_and_reopens_saved_provider_chat(workspace, chat_provider):
    page, url = workspace
    use_fake(page, chat_provider)
    page.locator('#setup-connect').click()
    page.locator('#welcome-key').fill(KEY)
    page.locator('#welcome-submit').click()
    page.locator('#welcome-start').click()
    new_task(page, 'Project briefing')
    page.locator('.schedule-card').get_by_role('button', name='Run now', exact=True).click()
    expect(page.locator('#schedule-runs')).to_contain_text('completed', timeout=15000)
    page.locator('#schedule-runs summary').click()
    expect(page.locator('.schedule-result')).to_contain_text('Ready to help with your first task.')
    page.screenshot(path='/tmp/omote-scheduled-results-desktop.png')
    data = page.request.get(url + '/api/schedules').json()
    assert data['runs'][0]['status'] == 'completed'
    assert len(chat_provider.bodies('POST', '/api/v1/chat/completions')) == 1
    page.locator('#schedule-runs').get_by_role('button', name='Open chat').click()
    expect(page.locator('#schedules-dialog')).to_be_hidden()
    expect(page.locator('#transcript')).to_contain_text('Ready to help with your first task.')


@pytest.mark.parametrize('width,height', [(390, 844), (320, 568), (844, 390)])
def test_mobile_form_fits_and_escape_restores_navigation(workspace, width, height):
    page, _ = workspace
    page.set_viewport_size({'width': width, 'height': height})
    if width <= 760:
        page.locator('#sidebar-toggle').click()
    page.locator('#open-schedules').click()
    page.locator('#schedule-new').click()
    page.locator('#schedule-name').fill('旅行の準備')
    page.locator('#schedule-prompt').fill('Compare hotel options for Tokyo.')
    box = page.locator('#schedules-dialog').bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= width
    assert box['height'] <= height
    assert page.locator('#schedules-dialog').evaluate('(el) => el.scrollWidth <= el.clientWidth')
    page.screenshot(path=f'/tmp/omote-scheduled-form-{width}.png')
    page.keyboard.press('Escape')
    expect(page.locator('#schedules-dialog')).to_be_hidden()
    assert not page.locator('main').evaluate('(el) => el.inert')


def test_comfy_version_comparison_includes_seed_and_escapes_prompt(workspace):
    page, url = workspace
    base = url + '/api/media/comfyui/workflows'
    prompt = {'1': {'class_type': 'Sampler', 'inputs': {'seed': 12, 'text': 'old'}}}
    job = page.request.post(base, data={'name': 'Character portrait', 'prompt': prompt}).json()
    prompt['1']['inputs'] = {'seed': 42, 'text': '<img src=x onerror=alert(1)>'}
    assert page.request.post(base + '/' + job['id'] + '/versions', data={'name': 'Character portrait', 'prompt': prompt, 'base_revision': 1}).ok
    page.locator('#start-studio').click()
    page.locator('#studio-graph').click()
    page.locator('#comfy-library-toggle').click()
    page.locator('.wb-saved').click()
    page.get_by_text('Compare saved versions', exact=True).click()
    page.locator('#wb-compare').click()
    expect(page.locator('#wb-comparison')).to_contain_text('v1 → v2: 2 changes')
    change = page.locator('.wb-change').filter(has_text='/prompt/1/inputs/seed')
    change.locator('summary').click()
    expect(change.locator('pre').first).to_have_text('12')
    expect(change.locator('pre').last).to_have_text('42')
    assert page.locator('#wb-comparison img').count() == 0
    page.screenshot(path='/tmp/omote-workflow-compare.png')
