"""Exercise real UI wiring against an isolated daemon; no model calls or keys."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from omote.memory.store import MemoryStore

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def workspace(tmp_path):
    store = MemoryStore(tmp_path / 'memory.db')
    for i in range(105):
        store.add('local', f'Project detail {i}', [1., .5, .25], kind='project', subject='demo', source='user')
    store.close()
    with socket.socket() as port_socket:
        port_socket.bind(('127.0.0.1', 0))
        port = port_socket.getsockname()[1]
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'SYSTEMROOT', 'TMPDIR') if key in os.environ}
    env.update(OMOTE_ENV=os.devnull, OMOTE_DATA_DIR=str(tmp_path), OMOTE_UPDATE_CHECK='false')
    process = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'omote.main:app', '--host', '127.0.0.1', '--port', str(port)],
        cwd=Path(__file__).resolve().parents[1], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                urllib.request.urlopen(f'{url}/healthz', timeout=1).close()
                break
            except urllib.error.URLError:
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail('isolated workspace daemon did not start')
                time.sleep(.05)
        with playwright.sync_playwright() as engine:
            try:
                browser = engine.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f'Chromium is unavailable: {exc}')
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, color_scheme='dark')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script("localStorage.setItem('omote.welcome.v1', 'done')")
            page.goto(url)
            yield page, url
            assert not errors, errors
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_paused_memory_editor_pagination_and_export(workspace):
    page, _ = workspace
    expect = playwright.expect
    page.locator('#open-memory').click()
    expect(page.locator('#mem-list > li')).to_have_count(100)
    expect(page.locator('#mem-save')).to_be_disabled()
    page.locator('#mem-more').click()
    expect(page.locator('#mem-list > li')).to_have_count(105)
    page.locator('#mem-list > li').first.get_by_role('button', name='Edit', exact=True).click()
    page.locator('#mem-text').fill('Corrected while paused')
    page.locator('#mem-save').click()
    expect(page.locator('#mem-status')).to_contain_text('saved locally')
    expect(page.locator('#mem-list')).to_contain_text('Corrected while paused')
    with page.expect_download() as downloaded:
        page.locator('#mem-export').click()
    exported = json.loads(Path(downloaded.value.path()).read_text())
    assert len(exported['memories']) == 105
    assert not exported['settings']['enabled']
    page.locator('#mem-connections').click()
    expect(page.locator('#settings-dialog')).to_be_visible()
    expect(page.locator('#panel-connections')).to_be_visible()


def test_graph_workspace_navigation_and_frame_preserves_work(workspace):
    page, url = workspace
    expect = playwright.expect
    page.locator('#start-studio').click()
    page.locator('#studio-graph').click()
    expect(page.locator('#comfy-workspace')).to_be_visible()
    expect(page.locator('#studio-generate')).to_be_hidden()
    expect(page.locator('#comfy-connect')).to_be_disabled()
    page.locator('#comfy-settings').click()
    expect(page.locator('#settings-dialog')).to_be_visible()
    page.locator('#conn-host').select_option('comfyui')
    expect(page.locator('#conn-editor-url')).to_be_visible()
    page.locator('#settings-close').click()
    # Stub only the external frontend. The real daemon discovers the connection
    # and the actual UI mounts and retains the frame through workspace switches.
    page.route('http://comfy.test/**', lambda route: route.fulfill(
        content_type='text/html', body='<label>Draft graph<input id="draft"></label>'))
    response = page.request.put(f'{url}/api/providers/connections', data={
        'id': 'comfy-test', 'adapter': 'comfyui', 'base_url': 'http://comfy.test', 'local': True,
    })
    assert response.ok
    page.locator('#comfy-refresh').click()
    expect(page.locator('#comfy-connect')).to_be_enabled()
    page.locator('#comfy-connect').click()
    draft = page.frame_locator('#comfy-frame').locator('#draft')
    draft.fill('unsaved workflow')
    page.locator('#studio-simple').click()
    expect(page.locator('#studio-generate')).to_be_visible()
    page.locator('#studio-graph').click()
    expect(draft).to_have_value('unsaved workflow')
    expect(page.locator('#comfy-external')).to_have_attribute('href', 'http://comfy.test/')


def test_mobile_workspace_has_no_horizontal_overflow(workspace):
    page, _ = workspace
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.locator('#start-studio').click()
    page.locator('#studio-graph').click()
    playwright.expect(page.locator('#comfy-workspace')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
