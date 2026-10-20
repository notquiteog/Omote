"""No terminal, no credentials, and no real installation triggered by UI tests."""
from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace

expect = playwright.expect


def state():
    return {target: {'state': 'missing', 'busy': False, 'waiting': False, 'note': '', 'log': '', 'error': ''}
            for target in ('comfyui', 'playwright')}


def test_studio_setup_cancel_retry_ready_and_closing_stops_polling(workspace):
    page, _ = workspace
    data = state()
    calls = []
    def route(route):
        path = route.request.url.split('/api/setup', 1)[1]
        calls.append((route.request.method, path))
        if path.endswith('/start'):
            data['comfyui'].update(state='installing', busy=True, note='Installing the generation engine')
        elif path.endswith('/cancel'):
            data['comfyui'].update(state='cancelled', busy=False, note='Setup paused')
        route.fulfill(json=data if route.request.method == 'GET' else data['comfyui'])
    page.route('**/api/setup**', route)
    page.locator('#start-studio').click()
    page.locator('#studio-setup').click()
    expect(page.locator('#ts-title')).to_have_text('Local studio')
    page.locator('#ts-start').click()
    expect(page.locator('#ts-status')).to_contain_text('Installing the generation engine')
    expect(page.locator('#ts-progress')).to_be_visible()
    page.locator('#ts-cancel').click()
    expect(page.locator('#ts-start')).to_have_text('Retry setup')
    page.locator('#ts-start').click()
    expect(page.locator('#ts-progress')).to_be_visible()
    page.locator('#ts-close').click()
    count = len(calls)
    page.wait_for_timeout(1800)
    assert len(calls) == count
    data['comfyui'].update(state='ready', busy=False, note='Local studio is ready · GPU')
    page.locator('#studio-setup').click()
    expect(page.locator('#ts-open')).to_have_text('Open studio')
    expect(page.locator('#ts-status')).to_contain_text('ready')
    assert [p for method, p in calls if method == 'POST'] == [
        '/comfyui/start', '/comfyui/cancel', '/comfyui/start']


def test_browser_setup_mobile_plain_language_and_no_overflow(workspace):
    page, _ = workspace
    page.set_viewport_size({'width': 390, 'height': 844})
    data = state()
    page.route('**/api/setup**', lambda route: route.fulfill(json=data))
    page.locator('#sidebar-toggle').click()
    page.locator('#open-settings').click()
    page.locator('#tab-browser').click()
    page.locator('#browser-setup').click()
    expect(page.locator('#ts-title')).to_have_text('Browser automation')
    expect(page.locator('#ts-start')).to_have_text('Set up browser')
    expect(page.locator('#ts-options')).not_to_be_visible()
    assert page.locator('#tool-setup-dialog').evaluate('e => e.scrollWidth <= e.clientWidth')
    assert 'pip install' not in page.locator('#tool-setup-dialog').inner_text()


def test_ready_browser_can_install_another_engine_from_advanced(workspace):
    page, _ = workspace
    data = state()
    data['playwright'].update(state='ready', engine='chromium', note='Chromium is ready')
    choices = []
    def route(route):
        if route.request.method == 'POST':
            choices.append(route.request.post_data_json['engine'])
            data['playwright'].update(state='installing', busy=True, note='Downloading Firefox')
        route.fulfill(json=data)
    page.route('**/api/setup**', route)
    page.locator('#open-settings').click()
    page.locator('#tab-browser').click()
    page.locator('#browser-setup').click()
    expect(page.locator('#ts-open')).to_have_text('Done')
    page.locator('#tool-setup-dialog summary').click()
    page.locator('#ts-engine').select_option('firefox')
    expect(page.locator('#ts-start')).to_be_visible()
    page.locator('#ts-start').click()
    expect(page.locator('#ts-status')).to_contain_text('Downloading Firefox')
    assert choices == ['firefox']
