"""Agents edit real versioned workflow records; only the Comfy socket is doubled."""

import copy
import json

import pytest

from omote.agent.tools.base import ToolError
from omote.agent.tools.comfy_workflow import ComfyWorkflowTool
from omote.agent.tools.media import media_tools
from omote.config import config
from tests.test_creative_apps import context
from tests.test_workbench import CATALOG, GRAPH, PROMPT, Response
from tests.test_workbench import bench as bench


async def call(tool, ctx, **args):
    return json.loads((await tool.run(args, ctx)).content)


async def test_workflow_discovery_atomic_patch_validation_exact_revision_and_export(bench, tmp_path):
    _, socket, _, _ = bench
    tool, ctx = ComfyWorkflowTool(), context(tmp_path)
    nodes = await call(tool, ctx, action='nodes', provider='comfy', query='Source')
    assert list(nodes['nodes']) == ['Source'] and nodes['nodes']['Source'] == CATALOG['Source']
    saved = await call(tool, ctx, action='save', fields={'name': 'Agent scene', 'prompt': copy.deepcopy(PROMPT)})
    ident = saved['id']
    updated = await call(tool, ctx, action='patch', id=ident, base_revision=1,
                         changes=[{'op': 'set_input', 'node': '1', 'input': 'seed', 'value': 24}])
    assert updated['revision'] == 2 and updated['prompt']['2'] == PROMPT['2']
    subset = await call(tool, ctx, action='get', id=ident, query='Source')
    assert subset['prompt_nodes']['1']['inputs']['seed'] == 24
    assert '2' not in subset['prompt_nodes']
    with pytest.raises(ToolError, match='changed'):
        await call(tool, ctx, action='patch', id=ident, base_revision=1,
                   changes=[{'op': 'set_input', 'node': '1', 'input': 'seed', 'value': 50}])
    with pytest.raises(ToolError, match='does not exist'):
        await call(tool, ctx, action='patch', id=ident, base_revision=2, changes=[
            {'op': 'set_input', 'node': '1', 'input': 'seed', 'value': 90}, {'op': 'remove_node', 'node': 'missing'}])
    assert (await call(tool, ctx, action='get', id=ident))['prompt']['1']['inputs']['seed'] == 24
    assert (await call(tool, ctx, action='validate', id=ident, provider='comfy'))['valid']
    invalid = await call(tool, ctx, action='patch', id=ident, base_revision=2,
                         changes=[{'op': 'set_input', 'node': '1', 'input': 'seed', 'value': 200}])
    assert invalid['revision'] == 3
    with pytest.raises(ToolError):
        await call(tool, ctx, action='run', id=ident, provider='comfy', revision=3)
    run = await call(tool, ctx, action='run', id=ident, provider='comfy', revision=2)
    payload = next(options['json'] for method, url, options in socket.calls if method == 'POST' and url.endswith('/prompt'))
    assert payload['prompt']['1']['inputs']['seed'] == 24
    socket.routes[('GET', '/history')] = Response({'owned-prompt': {'outputs': {}, 'status': {'status_str': 'success'}}})
    assert (await call(tool, ctx, action='status', id=run['id']))['state'] == 'done'
    exported = await call(tool, ctx, action='export', id=ident, revision=2, path='scene.json', format='api')
    assert json.loads((tmp_path / 'scene.json').read_text()) == updated['prompt']
    assert exported['revision'] == 2
    assert tool.assess({'action': 'export', 'id': ident, 'path': 'scene.json'}, ctx).invalid
    assert tool.assess({'action': 'export', 'id': ident, 'path': '../outside.json'}, ctx).invalid


async def test_editor_layout_never_becomes_stale_silently_and_original_versions_remain(bench, tmp_path, monkeypatch):
    tool, ctx = ComfyWorkflowTool(), context(tmp_path)
    saved = await call(tool, ctx, action='save', fields={'name': 'Native flow', 'graph': copy.deepcopy(GRAPH), 'prompt': copy.deepcopy(PROMPT)})
    args = {'action': 'patch', 'id': saved['id'], 'base_revision': 1,
            'changes': [{'op': 'set_input', 'node': '1', 'input': 'seed', 'value': 15}]}
    with pytest.raises(ToolError, match='editor layout'):
        await call(tool, ctx, **args)
    with pytest.raises(ToolError, match='editor layout'):
        await call(tool, ctx, action='save', id=saved['id'], fields={'name': 'Edited', 'prompt': copy.deepcopy(PROMPT), 'base_revision': 1})
    result = await call(tool, ctx, **args, drop_editor_layout=True)
    assert result['graph'] is None and result['warning']
    assert (await call(tool, ctx, action='get', id=saved['id'], revision=1))['graph'] == GRAPH
    monkeypatch.setattr(config, 'default_user', 'other-user')
    with pytest.raises(ToolError, match='No such'):
        await call(tool, ctx, action='get', id=saved['id'])


def test_image_only_installations_offer_workflow_import_and_job_inspection():
    class ImageOnly:
        def can_generate(self):
            return {'image'}

        def can_edit(self):
            return False

    names = {tool.name for tool in media_tools(ImageOnly())}
    assert {'generate_image', 'media_job', 'import_workflow'} <= names
