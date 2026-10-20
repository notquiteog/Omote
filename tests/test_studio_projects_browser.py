"""Guided project UI, preserved drafts and real local 3D frame capture."""
import re

from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace

expect = playwright.expect


def open_projects(page):
    page.locator('#start-studio').click()
    page.locator('#studio-projects-open').click()
    expect(page.locator('.project-template')).to_have_count(6)


def create(page, name='River film', template='Cinematic story'):
    page.locator('.project-template').filter(has_text=template).click()
    page.get_by_label('Project name', exact=True).fill(name)
    page.get_by_label('What are you making?', exact=True).fill('A traveler follows a river at dawn.')
    page.get_by_role('button', name='Create plan', exact=True).click()
    expect(page.locator('.project-shot')).to_have_count(1 if template in ('Stage in 3D', 'Remix a video', 'Visual effects') else 3)


def test_cinema_direction_prepares_without_rendering_and_restores_draft(workspace):
    page, url = workspace
    calls = []
    page.on('request', lambda request: calls.append(request.url) if request.method == 'POST' and request.url.endswith('/run') else None)
    page.locator('#start-studio').click()
    page.locator('#studio-prompt').fill('Keep my earlier image idea')
    page.locator('#studio-projects-open').click()
    create(page)
    page.get_by_text('Look & camera', exact=True).click()
    page.get_by_label('Lens', exact=True).select_option('50mm portrait')
    page.get_by_label('Lighting', exact=True).select_option('Golden hour')
    page.locator('.project-shot').first.get_by_label('What happens in this shot?', exact=True).fill('She reaches the river.')
    page.locator('.project-shot').first.get_by_role('button', name='Prepare image', exact=True).click()
    expect(page.locator('#studio-projects')).not_to_be_visible()
    expect(page.locator('#studio-prompt')).to_have_value(re.compile('She reaches the river.*50mm portrait', re.S))
    expect(page.locator('#studio-project-context')).to_contain_text('River film')
    assert calls == []
    page.locator('#studio-project-restore').click()
    expect(page.locator('#studio-prompt')).to_have_value('Keep my earlier image idea')
    assert page.request.get(url + '/api/creative/projects').json()['projects'][0]['revision'] == 2


def test_narration_reordering_persists_and_agent_handoff_preserves_chat(workspace):
    page, url = workspace
    page.locator('#input').evaluate("e => { e.value = 'My existing chat draft'; }")
    open_projects(page)
    create(page, template='Narrated short')
    first = page.locator('.project-shot').first
    first.get_by_label('Voiceover script', exact=True).fill('Why do rivers curve?')
    first.get_by_label('On-screen caption', exact=True).fill('Follow the river')
    first.get_by_text('Edit shot', exact=True).click()
    first.get_by_role('button', name='Move down', exact=True).click()
    expect(page.locator('.project-shot').nth(1).get_by_label('Voiceover script')).to_have_value('Why do rivers curve?')
    page.get_by_role('button', name='Ask AI', exact=True).click()
    expect(page.locator('#input')).to_have_value(re.compile(r'My existing chat draft\n\nHelp me develop Studio project'))
    assert page.locator('#transcript .turn.user').count() == 0
    rows = page.request.get(url + '/api/creative/projects').json()['projects']
    saved = page.request.get(url + '/api/creative/projects/' + rows[0]['id']).json()
    assert saved['shots'][1]['narration'] == 'Why do rivers curve?'
    page.reload()
    open_projects(page)
    page.locator('.project-recent').filter(has_text='River film').click()
    expect(page.locator('.project-shot').nth(1).get_by_label('On-screen caption')).to_have_value('Follow the river')


def test_mobile_projects_fit_and_unsaved_draft_survives_close(workspace):
    page, _ = workspace
    page.set_viewport_size({'width': 390, 'height': 844})
    open_projects(page)
    assert page.locator('#studio-projects').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
    create(page, template='Product campaign')
    page.get_by_label('Creative brief', exact=True).fill('A small ceramic cup')
    page.locator('#studio-projects').get_by_role('button', name='Close', exact=True).click()
    page.locator('#studio-projects-open').click()
    expect(page.get_by_label('Creative brief', exact=True)).to_have_value('A small ceramic cup')
    assert page.locator('#studio-projects').evaluate('e => e.scrollWidth <= e.clientWidth + 1')


def test_3d_stage_is_lazy_editable_and_captures_reference(workspace):
    page, url = workspace
    loaded = []
    page.on('request', lambda r: loaded.append(r.url) if 'three.module.js' in r.url else None)
    open_projects(page)
    create(page, template='Stage in 3D')
    assert not loaded
    page.get_by_role('button', name='Open 3D stage', exact=True).click()
    expect(page.locator('.stage-viewport canvas')).to_be_visible()
    assert loaded
    page.get_by_role('button', name='Add sphere', exact=True).click()
    page.get_by_label('Object name', exact=True).fill('Moon')
    page.get_by_label('Position X', exact=True).fill('2')
    page.get_by_label('Position X', exact=True).press('Tab')
    page.get_by_role('button', name='Capture reference frame', exact=True).click()
    expect(page.locator('.stage-inspector')).to_contain_text('Reference frame saved', timeout=15000)
    rows = page.request.get(url + '/api/creative/projects').json()['projects']
    project = page.request.get(url + '/api/creative/projects/' + rows[0]['id']).json()
    assert project['stage']['objects'][-1]['name'] == 'Moon'
    assert project['stage']['objects'][-1]['position'][0] == 2
    reference = project['shots'][0]['reference_asset_ids'][0]
    asset = page.request.get(url + '/api/creative/assets/' + reference).json()
    assert asset['kind'] == 'image'
    assert page.request.get(url + asset['url']).body().startswith(b'\x89PNG')
    page.get_by_role('button', name='Back to plan', exact=True).click()
    expect(page.locator('.stage-viewport canvas')).to_have_count(0)
    expect(page.locator('.project-shot')).to_contain_text('Stage reference')


def test_remix_prepares_video_control_and_blocks_text_only_model(workspace):
    page, _ = workspace
    # These are model schemas, not fabricated renders; no generation is sent.
    page.route('**/api/media/describe?**', lambda route: route.fulfill(json={
        'provider': 'fake', 'local': True, 'model': 'text-only',
        'providers': [{'id': 'fake', 'label': 'Fake', 'local': True}],
        'models': [{'id': 'text-only'}], 'params': [],
    }))
    open_projects(page)
    create(page, template='Remix a video')
    page.locator('.project-shot').get_by_role('button', name='Prepare video', exact=True).click()
    expect(page.locator('#studio-project-context')).to_contain_text('Add a source video')
    expect(page.locator('#studio-make')).to_be_disabled()


def test_reopening_loads_agent_changes_and_conflicts_keep_browser_draft(workspace):
    page, url = workspace
    open_projects(page)
    create(page)
    saved = page.request.get(url + '/api/creative/projects').json()['projects'][0]
    original = page.request.get(url + '/api/creative/projects/' + saved['id']).json()
    fields = {k: v for k, v in original.items() if k not in ('id', 'revision', 'created', 'updated')}
    page.get_by_label('Creative brief', exact=True).fill('My unsaved browser edits')
    fields['name'] = 'Agent revision'
    result = page.request.put(url + '/api/creative/projects/' + saved['id'], data={'document': fields, 'base_revision': 1})
    assert result.ok
    page.get_by_role('button', name='Save plan', exact=True).click()
    expect(page.locator('.project-status')).to_contain_text('changed elsewhere')
    expect(page.get_by_label('Creative brief', exact=True)).to_have_value('My unsaved browser edits')
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='Reload plan', exact=True).click()
    expect(page.get_by_label('Project name', exact=True)).to_have_value('Agent revision')
    page.locator('#studio-projects').get_by_role('button', name='Close', exact=True).click()
    fields['name'] = 'Another saved revision'
    assert page.request.put(url + '/api/creative/projects/' + saved['id'], data={'document': fields, 'base_revision': 2}).ok
    page.locator('#studio-projects-open').click()
    expect(page.get_by_label('Project name', exact=True)).to_have_value('Another saved revision')
