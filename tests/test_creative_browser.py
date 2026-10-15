"""Integrated creative UI with a real daemon and local CPU-only provider double."""
from __future__ import annotations

import base64
import http.server
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from tests.test_workbench import CATALOG, GRAPH, PROMPT

playwright = pytest.importorskip('playwright.sync_api')
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aNmcAAAAASUVORK5CYII=')


@pytest.fixture
def creative_workspace(tmp_path):
    received = []
    history = {}

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, data, content_type='application/json'):
            raw = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/v1/models':
                self.respond({'data': [{'id': 'gpt-image-1'}]})
            elif path == '/object_info':
                self.respond(CATALOG)
            elif path == '/history':
                self.respond(history)
            elif path == '/queue':
                self.respond({'queue_running': [], 'queue_pending': []})
            elif path == '/view':
                self.respond(PNG, 'image/png')
            elif path == '/':
                self.respond(b'<h1>Local ComfyUI test double</h1><p>No GPU or external service.</p>', 'text/html')
            else:
                self.send_error(404)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if self.path == '/prompt':
                received.append(json.loads(raw))
                ident = f'fake-prompt-{len(received)}'
                history[ident] = {'status': {'status_str': 'success'}, 'outputs': {
                    '2': {'images': [{'filename': 'scene.png', 'subfolder': '', 'type': 'output'}]},
                }}
                self.respond({'prompt_id': ident})
            elif self.path == '/queue':
                self.respond({})
            elif self.path in ('/v1/images/edits', '/v1/images/generations'):
                received.append({'image_request': raw.decode(errors='replace')})
                self.respond({'data': [{'b64_json': base64.b64encode(PNG).decode()}]})
            else:
                self.send_error(404)

    upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as temporary:
        temporary.bind(('127.0.0.1', 0))
        port = temporary.getsockname()[1]
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'SYSTEMROOT', 'TMPDIR') if key in os.environ}
    env.update(OMOTE_ENV=os.devnull, OMOTE_DATA_DIR=str(tmp_path), OMOTE_UPDATE_CHECK='false')
    process = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'omote.main:app', '--host', '127.0.0.1', '--port', str(port)],
        cwd=Path(__file__).resolve().parents[1], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(f'{url}/healthz', timeout=1).close()
                break
            except urllib.error.URLError:
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail('isolated creative daemon did not start')
                time.sleep(.05)
        with playwright.sync_playwright() as engine:
            try:
                browser = engine.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f'Chromium is unavailable: {exc}')
            page = browser.new_page(viewport={'width': 1440, 'height': 1050}, color_scheme='dark')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())
            page.add_init_script("localStorage.setItem('omote.welcome.v1', 'done')")
            page.goto(url)
            upstream_url = f'http://127.0.0.1:{upstream.server_port}'
            for body in [
                {'id': 'image-test', 'adapter': 'openai', 'base_url': upstream_url + '/v1',
                 'modalities': ['image'], 'local': True},
                {'id': 'comfy-test', 'adapter': 'comfyui', 'base_url': upstream_url, 'local': True},
            ]:
                response = page.request.put(url + '/api/providers/connections', data=body)
                assert response.ok, response.text()
            response = page.request.put(url + '/api/providers/routes', data={
                'modality': 'image', 'provider': 'image-test', 'model': 'gpt-image-1',
            })
            assert response.ok, response.text()
            yield page, url, received
            assert not errors, errors
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)


def library(page):
    if not page.locator('#pane-studio').is_visible():
        page.locator('[data-mode="studio"]').click()
    page.locator('#studio-library').click()
    playwright.expect(page.locator('#creative-workspace')).to_be_visible()


def upload(page, name='reference.png'):
    page.locator('#asset-upload').set_input_files({'name': name, 'mimeType': 'image/png', 'buffer': PNG})
    playwright.expect(page.locator('#creative-status')).to_have_text('Assets imported locally.')
    playwright.expect(page.locator('#creative-grid .asset-card')).to_have_count(1)


def make_character(page):
    page.locator('#character-new').click()
    form = page.locator('#character-form')
    form.locator('[name=name]').fill('綾・京都の旅人')
    form.locator('[name=project]').fill('桜の物語')
    form.locator('[name=description]').fill('A botanist exploring Kyoto and Berlin.')
    form.locator('[name=appearance_prompt]').fill('銀色の三つ編み、青いスカーフ、緑の瞳')
    form.locator('[name=consistency_notes]').fill('Keep the silver leaf pin in every scene.')
    form.locator('[name=negative_prompt]').fill('different eye colour')
    form.locator('[name=tags]').fill('主人公, botanical, watercolor')
    page.locator('#character-references input').check()
    form.get_by_role('button', name='Save character', exact=True).click()
    playwright.expect(page.locator('#character-dialog')).to_be_hidden()
    playwright.expect(page.locator('#character-list')).to_contain_text('綾・京都の旅人')


def workbench(page):
    page.locator('[data-mode="studio"]').click()
    page.locator('#studio-graph').click()
    page.locator('#comfy-library-toggle').click()
    playwright.expect(page.locator('#comfy-tools')).to_be_visible()
    page.locator('#comfy-provider').select_option('comfy-test')


def test_native_workflow_import_from_generate_keeps_the_editor_graph(creative_workspace):
    page, _, received = creative_workspace
    expect = playwright.expect
    page.locator('#start-studio').click()
    page.locator('#studio-workflow').set_input_files({
        'name': 'native-workflow.json', 'mimeType': 'application/json',
        'buffer': json.dumps(GRAPH).encode(),
    })
    expect(page.locator('#comfy-workspace')).to_be_visible()
    expect(page.locator('#comfy-tools')).to_be_visible()
    expect(page.locator('#wb-name')).to_have_value('native-workflow')
    expect(page.locator('#wb-node-count')).to_contain_text('editor nodes')
    assert not received, 'importing a native graph must not run it'


def import_workflow(page):
    page.locator('#wb-import').set_input_files({
        'name': 'character-scene.json', 'mimeType': 'application/json',
        'buffer': json.dumps({'graph': GRAPH, 'prompt': PROMPT}, ensure_ascii=False).encode(),
    })
    playwright.expect(page.locator('#wb-node-count')).to_have_text('2 executable nodes')
    page.locator('#wb-name').fill('京都のキャラクター')
    page.locator('#wb-project').fill('桜の物語')
    page.locator('#wb-notes').fill('First reproducible scene')
    page.locator('#wb-save').click()
    playwright.expect(page.locator('#wb-dirty')).to_have_text('Version 1')


def test_japanese_character_edit_reference_and_use_survive_reload(creative_workspace):
    page, url, _ = creative_workspace
    expect = playwright.expect
    library(page)
    upload(page)
    make_character(page)
    page.locator('#character-list').get_by_role('button', name='Edit', exact=True).click()
    page.locator('#character-form [name=consistency_notes]').fill('Keep the blue scarf and silver leaf pin.')
    page.locator('#character-form').get_by_role('button', name='Save character', exact=True).click()
    expect(page.locator('#character-dialog')).to_be_hidden()
    page.reload()
    library(page)
    expect(page.locator('#character-list')).to_contain_text('綾・京都の旅人')
    character = page.request.get(url + '/api/creative/characters').json()['characters'][0]
    assert character['consistency_notes'] == 'Keep the blue scarf and silver leaf pin.'
    assert character['tags'] == ['主人公', 'botanical', 'watercolor']
    assert len(character['reference_asset_ids']) == 1
    page.locator('#character-list').get_by_role('button', name='Use', exact=True).click()
    expect(page.locator('#studio-generate')).to_be_visible()
    expect(page.locator('#studio-prompt')).to_have_value(re.compile('銀色の三つ編み'))
    expect(page.locator('#studio-character')).to_contain_text('1 reference image(s) prepared')
    expect(page.locator('#studio-character')).to_contain_text('supports image inputs')
    reference = page.locator('#studio-params .param-image .thumb img').first
    expect(reference).to_be_visible()
    response = page.request.get(url + reference.get_attribute('src'))
    assert response.ok and response.body() == PNG
    recipe = page.request.post(url + f"/api/creative/characters/{character['id']}/prepare").json()
    assert recipe['negative_prompt'] == 'different eye colour'
    assert recipe['image'] in reference.get_attribute('src')
    page.screenshot(animations='disabled', path='/tmp/omote-character-use.png')


def test_asset_details_source_export_and_delete_original(creative_workspace):
    page, url, _ = creative_workspace
    expect = playwright.expect
    library(page)
    upload(page)
    make_character(page)
    page.locator('#creative-grid .asset-card').click()
    expect(page.locator('#asset-preview img')).to_be_visible()
    expect(page.locator('#asset-meta')).to_contain_text('"source": "upload"')
    form = page.locator('#asset-edit-form')
    form.locator('[name=name]').fill('綾の正面参考')
    form.locator('[name=description]').fill('Primary character reference, front view.')
    form.locator('[name=project]').fill('桜の物語')
    form.locator('[name=tags]').fill('front, approved')
    form.get_by_role('button', name='Save details').click()
    expect(page.locator('#asset-error')).to_have_text('Saved.')
    page.locator('#asset-close').click()
    page.locator('#asset-search').fill('approved')
    expect(page.locator('#creative-grid .asset-card')).to_have_count(1)
    expect(page.locator('#creative-grid')).to_contain_text('綾の正面参考')
    page.locator('#asset-kind').select_option('model')
    expect(page.locator('#creative-grid .asset-card')).to_have_count(0)
    page.locator('#asset-kind').select_option('image')
    expect(page.locator('#creative-grid .asset-card')).to_have_count(1)
    with page.expect_download() as downloading:
        page.get_by_role('link', name='Export library', exact=True).click()
    archive_data = Path(downloading.value.path()).read_bytes()
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['characters'][0]['name'] == '綾・京都の旅人'
        assert manifest['assets'][0]['tags'] == ['front', 'approved']
        assert archive.read(manifest['assets'][0]['archive_path']) == PNG
    page.screenshot(animations='disabled', path='/tmp/omote-creative-library-desktop.png')
    page.locator('#creative-grid .asset-card').click()
    page.locator('#asset-delete').click()
    expect(page.locator('#asset-dialog')).to_be_hidden()
    expect(page.locator('#creative-grid .asset-card')).to_have_count(0)
    persisted = page.request.get(url + '/api/creative/characters').json()['characters'][0]
    assert persisted['reference_asset_ids'] == []
    assert page.request.get(url + '/api/creative/assets').json()['total'] == 0


def test_workbench_import_version_inspector_run_and_keep_outputs(creative_workspace):
    page, url, received = creative_workspace
    expect = playwright.expect
    workbench(page)
    import_workflow(page)
    page.locator('#wb-catalog').click()
    expect(page.locator('#wb-status')).to_contain_text('Loaded 3 installed node schemas')
    seed = page.locator('#wb-inputs').get_by_label('seed', exact=True)
    seed.fill('42')
    seed.blur()
    expect(page.locator('#wb-dirty')).to_have_text('Unsaved changes')
    page.locator('#wb-run').click()
    expect(page.locator('#wb-status')).to_contain_text('Save the current draft')
    assert not received
    page.locator('#wb-notes').fill('A different seed')
    page.locator('#wb-save').click()
    expect(page.locator('#wb-dirty')).to_have_text('Version 2')
    expect(page.locator('#wb-version option')).to_have_count(2)
    page.locator('#wb-validate').click()
    expect(page.locator('#wb-status')).to_contain_text('Schema checks passed')
    page.locator('#wb-run').click()
    expect(page.locator('#wb-status')).to_contain_text('Queued this saved version')
    assert received[0]['prompt']['1']['inputs']['seed'] == 42
    assert 'extra_data' not in received[0], 'API edits must not claim the native widget graph was updated'
    page.get_by_text('Run history & outputs', exact=True).click()
    page.locator('#wb-runs').get_by_role('button', name='Check status').click()
    expect(page.locator('#wb-runs')).to_contain_text('done · 1 output files')
    page.locator('#wb-runs').get_by_role('button', name='Keep outputs').click()
    expect(page.locator('#wb-runs')).to_contain_text('1 assets in the library')
    page.locator('#comfy-connect').click()
    expect(page.locator('#comfy-frame')).to_be_visible()
    page.screenshot(animations='disabled', path='/tmp/omote-workbench-desktop.png')
    page.reload()
    workbench(page)
    page.locator('#wb-list .wb-saved').click()
    expect(page.locator('#wb-dirty')).to_have_text('Version 2')
    page.locator('#wb-version').select_option('1')
    expect(page.locator('#wb-dirty')).to_have_text('Version 1')
    with page.expect_download() as download:
        page.locator('#wb-export').click()
    assert json.loads(Path(download.value.path()).read_text()) == GRAPH
    library(page)
    expect(page.locator('#creative-grid .asset-card')).to_have_count(1)
    page.locator('#creative-grid .asset-card').click()
    expect(page.locator('#asset-meta')).to_contain_text('"source": "comfyui"')
    expect(page.locator('#asset-meta')).to_contain_text('"revision": 2')
    stored = page.request.get(url + '/api/creative/assets').json()['assets'][0]
    assert stored['project'] == '桜の物語'


def test_workbench_conflict_preserves_local_draft_and_mobile_controls(creative_workspace):
    page, url, _ = creative_workspace
    expect = playwright.expect
    workbench(page)
    import_workflow(page)
    saved = page.request.get(url + '/api/media/comfyui/workflows').json()['workflows'][0]
    response = page.request.post(url + f"/api/media/comfyui/workflows/{saved['id']}/versions", data={
        'name': 'Another editor saved', 'graph': GRAPH, 'prompt': PROMPT, 'base_revision': 1,
    })
    assert response.ok
    page.locator('#wb-name').fill('My unsaved correction')
    page.locator('#wb-save').click()
    expect(page.locator('#wb-status')).to_contain_text('This workflow changed')
    expect(page.locator('#wb-name')).to_have_value('My unsaved correction')
    expect(page.locator('#wb-dirty')).to_have_text('Unsaved changes')
    page.locator('#wb-list .wb-saved').click()
    expect(page.locator('#wb-name')).to_have_value('Another editor saved')
    expect(page.locator('#wb-dirty')).to_have_text('Version 2')
    page.set_viewport_size({'width': 390, 'height': 844})
    panel = page.locator('#comfy-tools').bounding_box()
    assert panel['width'] <= 390
    page.screenshot(animations='disabled', path='/tmp/omote-workbench-mobile.png')
    library(page)
    upload(page)
    page.locator('#character-new').click()
    expect(page.locator('#character-dialog')).to_be_visible()
    box = page.locator('#character-dialog').bounding_box()
    page.screenshot(animations='disabled', path='/tmp/omote-character-mobile.png')
    assert 0 <= box['x'] and box['x'] + box['width'] <= 390
    page.locator('#character-form [name=name]').fill('モバイルの旅人')
    page.locator('#character-form').get_by_role('button', name='Save character', exact=True).click()
    expect(page.locator('#character-dialog')).to_be_hidden()
    expect(page.locator('#character-list')).to_contain_text('モバイルの旅人')
    page.locator('#creative-grid .asset-card').click()
    expect(page.locator('#asset-dialog')).to_be_visible()
    asset_box = page.locator('#asset-dialog').bounding_box()
    assert 0 <= asset_box['x'] and asset_box['x'] + asset_box['width'] <= 390
    page.locator('#asset-close').click()



def test_mobile_navigation_exposes_chat_studio_memory_and_settings(creative_workspace):
    page, _, _ = creative_workspace
    expect = playwright.expect
    page.set_viewport_size({'width': 390, 'height': 844})
    toggle = page.get_by_role('button', name='Open navigation', exact=True)
    expect(toggle).to_be_visible()
    expect(page.locator('#sidebar')).to_be_hidden()
    toggle.click()
    expect(page.get_by_role('dialog', name='Workspace navigation')).to_be_visible()
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    assert page.locator('main').evaluate('(node) => node.inert')
    expect(page.locator('#sidebar-close')).to_be_focused()
    # Tab stays in the navigation drawer; the workspace is explicitly inert.
    page.locator('#sidebar-close').focus()
    page.keyboard.press('Shift+Tab')
    expect(page.locator('#open-settings')).to_be_focused()
    page.keyboard.press('Tab')
    expect(page.locator('#sidebar-close')).to_be_focused()
    page.screenshot(animations='disabled', path='/tmp/omote-mobile-navigation.png')
    page.locator('[data-mode="studio"]').click()
    expect(page.locator('#pane-studio')).to_be_visible()
    expect(page.locator('#sidebar')).to_be_hidden()
    expect(toggle).to_have_attribute('aria-expanded', 'false')
    assert not page.locator('main').evaluate('(node) => node.inert')
    expect(page.locator('#modes [aria-current="true"]')).to_have_count(1)
    expect(page.locator('[data-mode="chat"]')).to_have_attribute('aria-current', 'false')
    toggle.click()
    page.locator('[data-mode="chat"]').click()
    expect(page.locator('body')).to_have_class(re.compile('mode-chat'))
    expect(page.locator('#modes [aria-current="true"]')).to_have_count(1)
    toggle.click()
    page.locator('#open-memory').click()
    expect(page.locator('#memory-dialog')).to_be_visible()
    expect(page.locator('#sidebar')).to_be_hidden()
    memory = page.locator('#memory-dialog').bounding_box()
    assert memory['x'] >= 0 and memory['x'] + memory['width'] <= 390
    page.locator('#mem-close').click()
    expect(toggle).to_be_focused()
    toggle.click()
    page.locator('#open-settings').click()
    expect(page.locator('#settings-dialog')).to_be_visible()
    settings = page.locator('#settings-dialog').bounding_box()
    assert settings['x'] >= 0 and settings['x'] + settings['width'] <= 390
    page.locator('#settings-close').click()
    expect(toggle).to_be_focused()
    toggle.click()
    page.keyboard.press('Escape')
    expect(page.locator('#sidebar')).to_be_hidden()
    expect(toggle).to_be_focused()
    toggle.click()
    page.locator('#sidebar-backdrop').click(position={'x': 370, 'y': 50})
    expect(page.locator('#sidebar')).to_be_hidden()
    toggle.click()
    page.set_viewport_size({'width': 1440, 'height': 1050})
    expect(page.locator('#sidebar')).to_be_visible()
    expect(toggle).to_be_hidden()
    page.wait_for_function('!document.querySelector("main").inert')
    assert not page.locator('main').evaluate('(node) => node.inert')
    assert not page.locator('#sidebar').evaluate('(node) => node.inert')


def test_invalid_workflow_import_does_not_destroy_saved_draft(creative_workspace):
    page, _, _ = creative_workspace
    expect = playwright.expect
    workbench(page)
    import_workflow(page)
    for invalid in [
        {'graph': {'nodes': 'broken'}, 'prompt': PROMPT},
        {'1': {'class_type': 'Source'}},
        {'graph': {'nodes': []}, 'prompt': {'1': {'class_type': 'Source', 'inputs': []}}},
    ]:
        page.locator('#wb-import').set_input_files({
            'name': 'invalid.json', 'mimeType': 'application/json', 'buffer': json.dumps(invalid).encode(),
        })
        expect(page.locator('#wb-status')).to_have_text(re.compile(r'(invalid|must|needs|array|object|required)', re.I))
        expect(page.locator('#wb-name')).to_have_value('京都のキャラクター')
        expect(page.locator('#wb-dirty')).to_have_text('Version 1')
        expect(page.locator('#wb-node-count')).to_have_text('2 executable nodes')
        expect(page.locator('#wb-inputs').get_by_label('seed', exact=True)).to_have_value('12')
        expect(page.locator('#wb-version')).to_have_value('1')
    with page.expect_download() as download:
        page.locator('#wb-export').click()
    assert json.loads(Path(download.value.path()).read_text()) == GRAPH
