"""The real Settings wiring must reach the Tauri 2 bridge and preserve failures."""

from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace

expect = playwright.expect


def update_state():
    return {
        'current': '0.8.1',
        'available': True,
        'platform': 'linux-x64',
        'managed': True,
        'enabled': True,
        'downloading': False,
        'staged': '',
        'error': '',
        'latest': {'version': '0.10.0', 'asset': 'Omote_0.10.0_amd64.deb', 'notes': 'Update fixes'},
    }


def open_updates(page):
    page.locator('#open-settings').click()
    page.locator('#tab-updates').click()


def test_tauri2_download_then_install_and_errors_remain_visible(workspace):
    page, _ = workspace
    current = update_state()
    page.add_init_script("""window.updateCalls=[]; window.__TAURI__={core:{invoke:async(command,args)=>{
      window.updateCalls.push({command,args}); throw new Error('System authentication was canceled');
    }}};""")
    page.route('**/api/update/status', lambda route: route.fulfill(json=current))
    page.route('**/api/update/check', lambda route: route.fulfill(json=current))

    def download(route):
        current.update(staged='/tmp/updates/Omote_0.10.0_amd64.deb', progress=100, downloaded=12345)
        route.fulfill(json={'path': current['staged'], 'version': '0.10.0', 'bytes': 12345, 'verified': True})

    page.route('**/api/update/apply', download)
    page.reload()
    open_updates(page)
    expect(page.locator('#update-apply')).to_have_text('Download')
    page.locator('#update-apply').click()
    expect(page.locator('#update-apply')).to_have_text('Install')
    assert page.evaluate('window.updateCalls') == [], 'Downloading must not invoke an installer.'
    page.locator('#update-apply').click()
    expect(page.locator('#update-state')).to_contain_text('System authentication was canceled')
    assert page.evaluate('window.updateCalls')[0] == {'command': 'apply_update', 'args': {'path': current['staged']}}
    expect(page.locator('#update-apply')).to_be_enabled()
    page.wait_for_timeout(1400)
    expect(page.locator('#update-state')).to_contain_text('System authentication was canceled')


def test_download_failure_is_not_erased_by_the_next_status_poll(workspace):
    page, _ = workspace
    current = update_state()
    page.route('**/api/update/status', lambda route: route.fulfill(json=current))
    page.route(
        '**/api/update/apply',
        lambda route: route.fulfill(status=502, json={'detail': 'Checksum mismatch; file was deleted.'}),
    )
    page.reload()
    open_updates(page)
    page.locator('#update-apply').click()
    expect(page.locator('#update-state')).to_contain_text('Checksum mismatch')
    expect(page.locator('#update-apply')).to_be_enabled()
    page.wait_for_timeout(1400)
    expect(page.locator('#update-state')).to_contain_text('Checksum mismatch')


def test_completed_install_or_native_failure_survives_app_restart(workspace):
    page, _ = workspace
    current = update_state()
    current['installation'] = {'status': 'error', 'message': 'The package installer was canceled.'}
    page.route('**/api/update/status', lambda route: route.fulfill(json=current))
    page.reload()
    open_updates(page)
    expect(page.locator('#update-state')).to_contain_text('package installer was canceled')


def test_restored_verified_download_offers_install_without_downloading_again(workspace):
    page, _ = workspace
    current = update_state()
    current.update(staged='/tmp/updates/Omote_0.10.0_amd64.deb', progress=100, downloaded=12345)
    current['installation'] = {'status': 'error', 'message': 'The package installer was canceled.'}
    downloads = []
    page.add_init_script("""window.updateCalls=[]; window.__TAURI__={core:{invoke:async(command,args)=>{
      window.updateCalls.push({command,args}); throw new Error('Test install handoff reached');
    }}};""")
    page.route('**/api/update/status', lambda route: route.fulfill(json=current))

    def download(route):
        downloads.append(True)
        route.fulfill(status=500, json={'detail': 'Unnecessary download'})

    page.route('**/api/update/apply', download)
    page.reload()
    open_updates(page)
    expect(page.locator('#update-apply')).to_have_text('Install')
    expect(page.locator('#update-state')).to_contain_text('package installer was canceled')
    page.locator('#update-apply').click()
    expect(page.locator('#update-state')).to_contain_text('Test install handoff reached')
    assert page.evaluate('window.updateCalls') == [{'command': 'apply_update', 'args': {'path': current['staged']}}]
    assert downloads == []
