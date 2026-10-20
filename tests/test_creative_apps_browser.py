"""Simple creative-app discovery and model-assisted workflow handoff."""

import re

from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace

expect = playwright.expect


def app_catalog():
    return {'apps': [
        {'id': 'vectorcraft', 'label': 'VectorCraft', 'available': True, 'purpose': 'Editable vector images and exports',
         'setup': 'Discover installed tools in chat.', 'filesystem_scope': 'engine-enforced folders',
         'documentation': 'https://github.com/storytold/vectorcraft/blob/main/docs/mcp.md'},
        {'id': 'filmcraft', 'label': 'FilmCraft', 'available': False, 'purpose': 'Native video timelines',
         'setup': 'Install filmcraft-cli.', 'filesystem_scope': 'process account permissions', 'documentation': ''}],
        'note': 'Availability is checked; export codecs still depend on the installed engine.'}


def test_creative_app_discovery_hands_off_to_chat_without_submitting(workspace):
    page, _ = workspace
    page.route('**/api/creative/apps', lambda route: route.fulfill(json=app_catalog()))
    page.locator('[data-mode="studio"]').click()
    page.locator('#studio-creative-apps').click()
    expect(page.locator('#creative-apps-status')).to_contain_text('1 available')
    expect(page.locator('#creative-apps-detail')).to_contain_text('Editable vector images')
    page.get_by_role('button', name='Use in chat', exact=True).click()
    expect(page.locator('#creative-apps-dialog')).not_to_be_visible()
    expect(page.locator('#input')).to_have_value(re.compile('creative_apps with vectorcraft'))
    assert page.locator('#transcript .turn.user').count() == 0


def test_mobile_creative_apps_keep_draft_and_show_actual_scope(workspace):
    page, _ = workspace
    page.set_viewport_size({'width': 390, 'height': 844})
    page.route('**/api/creative/apps', lambda route: route.fulfill(json=app_catalog()))
    # A restored draft can exist even if its provider is currently disconnected.
    page.locator('#input').evaluate("node => { node.value = 'Keep this unsent idea'; }")
    page.locator('#sidebar-toggle').click()
    page.locator('[data-mode="studio"]').click()
    assert page.locator('#pane-studio').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
    page.locator('#studio-creative-apps').click()
    page.locator('#creative-apps-choice').select_option('filmcraft')
    expect(page.locator('#creative-apps-detail')).to_contain_text('process account permissions')
    assert page.locator('#creative-apps-dialog').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
    page.get_by_role('button', name='Set up with chat').click()
    expect(page.locator('#input')).to_have_value('Keep this unsent idea')


def test_edit_workflow_with_chat_requires_saved_version(workspace):
    page, url = workspace
    response = page.request.post(url + '/api/media/comfyui/workflows', data={'name': 'Chat-editable workflow',
        'prompt': {'1': {'class_type': 'EmptyLatentImage', 'inputs': {'width': 512, 'height': 512, 'batch_size': 1}}}})
    assert response.ok
    saved = response.json()
    page.locator('[data-mode="studio"]').click()
    page.locator('#studio-graph').click()
    page.locator('#comfy-library-toggle').click()
    page.locator('.wb-saved').filter(has_text='Chat-editable workflow').click()
    expect(page.locator('#wb-dirty')).to_have_text('Version 1')
    page.locator('#wb-name').fill('Unsaved workflow edit')
    page.locator('#wb-assistant').click()
    expect(page.locator('#wb-status')).to_contain_text('Save this version first')
    expect(page.locator('#pane-studio')).to_be_visible()
    page.locator('#wb-save').click()
    expect(page.locator('#wb-dirty')).to_contain_text('Version 2')
    page.locator('#wb-assistant').click()
    expect(page.locator('#input')).to_have_value(re.compile(saved['id']))
    expect(page.locator('#input')).to_have_value(re.compile('revision 2'))


def test_workflow_assistant_prepares_versioned_request_and_preserves_draft(workspace):
    page, url = workspace
    response = page.request.post(url + '/api/media/comfyui/workflows', data={'name': 'Portrait lighting',
        'prompt': {'1': {'class_type': 'EmptyLatentImage', 'inputs': {'width': 512, 'height': 512, 'batch_size': 1}}}})
    assert response.ok
    saved = response.json()
    page.locator('#input').evaluate("node => { node.value = 'My existing idea'; }")
    page.locator('#start-studio').click()
    page.locator('#studio-graph').click()
    page.locator('#comfy-library-toggle').click()
    page.locator('.wb-saved').filter(has_text='Portrait lighting').click()
    expect(page.locator('#wb-dirty')).to_have_text('Version 1')
    page.locator('#comfy-assistant').click()
    expect(page.locator('#workflow-assistant-context')).to_have_text('Portrait lighting · Version 1')
    page.get_by_role('button', name='Improve quality', exact=True).click()
    page.get_by_role('button', name='Continue in chat', exact=True).click()
    expect(page.locator('#input')).to_have_value(re.compile(r'My existing idea\n\nHelp me with ComfyUI workflow ' + saved['id']))
    expect(page.locator('#input')).to_have_value(re.compile('available GPU memory'))
    assert page.locator('#transcript .turn.user').count() == 0


def test_workflow_assistant_blocks_unsaved_version_and_fits_mobile(workspace):
    page, _ = workspace
    page.locator('#start-studio').click()
    page.locator('#studio-graph').click()
    page.locator('#comfy-library-toggle').click()
    page.locator('#wb-name').fill('Unsaved graph')
    page.locator('#comfy-assistant').click()
    page.get_by_role('button', name='Fix an error', exact=True).click()
    page.get_by_role('button', name='Continue in chat', exact=True).click()
    expect(page.locator('#workflow-assistant-status')).to_contain_text('Save your changes')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('#workflow-assistant').evaluate('node => node.scrollWidth <= node.clientWidth')
    expect(page.get_by_role('button', name='Continue in chat')).to_be_in_viewport()
    page.locator('#workflow-assistant-close').click()
    expect(page.locator('#wb-name')).to_have_value('Unsaved graph')
