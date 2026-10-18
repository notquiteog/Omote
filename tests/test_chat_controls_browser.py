"""Chat controls on each provider preset, using each adapter's real HTTP protocol."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests import test_workspace_browser
from tests.test_chat_controls_providers import CASES, HOSTS_WITH_CHAT

workspace = test_workspace_browser.workspace
playwright = test_workspace_browser.playwright

expect = playwright.expect


@pytest.fixture
def provider_server():
    state = {'model': '', 'requests': []}
    class Handler(BaseHTTPRequestHandler):
        def respond(self, data, content_type='application/json'):
            payload = data.encode() if isinstance(data, str) else json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802
            model = state['model']
            if self.path.endswith('/api/tags'):
                return self.respond({'models': [{'name': m} for m in (model, model + '-alternate')]})
            if self.path.endswith('/models'):
                return self.respond({'data': [{
                    'id': m, 'reasoning': state.get('reasoning', {}).get(m, {
                        'supported_efforts': ['high', 'medium', 'low'], 'mandatory': True}),
                } for m in (model, model + '-alternate')]})
            return self.respond({'id': model, 'max_output_tokens': 8192})

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            if self.path.endswith('/api/show'):
                return self.respond({'capabilities': ['completion', 'tools', 'thinking'],
                                     'thinking': {'values': ['low', 'medium', 'high']}})
            state['requests'].append(body)
            answer = 'Reply from ' + body['model']
            if self.path.endswith('/api/chat'):
                return self.respond(json.dumps({'message': {'content': answer}, 'done': True}) + '\n', 'application/x-ndjson')
            if self.path.endswith('/messages'):
                chunks = [
                    {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': answer}},
                    {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}},
                ]
            else:
                chunks = [{'choices': [{'delta': {'content': answer}, 'finish_reason': 'stop'}]}]
            return self.respond(''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks), 'text/event-stream')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state['base'] = f'http://127.0.0.1:{server.server_port}'
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def connect(page, url, provider, *, name='openrouter', adapter='openrouter', model='vendor/reasoner'):
    provider['model'] = model
    ident = 'test-' + name
    # Perch chat is its Ollama service. The provider contract matrix also
    # exercises Perch's production connection builder.
    assert page.request.put(url + '/api/providers/connections', data={
        'id': ident, 'label': name, 'adapter': adapter, 'base_url': provider['base'],
        'api_key': 'isolated-test-only', 'modalities': ['chat'], 'local': True,
    }).ok
    assert page.request.put(url + '/api/providers/routes', data={
        'modality': 'chat', 'provider': ident, 'model': model,
    }).ok
    page.reload()
    expect(page.locator('#chat-model option')).to_have_count(3)
    return ident


def choice(provider, model):
    return json.dumps([provider, model], separators=(',', ':'))


@pytest.mark.parametrize('host', HOSTS_WITH_CHAT, ids=lambda h: h.id)
def test_each_provider_model_selection_reasoning_and_conversation(workspace, provider_server, host):
    page, url = workspace
    model = CASES.get(host.id, ('Qwen/Qwen3-32B', {}))[0]
    adapter = 'ollama' if host.adapter == 'perch' else host.adapter
    ident = connect(page, url, provider_server, name=host.id, adapter=adapter, model=model)
    # The native dropdown identifies the default before the first message.
    expect(page.locator('#chat-model')).to_contain_text(model + ' (default)')
    page.locator('#chat-model').select_option(choice(ident, model))
    slider = page.locator('#effort')
    # The local fake addresses use the generic OpenAI dialect. Native
    # Anthropic/Ollama/OpenRouter retain their own reasoning contracts.
    if host.id in ('google', 'mistral', 'xai'):
        # The fake hostname is not Google's official dialect. Conservative
        # custom Gemini endpoints remain at Auto; the request contract tests
        # cover every effort against the real Google hostname.
        expect(slider).to_be_disabled()
    else:
        expect(slider).to_be_enabled()
        slider.press('Home')
        for _ in range(3):
            if page.locator('#effort-value').inner_text() == 'Medium':
                break
            slider.press('ArrowRight')
        expect(slider).to_have_attribute('aria-valuetext', 'Medium')
    page.locator('#input').fill('First provider test message')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_contain_text('Reply from ' + model)
    expect(page.locator('#stop')).to_be_hidden()
    session = page.request.get(url + '/api/sessions').json()['sessions'][0]
    assert session['provider'] == ident and session['model'] == model
    assert session['effort'] == (None if host.id in ('google', 'mistral', 'xai') else 'medium')
    if adapter == 'openrouter':
        assert provider_server['requests'][-1]['reasoning'] == {'effort': 'medium'}
    elif adapter == 'anthropic':
        assert provider_server['requests'][-1]['output_config'] == {'effort': 'medium'}
    elif adapter == 'ollama':
        assert provider_server['requests'][-1]['think'] == 'medium'
    page.locator('#chat-model').select_option(choice(ident, model + '-alternate'))
    expect(page.locator('#chat-model')).to_have_attribute('aria-busy', 'false')
    page.locator('#input').fill('Keep the earlier message')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_have_count(2)
    expect(page.locator('#stop')).to_be_hidden()
    sessions = page.request.get(url + '/api/sessions').json()['sessions']
    assert len(sessions) == 1 and sessions[0]['id'] == session['id']
    sent = provider_server['requests'][-1]
    assert sent['model'] == model + '-alternate'
    assert 'First provider test message' in str(sent['messages'])
    page.reload()
    expect(page.locator('#chat-model')).to_have_value(choice(ident, model + '-alternate'))
    for width, height in [(390, 844), (320, 568)]:
        page.set_viewport_size({'width': width, 'height': height})
        assert page.locator('#controls').evaluate('node => node.scrollWidth <= node.clientWidth')


def test_pending_model_switch_preserves_draft_and_blocks_old_model_send(workspace, provider_server):
    page, url = workspace
    ident = connect(page, url, provider_server)
    page.locator('#input').fill('Start the chat')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_contain_text('Reply from')
    expect(page.locator('#stop')).to_be_hidden()
    held = []
    page.route('**/api/sessions/*/model', lambda route: held.append(route))
    page.locator('#chat-model').select_option(choice(ident, 'vendor/reasoner-alternate'))
    expect(page.locator('#chat-model')).to_have_attribute('aria-busy', 'true')
    expect(page.locator('#send')).to_be_disabled()
    page.locator('#input').fill('Send only with my newly selected model')
    page.locator('#input').press('Enter')
    expect(page.locator('#input')).to_have_value('Send only with my newly selected model')
    assert len(provider_server['requests']) == 1
    held[0].fulfill(response=held[0].fetch())
    expect(page.locator('#send')).to_be_enabled()
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_have_count(2)
    expect(page.locator('#stop')).to_be_hidden()
    assert provider_server['requests'][-1]['model'] == 'vendor/reasoner-alternate'


def test_non_reasoning_model_has_quiet_disabled_slider(workspace, provider_server):
    page, url = workspace
    provider_server['model'] = 'vendor/plain'
    ident = connect(page, url, provider_server, adapter='openai', model='vendor/plain')
    page.locator('#chat-model').select_option(choice(ident, 'vendor/plain'))
    expect(page.locator('#effort')).to_be_disabled()
    expect(page.locator('#effort-value')).to_have_text('Auto')
    expect(page.locator('#effort-wrap')).to_have_attribute('title', 'This model manages reasoning automatically; there is no adjustable reasoning setting.')


def test_requested_effort_survives_switch_to_a_model_with_fewer_levels(workspace, provider_server):
    page, url = workspace
    provider_server['reasoning'] = {'vendor/reasoner-alternate': {
        'supported_efforts': ['none', 'low', 'medium', 'high', 'max'], 'mandatory': False,
    }}
    ident = connect(page, url, provider_server)
    page.evaluate("localStorage.setItem('omote.effort', 'max')")
    page.reload()
    expect(page.locator('#chat-model option')).to_have_count(3)
    page.locator('#chat-model').select_option(choice(ident, 'vendor/reasoner'))
    expect(page.locator('#effort-value')).to_have_text('High')
    page.locator('#input').fill('Use the highest supported reasoning')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_contain_text('Reply from')
    expect(page.locator('#stop')).to_be_hidden()
    assert provider_server['requests'][-1]['reasoning'] == {'effort': 'high'}
    page.locator('#chat-model').select_option(choice(ident, 'vendor/reasoner-alternate'))
    expect(page.locator('#chat-model')).to_have_attribute('aria-busy', 'false')
    expect(page.locator('#effort-value')).to_have_text('Max')
    page.locator('#input').fill('Keep my original preference')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_have_count(2)
    expect(page.locator('#stop')).to_be_hidden()
    assert provider_server['requests'][-1]['reasoning'] == {'effort': 'max'}


def test_controls_wait_for_first_chat_creation(workspace, provider_server):
    page, url = workspace
    connect(page, url, provider_server)
    held = []
    def hold_create(route):
        if route.request.method == 'POST':
            held.append(route)
        else:
            route.continue_()
    page.route('**/api/sessions', hold_create)
    page.locator('#input').fill('Create a chat with the choices I selected')
    page.locator('#input').press('Enter')
    expect(page.locator('#chat-model')).to_be_disabled()
    expect(page.locator('#effort')).to_be_disabled()
    expect(page.locator('#send')).to_be_disabled()
    held[0].fulfill(response=held[0].fetch())
    expect(page.locator('#transcript .turn.assistant')).to_contain_text('Reply from')
    expect(page.locator('#stop')).to_be_hidden()
    expect(page.locator('#chat-model')).to_be_enabled()
    expect(page.locator('#effort')).to_be_enabled()


def test_same_model_id_on_two_providers_switches_provider_without_losing_history(workspace, provider_server):
    page, url = workspace
    first = connect(page, url, provider_server, name='first')
    assert page.request.put(url + '/api/providers/connections', data={
        'id': 'test-second', 'label': 'second', 'adapter': 'openrouter', 'base_url': provider_server['base'],
        'modalities': ['chat'], 'local': True,
    }).ok
    page.reload()
    expect(page.locator('#chat-model option')).to_have_count(5)
    page.locator('#chat-model').select_option(choice(first, 'vendor/reasoner'))
    page.locator('#input').fill('Remember this first provider message')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_contain_text('Reply from')
    expect(page.locator('#stop')).to_be_hidden()
    page.locator('#chat-model').select_option(choice('test-second', 'vendor/reasoner'))
    expect(page.locator('#chat-model')).to_have_attribute('aria-busy', 'false')
    page.locator('#input').fill('Now use the second provider')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_have_count(2)
    expect(page.locator('#stop')).to_be_hidden()
    session = page.request.get(url + '/api/sessions').json()['sessions'][0]
    assert session['provider'] == 'test-second'
    expect(page.locator('#provider-chip')).to_have_text('second')
    assert 'Remember this first provider message' in str(provider_server['requests'][-1]['messages'])
