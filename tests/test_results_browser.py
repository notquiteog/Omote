"""Browse and download real saved work without configuring an AI provider."""
from pathlib import Path

import pytest

from omote.media.store import MediaStore
from omote.sessions import SessionStore
from tests.test_results import save_chat
from tests.test_workspace_browser import workspace as workspace

playwright = pytest.importorskip('playwright.sync_api')
expect = playwright.expect


def test_saved_results_download_search_and_recovery_without_a_model(workspace, tmp_path):
    page, url = workspace
    store = SessionStore(tmp_path / 'sessions')
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'report.txt').write_text('A finished project report')
    save_chat(store, project, text='The report is ready. [Download](report.txt) <img src=x onerror=alert(1)>')
    save_chat(store, project, 'stopped', unfinished=True)
    library = MediaStore(tmp_path / 'media')
    library.add(b'example media file', kind='video', media_type='video/mp4', prompt='Saved landscape film')
    requests = []
    page.on('request', lambda request: requests.append(request.method + ' ' + request.url))
    page.locator('#open-schedules').click()
    expect(page.locator('#task-count-attention')).to_have_text('1')
    page.locator('[data-task-filter="finished"]').click()
    expect(page.locator('.task-result')).to_have_count(3)
    report = page.locator('.task-result').filter(has_text='report.txt')
    report.locator('summary').click()
    expect(report).to_contain_text('<img src=x onerror=alert(1)>')
    assert page.locator('#task-results img').count() == 0
    with page.expect_download() as saved:
        report.get_by_role('link', name='report.txt').click()
    assert Path(saved.value.path()).read_text() == 'A finished project report'
    interrupted = page.locator('[data-result-id="chat-stopped"]')
    interrupted.get_by_text('Review saved conversation', exact=True).click()
    expect(interrupted.locator('.task-chat-preview')).to_contain_text('Make a report')
    with page.expect_download() as saved_chat:
        interrupted.get_by_role('link', name='Save conversation').click()
    assert 'stopped' in Path(saved_chat.value.path()).read_text()
    assert not [request for request in requests if request.startswith('POST ')]
    assert page.request.get(url + '/api/sessions').json()['sessions'] == []
    page.locator('#task-results-search').fill('landscape')
    expect(page.locator('.task-result')).to_have_count(1)
    expect(page.locator('.task-result')).to_contain_text('Saved landscape film')
    page.locator('#task-results-search').fill('missing result')
    expect(page.locator('#task-results-list')).to_contain_text('No matching results')
    page.locator('[data-task-filter="attention"]').click()
    page.get_by_role('button', name='Review saved work', exact=True).click()
    expect(page.locator('.task-result')).to_have_count(1)
    expect(page.locator('#task-results-search')).to_have_value('stopped')


def test_closed_inbox_ignores_late_results(workspace):
    page, _ = workspace
    held = []
    page.route('**/api/schedules/results?*', lambda route: held.append(route))
    page.locator('#open-schedules').click()
    page.locator('[data-task-filter="finished"]').click()
    expect(page.locator('#task-results-status')).to_have_text('Finding saved work…')
    page.keyboard.press('Escape')
    assert held
    held[0].fulfill(json={'results': [dict(id='late', title='Stale result')], 'total': 1, 'has_more': False, 'limited': False})
    page.unroute('**/api/schedules/results?*')
    page.locator('#open-schedules').click()
    expect(page.locator('#task-results-status')).to_have_text('0 of 0 saved results')
    expect(page.locator('#task-results-list')).not_to_contain_text('Stale result')


def test_results_are_lazy_paginated_and_do_not_poll_while_reading(workspace, tmp_path):
    page, _ = workspace
    store = SessionStore(tmp_path / 'sessions')
    for n in range(24):
        save_chat(store, tmp_path, str(n), text=f'Result {n}')
    calls = []
    page.on('request', lambda request: calls.append(request.url) if '/schedules/results?' in request.url else None)
    page.locator('#open-schedules').click()
    assert not calls
    page.locator('[data-task-filter="finished"]').click()
    expect(page.locator('.task-result')).to_have_count(20)
    page.locator('#task-results-more').click()
    expect(page.locator('.task-result')).to_have_count(24)
    expect(page.locator('#task-results-more')).to_be_hidden()
    page.locator('.task-result summary').first.click()
    page.wait_for_timeout(5200)
    assert len(calls) == 2
    expect(page.locator('.task-result details').first).to_have_attribute('open', '')
    page.keyboard.press('Escape')
    page.locator('#open-schedules').click()
    expect(page.locator('.task-result')).to_have_count(20)
    assert len(calls) == 3


def test_pagination_advances_when_new_work_shifts_a_page(workspace):
    page, _ = workspace
    offsets = []

    def results(route):
        from urllib.parse import parse_qs, urlparse

        offset = int(parse_qs(urlparse(route.request.url).query)['offset'][0])
        offsets.append(offset)
        # A newer result shifts the second page; the repeated row stays hidden,
        # but fetching must still advance past both rows returned by the server.
        ids = {0: ['a', 'b'], 2: ['b', 'c'], 4: ['d']}[offset]
        route.fulfill(json={'results': [dict(id=ident, kind='chat', title=ident, status='saved',
                                            created=1, summary='Saved reply', files=[]) for ident in ids],
                            'total': 5, 'has_more': offset < 4, 'limited': False})

    page.route('**/api/schedules/results?*', results)
    page.locator('#open-schedules').click()
    page.locator('[data-task-filter="finished"]').click()
    expect(page.locator('.task-result')).to_have_count(2)
    page.locator('#task-results-more').click()
    expect(page.locator('.task-result')).to_have_count(3)
    page.locator('#task-results-more').click()
    expect(page.locator('.task-result')).to_have_count(4)
    expect(page.locator('#task-results-more')).to_be_hidden()
    assert offsets == [0, 2, 4]


@pytest.mark.parametrize('width', [320, 390])
def test_results_fit_on_phone(workspace, tmp_path, width):
    page, _ = workspace
    save_chat(SessionStore(tmp_path / 'sessions'), tmp_path, text='Long result ' * 400)
    page.set_viewport_size({'width': width, 'height': 700})
    page.locator('#sidebar-toggle').click()
    page.locator('#open-schedules').click()
    page.locator('[data-task-filter="finished"]').click()
    expect(page.locator('.task-result')).to_have_count(1)
    page.locator('.task-result summary').click()
    assert page.locator('#schedules-dialog').evaluate('e => e.scrollWidth <= e.clientWidth')
    page.keyboard.press('Escape')
    expect(page.locator('#schedules-dialog')).to_be_hidden()
