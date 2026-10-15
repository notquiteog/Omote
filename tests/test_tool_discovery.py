"""Exercise the model-facing discovery path and its permission boundaries."""

import json

from omote.agent.approval import ApprovalPolicy, Mode
from omote.agent.runtime import build_session, default_tools
from omote.agent.session import AgentSession
from omote.agent.tools.base import Assessment, Output, Tool
from omote.agent.tools.discovery import CACHE_SIZE, LazyTool
from omote.protocol.agent import Risk, ToolCall, ToolDenied
from omote.providers.base import StreamDone, StreamText, StreamToolUse
from tests.test_agent import ScriptedProvider, turn


class OptionalTool(Tool):
    description = 'Inspect the optional remote catalog.'

    def __init__(self, name='optional_catalog', risk=Risk.READ):
        self.name = name
        self.risk = risk
        self.schema_reads = 0
        self.runs = 0

    @property
    def input_schema(self):
        self.schema_reads += 1
        return {'type': 'object', 'properties': {'query': {'type': 'string', 'description': 'x' * 4000}}}

    def assess(self, args, ctx):
        return Assessment(risk=self.risk, summary='inspect optional catalog')

    async def run(self, args, ctx):
        self.runs += 1
        return Output(content='catalog result')


def script_call(ident, name, **args):
    return [StreamToolUse(id=ident, name=name, input=args), StreamDone(stop_reason='tool_use')]


def session(tmp_path, tools, script=(), mode=Mode.ASK):
    return AgentSession(root=tmp_path, provider=ScriptedProvider(list(script)), model='test',
                        tools=tools, policy=ApprovalPolicy(mode=mode))


async def test_model_discovers_schema_before_using_optional_tool(tmp_path):
    optional = OptionalTool()
    agent = session(tmp_path, [optional], [
        script_call('find', 'find_tools', query=optional.name),
        script_call('use', optional.name, query='details'),
        [StreamText(text='done'), StreamDone()],
    ])
    assert optional.schema_reads == 0
    assert [t.name for t in agent._visible_tools()] == ['find_tools']
    agent.context_report()
    assert optional.schema_reads == 0, 'context estimates eagerly loaded an unused schema'
    await agent.start()
    await turn(agent, 'Inspect the optional catalog')
    first, second, _ = agent.provider.seen
    assert [t.name for t in first.tools] == ['find_tools']
    assert optional.name in {t.name for t in second.tools}
    assert optional.runs == 1
    assert 'find_tools' in first.system


async def test_browsing_metadata_does_not_load_schemas_and_supports_pages(tmp_path):
    tools = [OptionalTool(f'optional_{i}') for i in range(9)]
    agent = session(tmp_path, tools)
    result = await agent.invoke(ToolCall(id='browse', name='find_tools', arguments={'limit': 5}), 'turn')
    data = json.loads(result.content)
    assert data['total'] == 9 and data['next_offset'] == 5 and not data['loaded']
    assert all('input_schema' not in row for row in data['tools'])
    assert all(tool.schema_reads == 0 for tool in tools)
    assert not agent._discovery.loaded


async def test_optional_cache_is_bounded_and_calls_refresh_recent_use(tmp_path):
    tools = [OptionalTool(f'optional_{i:02}') for i in range(CACHE_SIZE + 2)]
    agent = session(tmp_path, tools)
    for tool in tools[:CACHE_SIZE]:
        await agent.invoke(ToolCall(id=tool.name, name='find_tools', arguments={'query': tool.name}), 'turn')
    await agent.invoke(ToolCall(id='touch', name=tools[0].name, arguments={}), 'turn')
    await agent.invoke(ToolCall(id='next', name='find_tools', arguments={'query': tools[-1].name}), 'turn')
    assert len(agent._discovery.loaded) == CACHE_SIZE
    assert tools[0].name in agent._discovery.loaded
    assert tools[1].name not in agent._discovery.loaded
    assert len(agent._visible_tools()) == CACHE_SIZE + 1


async def test_discovery_does_not_grant_execution_permission(tmp_path):
    optional = OptionalTool('mcp__example__mutate', Risk.EXECUTE)
    agent = session(tmp_path, [optional], [
        script_call('find', 'find_tools', query=optional.name),
        script_call('use', optional.name),
        [StreamText(text='stopped'), StreamDone()],
    ], mode=Mode.READ_ONLY)
    await agent.start()
    events = await turn(agent, 'Inspect this service')
    assert optional.name in {t.name for t in agent.provider.seen[1].tools}
    assert optional.runs == 0
    assert any(isinstance(event, ToolDenied) for event in events)


async def test_toolsets_and_live_plan_mode_filter_discovery(tmp_path):
    agent = build_session(root=tmp_path, provider=ScriptedProvider([]), model='test', toolset=['files'])
    assert 'shell' not in agent.tools
    result = await agent.invoke(ToolCall(id='find', name='find_tools', arguments={'query': 'shell'}), 'turn')
    assert 'shell' not in {row['name'] for row in json.loads(result.content)['tools']}
    await agent.set_mode(Mode.PLAN)
    result = await agent.invoke(ToolCall(id='write', name='find_tools', arguments={'query': 'write_file'}), 'turn')
    assert 'write_file' not in {row['name'] for row in json.loads(result.content)['tools']}
    assert 'propose_plan' in {t.name for t in agent._visible_tools()}


def test_default_optional_implementations_and_schemas_stay_unloaded(tmp_path):
    tools = default_tools()
    lazy = [tool for tool in tools if isinstance(tool, LazyTool)]
    assert {tool.name for tool in lazy} == {'schedule', 'photocraft', 'creative_library'}
    agent = session(tmp_path, tools)
    agent.context_report()
    assert not any(tool.loaded for tool in lazy)


def test_default_request_carries_substantially_less_schema_data(tmp_path):
    agent = build_session(root=tmp_path, provider=ScriptedProvider([]), model='test')

    def serialized_size(tools):
        rows = []
        for tool in tools:
            schema = tool.input_schema
            rows.append({'name': tool.name, 'description': tool.description, 'input_schema': schema})
        return len(json.dumps(rows).encode())

    initial = serialized_size(agent._visible_tools())
    full = serialized_size(agent._eligible_tools())
    assert initial < full * .35, (initial, full)


async def test_bad_discovery_arguments_do_not_change_cache(tmp_path):
    agent = session(tmp_path, [OptionalTool()])
    for arguments in ({'limit': 99}, {'offset': -1}, {'query': []}, {'limit': True}):
        result = await agent.invoke(ToolCall(id=str(arguments), name='find_tools', arguments=arguments), 'turn')
        assert result.is_error
    assert not agent._discovery.loaded


async def test_exact_discovery_materializes_only_the_selected_implementation(tmp_path):
    agent = session(tmp_path, default_tools())
    result = await agent.invoke(ToolCall(id='find', name='find_tools', arguments={'query': 'schedule'}), 'turn')
    rows = json.loads(result.content)['tools']
    assert [row['name'] for row in rows] == ['schedule']
    assert agent.tools['schedule'].loaded
    assert not agent.tools['photocraft'].loaded
    assert not agent.tools['creative_library'].loaded
    result = await agent.invoke(ToolCall(id='plural', name='find_tools', arguments={'query': 'notebooks'}), 'turn')
    assert json.loads(result.content)['tools'][0]['name'] == 'notebook_edit'
