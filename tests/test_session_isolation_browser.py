"""Switch chats while old socket, context and microphone work is still pending."""

from tests.test_chat_controls_browser import connect, expect
from tests.test_chat_controls_browser import provider_server as provider_server
from tests.test_workspace_browser import workspace as workspace


def two_chats(page, url, provider):
    ident = connect(page, url, provider)
    sessions = []
    for title in ['First private chat', 'Second private chat']:
        response = page.request.post(url + '/api/sessions', data={
            'provider': ident, 'model': provider['model'], 'title': title,
        })
        assert response.ok
        sessions.append(response.json()['id'])
    page.reload()
    expect(page.locator('#sessions [role=button]')).to_have_count(2)
    page.locator('#sessions [role=button]').filter(has_text='First private chat').click()
    expect(page.locator('#voice-toggle')).to_be_enabled()
    return sessions


def switch(page, name):
    page.locator('#sessions [role=button]').filter(has_text=name).click()
    expect(page.locator('#session-title')).to_have_text(name)


def test_chat_drafts_history_and_delayed_context_do_not_cross(workspace, provider_server):
    page, url = workspace
    first, second = two_chats(page, url, provider_server)
    page.locator('#input').fill('Only the first chat should know this phrase')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_contain_text('Reply from')
    expect(page.locator('#stop')).to_be_hidden()
    page.locator('#input').fill('First unsent draft')
    switch(page, 'Second private chat')
    expect(page.locator('#input')).to_have_value('')
    expect(page.locator('#transcript')).not_to_contain_text('Only the first chat')
    page.locator('#input').fill('Second independent message')
    page.locator('#input').press('Enter')
    expect(page.locator('#transcript .turn.assistant')).to_contain_text('Reply from')
    assert 'Only the first chat' not in str(provider_server['requests'][-1]['messages'])
    held = []
    page.route(f'**/api/sessions/{first}/context', lambda route: held.append(route))
    switch(page, 'First private chat')
    expect(page.locator('#input')).to_have_value('First unsent draft')
    page.wait_for_timeout(100)
    assert held
    switch(page, 'Second private chat')
    expect(page.locator('#input')).to_have_value('')
    held[0].fulfill(json={'tokens': 9999999, 'total_in': 9999999, 'fraction': .99})
    expect(page.locator('#context-text')).not_to_have_text('99%')
    assert first != second


def test_switch_cancels_pending_voice_and_ignores_old_socket_callbacks(workspace, provider_server):
    page, url = workspace
    connect(page, url, provider_server)
    # Capture callbacks as if they were already queued by the browser when
    # navigation happens. Only the voice handshake is faked; chat is real.
    page.add_init_script('''
      const NativeSocket = window.WebSocket;
      window.chatSockets = []; window.voiceSockets = [];
      window.WebSocket = class {
        static OPEN = 1; static CLOSING = 2;
        constructor(url) {
          url = String(url);
          if (!url.includes('/ws/voice')) {
            const ws = new NativeSocket(url);
            if (url.includes('/ws/agent')) window.chatSockets.push(ws);
            return ws;
          }
          this.readyState = 0; this.sent = []; window.voiceSockets.push(this);
        }
        send(data) { this.sent.push(data); }
        close() { this.readyState = 3; this.closed = true; }
      };
    ''')
    two_chats(page, url, provider_server)
    page.locator('#voice-toggle').click()
    page.evaluate('''() => {
      window.lateMessage = chatSockets.at(-1).onmessage;
      window.lateClose = chatSockets.at(-1).onclose;
      document.querySelector('#talk-log').textContent = 'Old talk transcript';
      document.querySelector('#live-log').textContent = 'Old live transcript';
    }''')
    switch(page, 'Second private chat')
    page.evaluate('''() => {
      lateMessage({data: JSON.stringify({type:'text.delta', text:'LEAKED OLD CHAT', seq:999})});
      lateClose({code:4404});
    }''')
    expect(page.locator('#session-title')).to_have_text('Second private chat')
    expect(page.locator('#transcript')).not_to_contain_text('LEAKED')
    expect(page.locator('#talk-log')).to_be_empty()
    expect(page.locator('#live-log')).to_be_empty()
    assert page.evaluate('voiceSockets.length === 1 && voiceSockets[0].closed && voiceSockets[0].sent.length === 0')
    expect(page.locator('#voice-strip')).to_be_hidden()


def test_stopped_voice_ignores_queued_audio_and_text(workspace):
    page, _ = workspace
    result = page.evaluate('''async () => {
      const { Voice } = await import('/static/voice.js');
      const Native = window.WebSocket;
      const sockets = [];
      window.WebSocket = class {
        static OPEN = 1; static CLOSING = 2;
        constructor() { this.readyState = 0; this.sent = []; sockets.push(this); queueMicrotask(() => { this.readyState = 1; this.onopen?.(); }); }
        send(data) { this.sent.push(JSON.parse(data)); }
        close() { this.readyState = 3; }
      };
      const events = [];
      try {
        const voice = new Voice({ said: text => events.push(text), speaking: () => events.push('audio'), stopped: () => events.push('stopped') });
        await voice.start({ agentSession: 'first' });
        const late = sockets[0].onmessage;
        let closed = 0;
        voice.player = { close() { closed++; } };
        voice.stop();
        late({data: JSON.stringify({type: 'assistant.text.delta', text:'old answer'})});
        late({data: new ArrayBuffer(20)});
        voice.stop();
        return { events, closed, state: sockets[0].readyState, sent:sockets[0].sent };
      } finally { window.WebSocket = Native; }
    }''')
    assert result['events'] == ['stopped']
    assert result['closed'] == 1 and result['state'] == 3
    assert result['sent'][0]['agent_session_id'] == 'first'
    assert result['sent'][-1]['type'] == 'voice.stop'
