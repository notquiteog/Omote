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
    page.locator('.memory-index > summary').click()
    page.locator('#mem-connections').click()
    expect(page.locator('#settings-dialog')).to_be_visible()
    expect(page.locator('#panel-connections')).to_be_visible()


def system_sample(gpu=True):
    return {'sampled_at': time.time(), 'cpu': {'percent': 23.5, 'logical_cores': 12},
            'memory': {'percent': 37.5, 'total_bytes': 32 * 1024**3, 'available_bytes': 20 * 1024**3},
            'disk': {'percent': 60., 'total_bytes': 100 * 1024**3, 'free_bytes': 40 * 1024**3},
            'gpu': {'devices': [{'name': 'Test GPU <safe text>', 'utilization_percent': 12.,
                                'memory_used_bytes': 1024**3, 'memory_total_bytes': 16 * 1024**3,
                                'temperature_c': 35., 'power_watts': 12.}] if gpu else [],
                    'note': '' if gpu else 'GPU driver is unavailable.'},
            'runtimes': [{'name': 'FFmpeg', 'status': 'Command available'}],
            'notes': [], 'work': [], 'work_count': 0}


def test_system_monitor_only_polls_while_open_and_can_pause(workspace):
    page, _ = workspace
    expect = playwright.expect
    requests = []

    def usage(route):
        requests.append(time.monotonic())
        route.fulfill(json=system_sample())

    page.route('**/api/system/usage', usage)
    assert not requests
    page.get_by_role('button', name='System', exact=True).click()
    expect(page.locator('#system-metrics article')).to_have_count(4)
    expect(page.locator('#system-metrics')).to_contain_text('20.0 GiB available')
    expect(page.locator('#system-metrics')).to_contain_text('Test GPU <safe text>')
    assert page.locator('#system-metrics safe').count() == 0
    page.get_by_role('button', name='Pause updates').click()
    before = len(requests)
    page.wait_for_timeout(2800)
    assert len(requests) == before
    page.get_by_role('button', name='Refresh', exact=True).click()
    expect(page.locator('#system-status')).to_contain_text('Paused')
    assert len(requests) == before + 1
    page.locator('#system-close').click()
    page.wait_for_timeout(2800)
    assert len(requests) == before + 1


def test_mobile_system_monitor_and_setup_draft(workspace):
    page, _ = workspace
    expect = playwright.expect
    page.set_viewport_size({'width': 390, 'height': 844})
    page.route('**/api/system/usage', lambda route: route.fulfill(json=system_sample(gpu=False)))
    page.locator('#sidebar-toggle').click()
    page.locator('#open-system').click()
    expect(page.locator('#system-dialog')).to_be_visible()
    expect(page.locator('#system-metrics')).to_contain_text('GPU driver is unavailable.')
    assert not page.locator('body').evaluate("node => node.classList.contains('navigation-open')")
    assert page.locator('#system-dialog').bounding_box()['width'] <= 390 - 28
    assert page.locator('#system-dialog').evaluate('node => node.scrollWidth <= node.clientWidth')
    page.locator('#system-plan').click()
    expect(page.locator('#system-dialog')).not_to_be_visible()
    expect(page.locator('#input')).to_have_value(__import__('re').compile('.*check my system resources.*'))
    # The action drafts a request; it does not submit or install on a click.
    expect(page.locator('#sessions > li[aria-current]')).to_have_count(0)


def test_brain_search_scope_and_offline_creation(workspace):
    page, _ = workspace
    expect = playwright.expect
    page.get_by_role('button', name='Brain', exact=True).click()
    expect(page.get_by_role('heading', name='Brain', exact=True)).to_be_visible()
    page.locator('#mem-query').fill('Project detail 104')
    expect(page.locator('#mem-list > li')).to_have_count(1)
    expect(page.locator('#mem-results')).to_contain_text('1 of 1')
    expect(page.locator('#mem-more')).to_be_hidden()
    page.locator('#mem-query').fill('')
    page.locator('#mem-scope').select_option('personal')
    expect(page.locator('#mem-empty')).to_be_visible()
    expect(page.locator('#mem-filter-subject')).to_be_disabled()
    page.locator('#mem-enabled').check()
    page.locator('#mem-editor > summary').click()
    expect(page.locator('#mem-save')).to_be_enabled()
    page.locator('#mem-text').fill('I prefer short explanations')
    page.locator('#mem-kind').select_option('preference')
    page.locator('#mem-save').click()
    expect(page.locator('#mem-status')).to_contain_text('saved locally')
    expect(page.locator('#mem-list > li')).to_have_count(1)
    expect(page.locator('#mem-list')).to_contain_text('I prefer short explanations')
    # Pausing preserves search, corrections and deletion without a provider.
    page.locator('#mem-enabled').uncheck()
    expect(page.locator('#mem-count')).to_contain_text('paused')
    page.locator('#mem-list').get_by_role('button', name='Forget', exact=True).click()
    expect(page.locator('#mem-empty')).to_be_visible()
    page.set_viewport_size({'width': 390, 'height': 844})
    bounds = page.locator('#memory-dialog').bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
    assert page.locator('#memory-dialog').evaluate('(node) => node.scrollWidth <= node.clientWidth')


def test_graph_workspace_navigation_and_frame_preserves_work(workspace):
    page, url = workspace
    expect = playwright.expect
    page.locator('#start-studio').click()
    page.locator('#studio-graph').click()
    expect(page.locator('#comfy-workspace')).to_be_visible()
    expect(page.locator('#studio-generate')).to_be_hidden()
    expect(page.locator('#comfy-connect')).to_be_disabled()
    page.locator('.comfy-options > summary').click()
    page.locator('#comfy-settings').click()
    expect(page.locator('#settings-dialog')).to_be_visible()
    page.locator('#conn-host').select_option('comfyui')
    expect(page.locator('#conn-editor-url')).to_be_visible()
    page.locator('#conn-editor-url').fill('http://draft-editor.test')
    page.locator('#settings-close').click()
    page.locator('#comfy-settings').click()
    expect(page.locator('#conn-host')).to_have_value('comfyui')
    expect(page.locator('#conn-editor-url')).to_have_value('http://draft-editor.test')
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
    expect(page.locator('#comfy-tools')).to_be_hidden()
    frame = page.locator('#comfy-frame')
    assert frame.bounding_box()['height'] > 500
    page.locator('#comfy-focus').click()
    expect(page.locator('#comfy-focus')).to_have_text('Exit focus')
    assert frame.bounding_box()['width'] > 1300
    page.locator('#comfy-library-toggle').click()
    expect(page.locator('#comfy-tools')).to_be_visible()
    expect(draft).to_have_value('unsaved workflow')
    page.locator('#wb-collapse').click()
    expect(page.locator('#comfy-library-toggle')).to_have_attribute('aria-expanded', 'false')
    page.keyboard.press('Escape')
    expect(page.locator('#comfy-focus')).to_have_text('Focus mode')
    page.locator('#studio-simple').click()
    expect(page.locator('#studio-generate')).to_be_visible()
    page.locator('#studio-graph').click()
    expect(draft).to_have_value('unsaved workflow')
    expect(page.locator('#comfy-external')).to_have_attribute('href', 'http://comfy.test/')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert frame.bounding_box()['height'] > 250
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    expect(draft).to_have_value('unsaved workflow')


def test_studio_examples_fill_a_draft_without_starting_generation(workspace):
    page, _ = workspace
    page.locator('#start-studio').click()
    expect = playwright.expect
    expect(page.get_by_role('heading', name='Your creations start here')).to_be_visible()
    page.get_by_role('button', name='Product photo', exact=True).click()
    expect(page.locator('#studio-prompt')).to_have_value('A studio product photograph of a handmade ceramic cup, soft window light, warm neutral background')
    expect(page.locator('#studio-jobs')).to_be_hidden()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#studio-prompt').focus()
    expect(page.locator('#studio-prompt')).to_be_in_viewport(ratio=1)
    assert page.locator('#studio-prompt').bounding_box()['height'] >= 110
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_mobile_workspace_has_no_horizontal_overflow(workspace):
    page, _ = workspace
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.locator('#start-studio').click()
    page.locator('#studio-graph').click()
    playwright.expect(page.locator('#comfy-workspace')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
