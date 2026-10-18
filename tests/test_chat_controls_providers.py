"""Every chat preset exercises its real adapter and serialized reasoning request.

No vendor credentials: the recording transport supplies vendor-shaped replies.
The browser matrix separately tests the controls against real local HTTP.
"""
from __future__ import annotations

import json

import pytest

from omote.providers import connections
from omote.providers import reasoning as R
from omote.providers.base import (
    ChatRequest,
    Message,
    Modality,
    StreamText,
    StreamThinking,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from omote.providers.catalog import HOSTS
from omote.providers.registry import can_serve

HOSTS_WITH_CHAT = [h for h in HOSTS if Modality.CHAT in h.modalities]

# Expected wire fields are independent of the production translation tables.
CASES = {
    'openai': ('gpt-5.6', {'reasoning_effort': 'medium'}),
    'anthropic': ('claude-sonnet-4-6', {'output_config': {'effort': 'medium'}}),
    'groq': ('openai/gpt-oss-20b', {'reasoning_effort': 'medium'}),
    'openrouter': ('vendor/reasoner', {'reasoning': {'effort': 'medium'}}),
    'fireworks': ('accounts/fireworks/models/qwen3-8b', {'reasoning_effort': 'medium'}),
    'together': ('Qwen/Qwen3-32B', {'reasoning': {'enabled': True}}),
    'siliconflow': ('Qwen/Qwen3-32B', {'enable_thinking': True, 'thinking_budget': 4096}),
    'alibaba': ('qwen-plus', {'enable_thinking': True, 'thinking_budget': 4096}),
    'deepseek': ('deepseek-flash', {'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low'}),
    'xai': ('grok-4.7', {'reasoning_effort': 'medium'}),
    'mistral': ('mistral-medium-3-5', {'reasoning_effort': 'high'}),
    'google': ('gemini-2.5-pro', {'reasoning_effort': 'medium'}),
    'ollama': ('gpt-oss:20b', {'think': 'medium'}),
    'perch': ('gpt-oss:20b', {'think': 'medium'}),
}


class Response:
    status = 200

    def __init__(self, body=None, lines=()):
        self.body = body
        async def content():
            for line in lines:
                yield line
        self.content = content()

    async def json(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class RecordingTransport:
    def __init__(self, model):
        self.model = model
        self.requests = []
        self.row = {'id': model, 'reasoning': {'supported_efforts': ['high', 'medium', 'low'], 'mandatory': True}}

    def session(self, *args):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.requests.append(('GET', url, kwargs))
        if url.endswith('/models'):
            return Response({'data': [self.row]})
        return Response({'id': self.model, 'max_output_tokens': 8192})

    def post(self, url, **kwargs):
        self.requests.append(('POST', url, kwargs))
        if url.endswith('/api/show'):
            return Response({'capabilities': ['completion', 'tools', 'thinking'],
                             'thinking': {'values': ['low', 'medium', 'high']}})
        if url.endswith('/api/chat'):
            chunks = [{'message': {'content': 'provider reply'}, 'done': True}]
            return Response(lines=[(json.dumps(c) + '\n').encode() for c in chunks])
        if url.endswith('/messages'):
            chunks = [
                {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'provider reply'}},
                {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}},
            ]
        else:
            chunks = [{'choices': [{'delta': {'content': 'provider reply'}, 'finish_reason': 'stop'}]}]
        return Response(lines=[('data: ' + json.dumps(c) + '\n\n').encode() for c in chunks])


@pytest.mark.parametrize('host', HOSTS_WITH_CHAT, ids=lambda h: h.id)
async def test_every_chat_preset_serializes_the_selected_model_and_effort(host, monkeypatch):
    model, expected = CASES.get(host.id, ('Qwen/Qwen3-32B', {'reasoning_effort': 'medium'}))
    transport = RecordingTransport(model)
    monkeypatch.setattr(connections.Connection, 'transport', lambda self: transport)
    conn = connections.from_host(host.id, api_key='test-only', connection_id='chosen-provider')
    _, impls = connections.build(conn)
    impl = impls[Modality.CHAT]
    impl.transport = transport  # Perch deliberately constructs its own transport.
    events = [e async for e in impl.stream(ChatRequest(model=model, messages=[], effort='medium'))]
    assert any(isinstance(e, StreamText) and e.text == 'provider reply' for e in events)
    body = [kw['json'] for method, url, kw in transport.requests
            if method == 'POST' and not url.endswith('/api/show')][-1]
    assert body['model'] == model
    for field, value in expected.items():
        assert body[field] == value, (host.id, body)
    assert 'effort' not in body, 'the UI spelling must be translated by the adapter'


@pytest.mark.parametrize('requested,expected', [
    ('off', 'low'), ('low', 'low'), ('medium', 'medium'), ('high', 'high'), ('xhigh', 'high'), ('max', 'high'),
])
async def test_openrouter_mandatory_thinking_clamps_all_slider_positions(requested, expected):
    from omote.providers.openrouter import OpenRouterProvider
    transport = RecordingTransport('vendor/reasoner')
    provider = OpenRouterProvider(transport=transport)
    assert (await provider.models())[0]['reasoning']['mandatory']
    for _ in range(2):
        _ = [e async for e in provider.stream(ChatRequest(model=transport.model, messages=[], effort=requested))]
    posts = [kw['json'] for method, url, kw in transport.requests if method == 'POST']
    assert all(body['reasoning'] == {'effort': expected} for body in posts)
    assert len([url for method, url, _ in transport.requests if method == 'GET']) == 1


@pytest.mark.parametrize('model', ['openrouter/free', 'openrouter/auto', 'vendor/plain'])
async def test_openrouter_without_reasoning_controls_leaves_requests_at_default(model):
    from omote.providers.openrouter import OpenRouterProvider
    transport = RecordingTransport(model)
    transport.row = {'id': model}
    provider = OpenRouterProvider(transport=transport)
    _ = [e async for e in provider.stream(ChatRequest(model=model, messages=[], effort='off'))]
    body = [kw['json'] for method, _, kw in transport.requests if method == 'POST'][-1]
    assert 'reasoning' not in body


def test_slider_only_offers_distinct_supported_settings():
    assert R.reasoning_control('gpt-4o', base_url='https://api.openai.com/v1')['levels'] == []
    assert R.reasoning_control('gpt-6-astra', base_url='https://api.openai.com/v1')['levels'] == ['low', 'medium', 'high', 'xhigh', 'max']
    assert R.reasoning_control('gpt-oss:20b', adapter='ollama')['levels'] == ['low', 'medium', 'high']
    assert R.reasoning_control('custom:7b', adapter='ollama', row={
        'capabilities': ['completion', 'thinking'], 'thinking': {'values': [True, False]},
    })['levels'] == ['off', 'low']
    assert R.reasoning_control('claude-sonnet-4-6', adapter='anthropic')['levels'] == ['low', 'medium', 'high', 'max']
    assert R.reasoning_control('claude-haiku-4-5', adapter='anthropic')['levels'][0] == 'off'
    assert R.reasoning_control('vendor/reasoner', adapter='openrouter', row={
        'reasoning': {'supported_efforts': ['high', 'medium', 'low'], 'mandatory': True},
    })['levels'] == ['low', 'medium', 'high']
    assert R.reasoning_control('openrouter/free', adapter='openrouter')['levels'] == []


def test_binary_thinking_is_labelled_on_and_mandatory_models_cannot_be_disabled():
    row = {'reasoning': {'mandatory': False}}
    assert R.openrouter_reasoning('off', row) == {'reasoning': {'enabled': False}}
    assert R.openrouter_reasoning('max', row) == {'reasoning': {'enabled': True}}
    control = R.reasoning_control('vendor/hybrid', adapter='openrouter', row=row)
    assert control['levels'] == ['off', 'low'] and control['labels'] == {'low': 'On'}
    row['reasoning']['mandatory'] = True
    assert R.openrouter_reasoning('off', row) == {}
    assert R.reasoning_control('vendor/mandatory', adapter='openrouter', row=row)['levels'] == []


async def test_ollama_discovers_boolean_thinking_and_omits_it_for_plain_models():
    from omote.providers.ollama import OllamaProvider
    transport = RecordingTransport('custom:7b')
    provider = OllamaProvider(transport=transport)
    provider._capability_cache['custom:7b'] = ['completion', 'thinking']
    provider._thinking_cache['custom:7b'] = {'values': [True, False]}
    for level, expected in [('high', True), ('off', False)]:
        _ = [e async for e in provider.stream(ChatRequest(model='custom:7b', messages=[], effort=level))]
        assert transport.requests[-1][2]['json']['think'] is expected
    provider._capability_cache['plain:7b'] = ['completion']
    _ = [e async for e in provider.stream(ChatRequest(model='plain:7b', messages=[], effort='max'))]
    assert 'think' not in transport.requests[-1][2]['json']


async def test_anthropic_capability_tree_does_not_hide_models_from_chat_routing():
    from omote.providers.anthropic import AnthropicProvider
    transport = RecordingTransport('claude-sonnet-4-6')
    transport.row['capabilities'] = {'thinking': {'types': {'adaptive': {'supported': True}}}}
    provider = AnthropicProvider(transport=transport)
    row = (await provider.models())[0]
    assert can_serve(row, Modality.CHAT)
    assert row['reasoning_capabilities'] == transport.row['capabilities']


@pytest.mark.parametrize('model,host,expected', [
    ('grok-4.7', 'https://api.x.ai/v1', ['low', 'medium', 'high', 'xhigh']),
    ('grok-4.5', 'https://api.x.ai/v1', ['low', 'medium', 'high']),
    ('grok-4.3', 'https://api.x.ai/v1', ['off', 'low', 'medium', 'high']),
    ('mistral-medium-3-5', 'https://api.mistral.ai/v1', ['off', 'high']),
    ('zai-glm-5-3', 'https://api.mistral.ai/v1', ['low', 'high', 'max']),
    ('deepseek-flash', 'https://api.deepseek.com/v1', ['off', 'low', 'high', 'max']),
])
def test_additional_official_provider_ladders(model, host, expected):
    assert R.reasoning_control(model, base_url=host)['levels'] == expected


def test_a_model_with_one_fixed_effort_displays_its_actual_level():
    control = R.reasoning_control('gpt-5-pro', base_url='https://api.openai.com/v1')
    assert control['levels'] == ['high']
    assert 'labels' not in control
    control = R.reasoning_control('custom:7b', adapter='ollama', row={'thinking': {'values': ['high']}})
    assert control['levels'] == ['high']


def test_deepseek_off_and_legacy_models_use_different_protocol_controls():
    base = 'https://api.deepseek.com/v1'
    assert R.openai_compat_reasoning('off', 'deepseek-flash', base) == {'thinking': {'type': 'disabled'}}
    assert R.openai_compat_reasoning('max', 'deepseek-v4-pro', base) == {
        'thinking': {'type': 'enabled'}, 'reasoning_effort': 'max'}
    assert R.openai_compat_reasoning('high', 'deepseek-reasoner', base) == {}


async def test_deepseek_preserves_reasoning_for_subsequent_tool_requests_only_on_that_host():
    from omote.providers.openai_compat import OpenAICompatProvider
    messages = [Message(role='assistant', content=[
        ThinkingBlock(text='Preserved provider reasoning'), TextBlock(text='Checking'),
        ToolUseBlock(id='call1', name='read_file', input={'path': 'README.md'}),
    ])]
    for host, replay in [('https://api.deepseek.com/v1', True), ('https://api.openai.com/v1', False)]:
        transport = RecordingTransport('deepseek-flash')
        provider = OpenAICompatProvider(host, transport=transport)
        _ = [e async for e in provider.stream(ChatRequest(model='deepseek-flash', messages=messages, effort='high'))]
        sent = transport.requests[-1][2]['json']['messages'][0]
        assert ('reasoning_content' in sent) is replay
        if replay:
            assert sent['reasoning_content'] == 'Preserved provider reasoning'
        assert 'Preserved provider reasoning' not in str(sent['content'])


async def test_mistral_typed_thinking_chunks_are_separate_from_the_answer():
    from omote.providers.openai_compat import OpenAICompatProvider
    transport = RecordingTransport('mistral-medium-3-5')
    content = [{'type': 'thinking', 'thinking': [{'type': 'text', 'text': 'Working it out'}]},
               {'type': 'text', 'text': 'Final answer'}]
    def post(url, **kwargs):
        return Response(lines=[('data: ' + json.dumps({'choices': [{
            'delta': {'content': content}, 'finish_reason': 'stop',
        }]}) + '\n\n').encode()])
    transport.post = post
    provider = OpenAICompatProvider('https://api.mistral.ai/v1', transport=transport)
    events = [e async for e in provider.stream(ChatRequest(model=transport.model, messages=[], effort='high'))]
    assert [e.text for e in events if isinstance(e, StreamText)] == ['Final answer']
    assert [e.text for e in events if isinstance(e, StreamThinking)] == ['Working it out']
