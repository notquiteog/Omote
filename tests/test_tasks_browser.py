"""The task inbox exposes work without approving it or mixing chat context."""
import pytest

from tests import test_workspace_browser

workspace = test_workspace_browser.workspace
playwright = pytest.importorskip('playwright.sync_api')
expect = playwright.expect


def test_activity_filters_and_studio_navigation(workspace):
    page, url = workspace
    data = page.request.get(url + '/api/schedules').json()
    data['activity'] = [
        dict(id='approval', kind='chat', title='<img src=x> Review a change',
             status='waiting', note='Open this chat to respond.', session_id='approval-chat'),
        dict(id='render', kind='media', title='Rendering a landscape', status='running', progress=.4),
        dict(id='result', kind='media', title='Portrait ready', status='completed'),
    ]
    page.route('**/api/schedules', lambda route: route.fulfill(json=data))
    page.get_by_role('button', name='Tasks', exact=True).click()
    cards = page.locator('#schedule-runs .schedule-run')
    expect(cards).to_have_count(3)
    expect(cards.first).to_contain_text('<img src=x> Review a change')
    assert page.locator('#schedule-runs img').count() == 0
    expect(page.locator('#task-count-attention')).to_have_text('1')
    page.locator('[data-task-filter="attention"]').click()
    expect(cards).to_have_count(1)
    expect(cards.first.get_by_role('button', name='Review request')).to_be_visible()
    page.locator('[data-task-filter="working"]').click()
    expect(cards).to_have_count(1)
    expect(cards.first.locator('progress')).to_have_attribute('value', '0.4')
    page.locator('[data-task-filter="finished"]').click()
    expect(cards).to_have_count(1)
    expect(cards.first).to_contain_text('Portrait ready')
    cards.first.get_by_role('button', name='Open Studio').click()
    expect(page.locator('#schedules-dialog')).to_be_hidden()
    expect(page.locator('#studio-prompt')).to_be_visible()
    assert page.request.get(url + '/api/sessions').json()['sessions'] == []


def test_template_is_editable_draft_preserved_on_close(workspace):
    page, url = workspace
    page.locator('#open-schedules').click()
    page.get_by_role('button', name='Morning briefing').click()
    expect(page.locator('#schedule-name')).to_have_value('Morning briefing')
    expect(page.locator('#schedule-mode')).to_have_value('ask')
    expect(page.locator('#schedule-workspace-options')).not_to_have_attribute('open', '')
    page.locator('#schedule-prompt').fill('Review my project notes. Do not send any messages.')
    page.keyboard.press('Escape')
    page.locator('#open-schedules').click()
    expect(page.locator('#schedule-form')).to_be_visible()
    expect(page.locator('#schedule-prompt')).to_have_value('Review my project notes. Do not send any messages.')
    assert page.request.get(url + '/api/schedules').json()['schedules'] == []
    page.locator('#schedule-cadence').select_option('manual')
    page.locator('#schedule-save').click()
    expect(page.locator('#schedule-status')).to_contain_text('Choose Run now when you’re ready')
    expect(page.locator('.schedule-card .pill')).to_have_text('on demand')
    saved = page.request.get(url + '/api/schedules').json()
    assert saved['schedules'][0]['spec']['mode'] == 'ask'
    assert saved['schedules'][0]['spec']['root'] == saved['workspace']
    assert saved['runs'] == []
    page.get_by_role('button', name='Research update').click()
    page.locator('#schedule-cancel').click()
    expect(page.locator('#task-overview')).to_be_visible()
    page.locator('#schedule-new').click()
    expect(page.locator('#schedule-name')).to_have_value('')


def test_closing_during_initial_load_does_not_leave_polling(workspace):
    page, url = workspace
    data = page.request.get(url + '/api/schedules').json()
    held = []
    page.route('**/api/schedules', lambda route: held.append(route))
    page.locator('#open-schedules').click()
    page.locator('#schedules-close').click()
    assert len(held) == 1
    held[0].fulfill(json=data)
    page.wait_for_timeout(5200)
    assert len(held) == 1
    page.unroute('**/api/schedules')
    page.locator('#open-schedules').click()
    expect(page.locator('#schedule-new')).to_be_enabled()


@pytest.mark.parametrize('width,height', [(1440, 900), (390, 844), (320, 568), (844, 390)])
def test_home_message_first_and_task_overview_fit(workspace, width, height):
    page, _ = workspace
    page.set_viewport_size({'width': width, 'height': height})
    composer = page.locator('#composer').bounding_box()
    suggestions = page.locator('#workspace-suggestions').bounding_box()
    assert composer['y'] + composer['height'] <= suggestions['y'] + 1
    assert composer['y'] >= 0 and composer['y'] + composer['height'] <= height
    assert page.locator('main').evaluate('node => node.scrollWidth <= node.clientWidth')
    for starter in page.locator('#workspace-suggestions button').all():
        starter.scroll_into_view_if_needed()
        # Chromium can round a fraction of the border outside the viewport.
        expect(starter).to_be_in_viewport(ratio=.99)
    page.locator('#sidebar-toggle' if width <= 760 else '#bar').scroll_into_view_if_needed()
    if width <= 760:
        page.locator('#sidebar-toggle').click()
    page.locator('#open-schedules').click()
    expect(page.locator('#schedule-new')).to_be_enabled()
    assert page.locator('#schedules-dialog').evaluate('node => node.scrollWidth <= node.clientWidth')
    box = page.locator('#schedules-dialog').bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= width
    assert box['y'] >= 0 and box['y'] + box['height'] <= height
