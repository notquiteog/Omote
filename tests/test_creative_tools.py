"""The worker operates the same creative records that Studio displays."""

import json
from types import SimpleNamespace

import pytest

from omote.agent.tools.base import ToolError
from omote.config import config
from omote.creative.store import CreativeStore
from omote.creative.tools import CreativeLibraryTool, creative_tools
from omote.media.store import MediaStore
from omote.protocol.agent import Risk
from omote.routers import creative


@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.setattr(creative, 'store', CreativeStore(tmp_path / 'creative'))
    monkeypatch.setattr(creative, 'media_store', MediaStore(tmp_path / 'media'))
    monkeypatch.setattr(config, 'default_user', 'owner')
    return CreativeLibraryTool()


async def run(tool, **args):
    return json.loads((await tool.run(args, SimpleNamespace())).content)


def test_read_and_write_assessments_and_validation(tool):
    for action in ('list_characters', 'list_assets', 'recipe', 'get_character', 'get_asset'):
        assert tool.assess({'action': action, 'id': 'known'}, SimpleNamespace()).risk == Risk.READ
    for action in ('save_character', 'update_character', 'prepare', 'import_generated', 'update_asset',
                   'delete_character', 'delete_asset'):
        assert tool.assess({'action': action, 'id': 'known'}, SimpleNamespace()).risk == Risk.WRITE
    assert tool.assess({'action': 'delete_asset'}, SimpleNamespace()).invalid
    assert tool.assess({'action': 'invented'}, SimpleNamespace()).invalid
    assert len(creative_tools()) == 1


async def test_worker_creates_finds_updates_and_reuses_character(tool):
    saved = await run(tool, action='save_character', fields={
        'name': 'Mei', 'project': 'The river', 'appearance_prompt': 'Blue jacket, cropped hair',
        'negative_prompt': 'different clothes',
    })
    assert creative.store.get_character('owner', saved['id'])['appearance_prompt'].startswith('Blue')
    listed = await run(tool, action='list_characters', q='Mei')
    assert listed['characters'][0]['id'] == saved['id']
    updated = await run(tool, action='update_character', id=saved['id'], fields={'description': 'An engineer'})
    assert updated['name'] == 'Mei' and updated['appearance_prompt'].startswith('Blue')
    recipe = await run(tool, action='prepare', id=saved['id'])
    assert recipe['prompt'] == 'An engineer\nBlue jacket, cropped hair'
    assert recipe['negative_prompt'] == 'different clothes'
    assert recipe['image'] is None
    assert (await run(tool, action='delete_character', id=saved['id']))['ok']
    with pytest.raises(ToolError, match='No such'):
        await run(tool, action='recipe', id=saved['id'])


async def test_tool_errors_are_actionable_and_cannot_write_other_users_records(tool):
    other = creative.store.save_character('other', {'name': 'Private'})
    with pytest.raises(ToolError, match='No such'):
        await run(tool, action='update_character', id=other['id'], fields={'name': 'Stolen'})
    with pytest.raises(ToolError):
        await run(tool, action='save_character', fields={'name': '   '})
    with pytest.raises(ToolError):
        await run(tool, action='save_character', fields={'name': 'Name', 'api_key': 'private'})
    assert creative.store.get_character('other', other['id'])['name'] == 'Private'
