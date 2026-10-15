"""Native app sessions, real schema discovery, scope and approval boundaries."""

import json

import pytest

from omote.agent.approval import ApprovalPolicy, Mode
from omote.agent.session import AgentSession
from omote.agent.tools.base import ToolContext, ToolError
from omote.agent.tools.creative_apps import CreativeAppsTool
from omote.agent.tools.discovery import LazyTool
from omote.creative.apps import APPS, catalog
from omote.mcp.client import MCPResult, MCPTool
from omote.mcp.manager import ServerConfig, load_config, manager
from omote.protocol.agent import Risk, ToolCall
from tests.test_agent import ScriptedProvider


async def noop(*args):
    return None


def context(root, owner='test'):
    return ToolContext(root=root, cwd=root, session_id=owner, emit=noop, ask=noop)


class Engine:
    def __init__(self, name, command, args, **kwargs):
        self.args = args
        self.running = False
        self.document = None
        self.tools = [MCPTool('doc_new', 'Create an editable document', {'type': 'object'}, server=name),
                      MCPTool('doc_inspect', 'Inspect a document', {'type': 'object'}, {'readOnlyHint': True}, server=name),
                      MCPTool('destroy', 'Delete an existing export', {'type': 'object'}, {'destructiveHint': True}, server=name)]

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    async def call(self, name, arguments):
        if name == 'doc_new':
            self.document = arguments
        return MCPResult(json.dumps({'document': self.document}))


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr('omote.agent.tools.creative_apps.StdioServer', Engine)
    monkeypatch.setattr('omote.agent.tools.creative_apps.executable', lambda _: '/known/operator/engine')
    monkeypatch.setattr('omote.creative.apps.executable', lambda _: '/known/operator/engine')
    return CreativeAppsTool()


async def test_native_state_persists_until_explicit_close_and_schemas_are_required(tool, tmp_path):
    ctx = context(tmp_path)
    opened = json.loads((await tool.run({'action': 'tools', 'app': 'photocraft', 'filter': 'doc_new'}, ctx)).content)
    ident = opened['session']
    engine = tool.sessions[ident]['server']
    assert '--automation-read-root' in engine.args and str(tmp_path) in engine.args
    with pytest.raises(ToolError, match='Discover'):
        await tool.run({'action': 'call', 'session': ident, 'tool': 'doc_inspect'}, ctx)
    await tool.run({'action': 'call', 'session': ident, 'tool': 'doc_new', 'arguments': {'name': 'Layered project'}}, ctx)
    await tool.run({'action': 'tools', 'session': ident, 'filter': 'doc_inspect'}, ctx)
    result = await tool.run({'action': 'call', 'session': ident, 'tool': 'doc_inspect'}, ctx)
    assert json.loads(result.content)['document']['name'] == 'Layered project'
    assert tool.assess({'action': 'call', 'session': ident, 'tool': 'doc_inspect'}, ctx).risk is Risk.EXECUTE
    with pytest.raises(ToolError, match='this chat'):
        await tool.run({'action': 'call', 'session': ident, 'tool': 'doc_inspect'}, context(tmp_path, 'other'))
    await tool.run({'action': 'close', 'session': ident}, ctx)
    assert not engine.running and not tool.sessions


async def test_unscoped_engine_is_reported_and_not_given_unsupported_flags(tool, tmp_path):
    rows = catalog()['apps']
    assert len([row for row in rows if row['kind'] == 'local']) == 13
    assert next(row for row in rows if row['id'] == 'filmcraft')['filesystem_scope'] == 'process account permissions'
    opened = json.loads((await tool.run({'action': 'tools', 'app': 'filmcraft'}, context(tmp_path))).content)
    assert tool.sessions[opened['session']]['server'].args == ['mcp']
    await tool.close()


async def test_lazy_editor_cleanup_and_unused_tools_stay_unloaded(tmp_path, monkeypatch):
    monkeypatch.setattr('omote.agent.tools.creative_apps.StdioServer', Engine)
    monkeypatch.setattr('omote.agent.tools.creative_apps.executable', lambda _: '/known/engine')
    lazy = LazyTool('creative_apps', 'Creative engines', 'omote.agent.tools.creative_apps', 'CreativeAppsTool')
    agent = AgentSession(root=tmp_path, provider=ScriptedProvider([]), model='tools-capable', tools=[lazy], policy=ApprovalPolicy(mode=Mode.UNRESTRICTED))
    opened = json.loads((await lazy.run({'action': 'tools', 'app': 'photocraft'}, agent._context('turn', 'call'))).content)
    engine = lazy._instance.sessions[opened['session']]['server']
    assert engine.running
    await agent.close()
    assert not engine.running
    unused = LazyTool('unused', 'Unused', 'module_that_must_not_load', 'Nope')
    await unused.close()
    assert not unused.loaded


async def test_creative_tool_cannot_bypass_readonly_approvals(tool, tmp_path):
    agent = AgentSession(root=tmp_path, provider=ScriptedProvider([]), model='tools-capable', tools=[tool], policy=ApprovalPolicy(mode=Mode.READ_ONLY))
    result = await agent.invoke(ToolCall(id='blocked', name=tool.name, arguments={'action': 'tools', 'app': 'photocraft'}), 'turn')
    assert result.is_error and not tool.sessions
    assert tool.assess({'action': 'tools', 'app': 'photocraft', 'workspace': '../'}, context(tmp_path)).invalid


async def test_only_explicit_creative_connectors_are_visible_and_close_does_not_stop_shared_server(tool, tmp_path, monkeypatch):
    visible = Engine('adobe', '', [])
    hidden = Engine('private-database', '', [])
    await visible.start()
    await hidden.start()
    monkeypatch.setattr(manager, 'configs', {'adobe': ServerConfig('adobe', creative=True), 'private-database': ServerConfig('private-database')})
    monkeypatch.setattr(manager, 'servers', {'adobe': visible, 'private-database': hidden})
    assert 'mcp:adobe' in {row['id'] for row in catalog()['apps']}
    assert 'mcp:private-database' not in {row['id'] for row in catalog()['apps']}
    ctx = context(tmp_path)
    assert tool.assess({'action': 'tools', 'app': 'mcp:private-database'}, ctx).invalid
    opened = json.loads((await tool.run({'action': 'tools', 'app': 'mcp:adobe'}, ctx)).content)
    await tool.run({'action': 'close', 'session': opened['session']}, ctx)
    assert visible.running
    path = tmp_path / '.mcp.json'
    path.write_text(json.dumps({'mcpServers': {'adobe': {'command': 'configured-bridge', 'creative': True}}}))
    assert load_config(path)[0].creative


async def test_opening_extra_engines_never_silently_discards_unsaved_documents(tool, tmp_path):
    ctx = context(tmp_path)
    for app in list(APPS)[:4]:
        await tool.run({'action': 'tools', 'app': app}, ctx)
    with pytest.raises(ToolError, match='Save and close'):
        await tool.run({'action': 'tools', 'app': 'pdfcraft'}, ctx)
    assert len(tool.sessions) == 4 and all(record['server'].running for record in tool.sessions.values())
    await tool.close()
