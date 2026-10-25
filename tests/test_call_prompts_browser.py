"""Actual agent approvals and questions remain usable on the full-screen call surface."""
import re

import pytest

from tests.commands import python_command
from tests.test_chat_controls_browser import connect
from tests.test_chat_controls_browser import provider_server as provider_server
from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace

expect = playwright.expect


@pytest.mark.parametrize('kind', ['talk', 'live'])
@pytest.mark.parametrize('decision', ['Allow', 'Deny'])
def test_call_shows_real_approval_and_permission_selector(workspace, provider_server, tmp_path, kind, decision):
    page, url = workspace
    connect(page, url, provider_server)
    provider_server['next_tool'] = {'name': 'shell', 'input': {
        'command': python_command(tmp_path, 'print("call approval test")'),
    }}
    page.locator('#input').fill('Run the harmless test command')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .decide')).to_be_visible()
    # Hold the audio handshake; this test never opens a real microphone.
    page.route_web_socket(re.compile(r'.*/ws/(voice|realtime).*'), lambda ws: None)
    page.evaluate("kind => { import('/static/call.js').then(m => { void m.startCall(kind); }); }", kind)
    expect(page.locator('#call-prompts .decide')).to_be_visible()
    expect(page.locator('#call-permissions #mode-wrap')).to_be_visible()
    expect(page.locator('#transcript')).to_be_hidden()
    page.locator('#call-prompts').get_by_role('button', name=decision, exact=True).click()
    expect(page.locator('#call-prompts')).to_be_hidden()
    page.evaluate("async () => { const m = await import('/static/call.js'); await m.endCall(); }")
    expect(page.locator('#composer #mode-wrap')).to_be_visible()
    expect(page.locator('#transcript .tool .decide')).to_have_count(0)
    expect(page.locator('#transcript')).to_contain_text('call approval test' if decision == 'Allow' else 'Not run:')


@pytest.mark.parametrize('kind', ['talk', 'live'])
def test_call_shows_and_resolves_a_question(workspace, provider_server, kind):
    page, url = workspace
    connect(page, url, provider_server)
    provider_server['next_tool'] = {'name': 'ask_user', 'input': {
        'question': 'Which colour?', 'options': ['Blue', 'Green'],
    }}
    page.locator('#input').fill('Ask which colour')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .question')).to_be_visible()
    page.route_web_socket(re.compile(r'.*/ws/(voice|realtime).*'), lambda ws: None)
    page.evaluate("kind => { import('/static/call.js').then(m => { void m.startCall(kind); }); }", kind)
    expect(page.locator('#call-prompts .question')).to_be_visible()
    page.locator('#call-prompts').get_by_role('button', name='Green', exact=True).click()
    expect(page.locator('#call-prompts')).to_be_hidden()
    page.evaluate("async () => { const m = await import('/static/call.js'); await m.endCall(); }")
    expect(page.locator('#transcript')).to_contain_text('You answered: Green')



def test_reopening_a_conversation_keeps_its_selected_permission_mode(workspace, provider_server):
    page, url = workspace
    connect(page, url, provider_server)
    page.locator('#input').fill('Start a conversation')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript')).to_contain_text('Reply from')
    page.locator('#mode').select_option('unrestricted')
    expect(page.locator('#mode-wrap')).to_have_attribute('title', re.compile('unrestricted'))
    session = page.request.get(url + '/api/sessions').json()['sessions'][0]
    assert page.request.delete(url + '/api/sessions/' + session['id']).ok
    response = page.request.post(url + '/api/sessions/' + session['id'] + '/resume')
    assert response.ok
    assert response.json()['session']['policy'] == 'unrestricted'
