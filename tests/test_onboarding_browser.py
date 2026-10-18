"""First-use journeys through the real UI and daemon, with a local fake provider."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests import test_workspace_browser
from tests.test_openrouter import KEY, Fake, _handler

workspace = test_workspace_browser.workspace

playwright = pytest.importorskip('playwright.sync_api')
expect = playwright.expect


@pytest.fixture
def chat_provider():
    state = Fake()
    base = _handler(state)
    class Handler(base):
        def do_POST(self):  # noqa: N802
            if self.path != '/api/v1/chat/completions':
                return super().do_POST()
            self._record()
            if not self._authorised():
                return self._refuse()
            chunks = [
                {'choices': [{'delta': {'role': 'assistant', 'content': 'Ready to help with your first task.'}, 'finish_reason': None}]},
                {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            ]
            payload = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
            self._send(200, payload.encode(), 'text/event-stream')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.base = f'http://127.0.0.1:{server.server_port}/api/v1'
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def fresh(page):
    page.add_init_script("localStorage.removeItem('omote.welcome.v1')")
    page.reload()
    expect(page.locator('#welcome-dialog')).to_be_visible()


def use_fake(page, provider):
    def redirect(route):
        data = route.request.post_data_json
        data['base_url'] = provider.base
        result = route.fetch(post_data=json.dumps(data))
        route.fulfill(response=result)
    page.route('**/api/providers/connections', redirect)


def test_key_only_setup_bad_key_retry_and_first_message(workspace, chat_provider):
    page, url = workspace
    fresh(page)
    use_fake(page, chat_provider)
    expect(page.locator('#welcome-provider')).to_have_value('openrouter')
    page.locator('#welcome-key').fill('wrong-key')
    page.locator('#welcome-submit').click()
    expect(page.locator('#welcome-status')).to_contain_text('refused this API key')
    assert page.request.get(url + '/api/providers/connections').json()['connections'] == []
    page.locator('#welcome-key').fill(KEY)
    page.locator('#welcome-submit').click()
    expect(page.locator('#welcome-ready-panel')).to_be_visible()
    expect(page.locator('#welcome-key')).to_have_value('')
    assert KEY not in page.evaluate('JSON.stringify(localStorage)')
    assert not page.request.get(url + '/api/memory/settings').json()['enabled']
    page.locator('#welcome-start').click()
    expect(page.locator('#input')).to_be_enabled()
    page.locator('#input').fill('Help me plan my first task')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_contain_text('Ready to help with your first task.')
    expect(page.locator('#transcript')).not_to_contain_text('Not connected')
    expect(page.locator('#transcript')).not_to_contain_text('Reconnected')
    sessions = page.request.get(url + '/api/sessions').json()['sessions']
    assert len(sessions) == 1
    assert sessions[0]['model'] == 'deepseek/deepseek-v4-flash'
    assert len(chat_provider.bodies('POST', '/api/v1/chat/completions')) == 1
    expect(page.locator('#mode')).to_have_value('ask')
    page.locator('#nav-rewind').click()
    expect(page.locator('#rewind-dialog')).to_be_visible()
    expect(page.locator('#rewind-list')).to_contain_text('Nothing to undo')


@pytest.mark.parametrize('status,body,expected', [
    (500, 'Internal Server Error', 'Could not start the chat (HTTP 500)'),
    (200, '{}', 'The server did not return a chat'),
])
def test_first_message_server_failure_is_visible_and_preserves_the_draft(workspace, chat_provider, status, body, expected):
    page, url = workspace
    response = page.request.put(url + '/api/providers/connections', data={
        'host_id': 'openrouter', 'api_key': KEY, 'base_url': chat_provider.base, 'configure_chat': True,
    })
    assert response.ok
    page.reload()
    expect(page.locator('#input')).to_be_enabled()

    def fail_create(route):
        if route.request.method == 'POST':
            route.fulfill(status=status, body=body, content_type='text/plain')
        else:
            route.continue_()

    page.route('**/api/sessions', fail_create)
    page.locator('#input').fill('Keep my first message')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript')).to_contain_text(expected)
    expect(page.locator('#input')).to_have_value('Keep my first message')
    expect(page.locator('#input')).to_be_enabled()
    expect(page.locator('#send')).to_be_enabled()


def test_model_dropdown_and_reasoning_slider_apply_to_new_and_existing_chats(workspace, chat_provider):
    page, url = workspace
    assert page.request.put(url + '/api/providers/connections', data={
        'id': 'work-router', 'host_id': 'openrouter', 'api_key': KEY,
        'base_url': chat_provider.base, 'configure_chat': True,
    }).ok
    page.route('**/api/providers/work-router/models?modality=chat', lambda route: route.fulfill(json={
        'models': [{'id': 'vendor/first', 'label': 'First model'}, {'id': 'vendor/second', 'label': 'Second model'}],
    }))
    page.reload()
    expect(page.locator('#chat-model option')).to_have_count(3)
    page.locator('#chat-model').select_option(json.dumps(['work-router', 'vendor/first'], separators=(',', ':')))
    for _ in range(4):
        page.locator('#effort').press('ArrowRight')
    expect(page.locator('#effort-value')).to_have_text('High')
    page.locator('#input').fill('First message with my choices')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_contain_text('Ready to help')
    sessions = page.request.get(url + '/api/sessions').json()['sessions']
    assert len(sessions) == 1
    sid = sessions[0]['id']
    assert sessions[0]['model'] == 'vendor/first' and sessions[0]['effort'] == 'high'
    assert chat_provider.bodies('POST', '/api/v1/chat/completions')[-1]['model'] == 'vendor/first'
    page.locator('#chat-model').select_option(json.dumps(['work-router', 'vendor/second'], separators=(',', ':')))
    expect(page.locator('#transcript')).to_contain_text('Answering with vendor/second')
    for _ in range(2):
        page.locator('#effort').press('ArrowLeft')
    expect(page.locator('#transcript')).to_contain_text('Thinking is now: low')
    page.locator('#input').fill('Second message keeps our conversation')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_have_count(2)
    sessions = page.request.get(url + '/api/sessions').json()['sessions']
    assert len(sessions) == 1 and sessions[0]['id'] == sid
    assert sessions[0]['model'] == 'vendor/second' and sessions[0]['effort'] == 'low'
    sent = chat_provider.bodies('POST', '/api/v1/chat/completions')[-1]
    assert sent['model'] == 'vendor/second'
    assert any('First message with my choices' in str(message) for message in sent['messages'])
    assert page.request.delete(url + '/api/sessions/' + sid).ok
    assert page.request.post(url + '/api/sessions/' + sid + '/resume').ok
    reopened = page.request.get(url + '/api/sessions').json()['sessions'][0]
    assert reopened['model'] == 'vendor/second' and reopened['effort'] == 'low'
    page.reload()
    expect(page.locator('#chat-model')).to_have_value(json.dumps(['work-router', 'vendor/second'], separators=(',', ':')))
    expect(page.locator('#effort-value')).to_have_text('Low')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('#controls').evaluate('node => node.scrollWidth <= node.clientWidth')


def test_model_change_failure_keeps_the_active_model_and_draft(workspace, chat_provider):
    page, url = workspace
    assert page.request.put(url + '/api/providers/connections', data={
        'id': 'work-router', 'host_id': 'openrouter', 'api_key': KEY,
        'base_url': chat_provider.base, 'configure_chat': True,
    }).ok
    page.route('**/api/providers/work-router/models?modality=chat', lambda route: route.fulfill(json={
        'models': [{'id': 'deepseek/deepseek-v4-flash'}, {'id': 'vendor/second'}],
    }))
    page.reload()
    page.locator('#input').fill('Start the conversation')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_contain_text('Ready to help')
    page.locator('#input').fill('Keep this draft')
    page.route('**/api/sessions/*/model', lambda route: route.fulfill(
        status=409, json={'detail': 'Finish the current reply before changing models.'}))
    page.locator('#chat-model').select_option(json.dumps(['work-router', 'vendor/second'], separators=(',', ':')))
    expect(page.locator('#transcript')).to_contain_text('Finish the current reply')
    expect(page.locator('#chat-model')).to_have_value(json.dumps(['work-router', 'deepseek/deepseek-v4-flash'], separators=(',', ':')))
    expect(page.locator('#input')).to_have_value('Keep this draft')


def test_setup_can_be_skipped_and_tour_is_replayable(workspace):
    page, _ = workspace
    # The fixture skips welcome for other workspace regression tests. Open it
    # manually here to verify dismissing it survives a reload.
    page.locator('#setup-connect').click()
    page.locator('#welcome-preview-tour').click()
    expect(page.locator('#guide-dialog')).to_be_visible()
    page.locator('#guide-next').click()
    expect(page.locator('#guide-title')).to_have_text('You stay in control.')
    page.locator('#guide-back').click()
    expect(page.locator('#guide-title')).to_have_text('Start with what you need.')
    for _ in range(5):
        page.locator('#guide-next').click()
    expect(page.locator('#guide-dialog')).to_be_hidden()
    page.reload()
    expect(page.locator('#welcome-dialog')).to_be_hidden()
    expect(page.locator('#setup-banner')).to_be_visible()
    page.locator('#open-guide').click()
    expect(page.locator('#welcome-dialog')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#welcome-dialog')).to_be_hidden()


@pytest.mark.parametrize('width,height', [(390, 844), (320, 568), (844, 390)])
def test_mobile_setup_tour_and_settings_fit_screen(workspace, width, height):
    page, _ = workspace
    page.set_viewport_size({'width': width, 'height': height})
    page.locator('#setup-connect').click()
    page.locator('#welcome-key').fill('never-persist-this')
    page.locator('#welcome-show-key').click()
    expect(page.locator('#welcome-key')).to_have_attribute('type', 'text')
    box = page.locator('#welcome-dialog').bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= width
    page.screenshot(path=f'/tmp/omote-welcome-{width}.png')
    page.locator('#welcome-preview-tour').click()
    box = page.locator('#guide-dialog').bounding_box()
    assert box['width'] <= width and box['height'] <= height
    page.screenshot(path=f'/tmp/omote-tour-{width}.png')
    page.keyboard.press('Escape')
    if width <= 760:
        page.locator('#sidebar-toggle').click()
    page.locator('#open-settings').click()
    expect(page.locator('#panel-connections')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=f'/tmp/omote-settings-{width}.png')


def test_provider_load_failure_can_be_retried(workspace):
    page, _ = workspace
    page.route('**/api/providers/catalog', lambda route: route.fulfill(status=503, json={'detail': 'offline'}))
    page.locator('#setup-connect').click()
    expect(page.locator('#welcome-status')).to_contain_text('Could not load providers')
    page.locator('#welcome-close').click()
    page.unroute('**/api/providers/catalog')
    page.locator('#setup-connect').click()
    expect(page.locator('#welcome-provider')).to_have_value('openrouter')
    expect(page.locator('#welcome-status')).to_be_empty()


def test_settings_tabs_are_keyboard_operable(workspace):
    page, _ = workspace
    page.locator('#open-settings').click()
    expect(page.locator('#panel-connections')).to_be_visible()
    page.locator('#tab-connections').focus()
    page.keyboard.press('ArrowRight')
    expect(page.locator('#tab-updates')).to_be_focused()
    expect(page.locator('#panel-updates')).to_be_visible()
    page.keyboard.press('Home')
    expect(page.locator('#tab-browser')).to_be_focused()
    expect(page.locator('#panel-browser')).to_be_visible()


def test_chat_creation_failure_preserves_draft(workspace, chat_provider):
    page, _ = workspace
    use_fake(page, chat_provider)
    page.locator('#setup-connect').click()
    page.locator('#welcome-key').fill(KEY)
    page.locator('#welcome-submit').click()
    page.locator('#welcome-start').click()
    page.route('**/api/sessions', lambda route: route.fulfill(status=503, json={'detail': 'Provider temporarily unavailable'}) if route.request.method == 'POST' else route.continue_())
    page.locator('#input').fill('Keep my draft')
    page.locator('#send').click()
    expect(page.locator('#transcript')).to_contain_text('Provider temporarily unavailable')
    expect(page.locator('#input')).to_have_value('Keep my draft')
    expect(page.locator('#input')).to_be_enabled()
    page.unroute('**/api/sessions')
    page.locator('#send').click()
    expect(page.locator('#transcript')).to_contain_text('Ready to help with your first task.')


def test_key_entry_waits_for_provider_catalog_to_finish(workspace, chat_provider):
    page, url = workspace
    use_fake(page, chat_provider)
    held = []
    page.route('**/api/providers/catalog', lambda route: held.append(route))
    page.locator('#setup-connect').click()
    expect(page.locator('#welcome-key')).to_be_disabled()
    expect(page.locator('#welcome-submit')).to_be_disabled()
    page.wait_for_timeout(100)
    assert held
    catalog = page.request.get(url + '/api/providers/catalog').json()
    held[0].fulfill(json=catalog)
    expect(page.locator('#welcome-key')).to_be_enabled()
    page.locator('#welcome-key').fill(KEY)
    page.locator('#welcome-submit').click()
    expect(page.locator('#welcome-ready-panel')).to_be_visible()


def test_explore_does_not_force_tutorial_and_studio_has_setup_action(workspace):
    page, _ = workspace
    page.locator('#setup-connect').click()
    page.locator('#welcome-skip').click()
    expect(page.locator('#welcome-dialog')).to_be_hidden()
    expect(page.locator('#guide-dialog')).to_be_hidden()
    page.locator('#start-studio').click()
    expect(page.locator('#studio-connection-help')).to_be_visible()
    expect(page.locator('#studio-make')).to_be_disabled()
    page.locator('#studio-connect-provider').click()
    expect(page.locator('#panel-connections')).to_be_visible()
    expect(page.locator('#connection-advanced')).to_have_attribute('open', '')


def test_connected_desktop_new_chat_and_tour(workspace, chat_provider):
    page, _ = workspace
    use_fake(page, chat_provider)
    page.locator('#setup-connect').click()
    page.screenshot(path='/tmp/omote-welcome-desktop.png')
    page.locator('#welcome-key').fill(KEY)
    page.locator('#welcome-submit').click()
    expect(page.locator('#welcome-ready-panel')).to_be_visible()
    page.screenshot(path='/tmp/omote-connected-desktop.png')
    page.locator('#welcome-tour').click()
    expect(page.locator('#guide-title')).to_have_text('Start with what you need.')
    page.screenshot(path='/tmp/omote-tour-desktop.png')
    page.locator('#guide-close').click()
    page.locator('#open-guide').click()
    expect(page.locator('#guide-dialog')).to_be_visible()
    page.keyboard.press('Escape')
    page.locator('#nav-new').click()
    expect(page.locator('#new-dialog')).to_be_visible()
    expect(page.locator('#new-dialog input[name=model]')).to_be_hidden()
    page.locator('.session-advanced summary').click()
    expect(page.locator('#new-dialog input[name=model]')).to_be_visible()
    page.keyboard.press('Escape')
    page.screenshot(path='/tmp/omote-ready-workspace.png')
