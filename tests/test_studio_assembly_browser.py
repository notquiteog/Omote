"""Finish a real video through the UI; no remote providers or keys."""
import io
import re

from PIL import Image

from tests.test_studio_projects_browser import create, open_projects
from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace

expect = playwright.expect


def image_bytes():
    data = io.BytesIO()
    Image.new('RGB', (320, 180), '#294c69').save(data, format='PNG')
    return data.getvalue()


def test_choose_upload_export_play_and_reopen(workspace):
    page, url = workspace
    open_projects(page)
    create(page, name='A quiet blue', template='Visual effects')
    page.get_by_label('Seconds', exact=True).fill('1')
    page.get_by_text('Edit shot', exact=True).click()
    page.get_by_role('button', name='Add voiceover & caption', exact=True).click()
    page.get_by_label('On-screen caption', exact=True).fill('A moment of calm')
    page.get_by_role('button', name='Make final video', exact=True).click()
    expect(page.get_by_text('Bring your story together')).to_be_visible()
    page.get_by_role('button', name='Export video', exact=True).click()
    expect(page.locator('.export-settings')).to_contain_text('Choose an image or video for every shot.')
    page.locator('.export-shot input[type=file]').set_input_files({
        'name': 'blue.png', 'mimeType': 'image/png', 'buffer': image_bytes(),
    })
    expect(page.locator('.export-preview img')).to_be_visible()
    expect(page.locator('.export-caption')).to_have_text('A moment of calm')
    page.get_by_role('button', name='Export video', exact=True).click()
    expect(page.locator('.export-result > strong').first).to_have_text(re.compile('Ready'), timeout=30000)
    video = page.locator('.export-result video').first
    expect(video).to_be_visible()
    video.evaluate('e => e.load()')
    page.wait_for_function('document.querySelector(".export-result video").readyState >= 2')
    assert video.evaluate('e => e.duration') >= 1
    with page.expect_download() as download:
        page.get_by_role('link', name='Download MP4').click()
    assert download.value.suggested_filename == 'A quiet blue.mp4'
    page.get_by_role('button', name='Back to plan', exact=True).click()
    expect(page.get_by_label('On-screen caption', exact=True)).to_have_value('A moment of calm')
    page.get_by_role('button', name='Make final video', exact=True).click()
    expect(page.locator('.export-preview img')).to_be_visible()
    expect(page.locator('.export-result > strong').first).to_contain_text('Ready')


def test_mobile_export_fit_and_stale_plan_recovery(workspace):
    page, url = workspace
    page.set_viewport_size({'width': 390, 'height': 844})
    upload = page.request.post(url + '/api/creative/assets/upload?filename=blue.png', data=image_bytes())
    assert upload.status == 201
    asset = upload.json()
    open_projects(page)
    create(page, name='Mobile film', template='Visual effects')
    page.get_by_role('button', name='Make final video', exact=True).click()
    picker = page.get_by_label('Visual for Create the effect', exact=True)
    picker.select_option('asset:' + asset['id'])
    expect(page.locator('.export-preview img')).to_be_visible()
    assert page.locator('#studio-projects').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
    page.get_by_label('Sound', exact=True).select_option('narration')
    expect(page.locator('.export-settings')).to_contain_text('Connect a voice provider')
    page.get_by_label('Sound', exact=True).select_option('silent')
    projects = page.request.get(url + '/api/creative/projects').json()['projects']
    current = page.request.get(url + '/api/creative/projects/' + projects[0]['id']).json()
    document = {k: v for k, v in current.items() if k not in ('id', 'revision', 'created', 'updated')}
    document['name'] = 'Agent changed the plan'
    assert page.request.put(url + '/api/creative/projects/' + current['id'], data={
        'base_revision': current['revision'], 'document': document,
    }).ok
    page.get_by_role('button', name='Export video', exact=True).click()
    expect(page.locator('.export-settings')).to_contain_text('The plan changed')
    assert page.request.get(url + '/api/creative/projects/' + current['id'] + '/exports').json()['exports'] == []
    page.get_by_role('button', name='Back to plan', exact=True).click()
    page.get_by_role('button', name='Reload plan', exact=True).click()
    expect(page.locator('.project-dialog-header h2')).to_have_text('Agent changed the plan')


def test_missing_video_tools_offer_setup_with_cancel_and_retry(workspace):
    page, _ = workspace
    job = {'id': 'setup-fixture', 'state': 'running', 'note': 'Downloading video tools'}
    starts = []
    page.route('**/export-options', lambda route: route.fulfill(json={
        'available': False, 'can_install': True, 'providers': [],
    }))
    def start(route):
        starts.append(True)
        job.update(state='running', note='Downloading video tools')
        route.fulfill(status=202, json=job)
    def status(route):
        if route.request.method == 'DELETE':
            job.update(state='cancelled', note='Setup cancelled. Choose Set up video export to resume.')
        route.fulfill(json=job)
    page.route('**/api/media/setup/video', start)
    page.route('**/api/media/jobs/setup-fixture', status)
    open_projects(page)
    create(page, name='Setup a film', template='Visual effects')
    page.get_by_role('button', name='Make final video', exact=True).click()
    expect(page.get_by_role('button', name='Export video', exact=True)).to_be_disabled()
    page.get_by_role('button', name='Set up video export', exact=True).click()
    expect(page.locator('.export-settings')).to_contain_text('Downloading video tools')
    page.get_by_role('button', name='Cancel setup', exact=True).click()
    expect(page.get_by_role('button', name='Set up video export', exact=True)).to_be_visible()
    page.get_by_role('button', name='Set up video export', exact=True).click()
    job.update(state='done', note='Video export is ready')
    expect(page.get_by_role('button', name='Export video', exact=True)).to_be_enabled()
    expect(page.get_by_role('button', name='Cancel setup', exact=True)).not_to_be_visible()
    assert len(starts) == 2
