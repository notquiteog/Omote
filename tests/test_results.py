"""Saved results survive restarts without executing work or broadening file access."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from omote.agent.manager import manager
from omote.media.store import MediaStore
from omote.results import PREVIEW_CHARS, chat_files, chat_preview, last_reply, linked_files, list_results
from omote.routers import media, schedules
from omote.scheduling import Schedule, ScheduleStore
from omote.sessions import SessionStore, Stored


def save_chat(store, root, ident='report', text='A useful saved answer', unfinished=False):
    store.save(Stored(id=ident, title=ident, root=str(root), unfinished=unfinished, messages=[
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Make a report'}]},
        {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]},
    ]))


def test_results_survive_restart_and_exclude_active_chats_and_uploads(tmp_path):
    chats = SessionStore(tmp_path / 'sessions')
    media_store = MediaStore(tmp_path / 'media')
    save_chat(chats, tmp_path)
    save_chat(chats, tmp_path, 'active')
    save_chat(chats, tmp_path, 'interrupted', unfinished=True)
    media_store.add(b'generated', kind='image', media_type='image/png', prompt='Portrait')
    media_store.add(b'private reference', kind='image', media_type='image/png', source='upload')
    data = list_results(SessionStore(chats.root), MediaStore(media_store.root), [], live_ids={'active'})
    assert data['total'] == 3
    assert {item['kind'] for item in data['results']} == {'chat', 'media'}
    interrupted = next(item for item in data['results'] if item['id'] == 'chat-interrupted')
    assert interrupted['status'] == 'interrupted' and not interrupted['summary']
    assert not any('private reference' in str(item) or item['id'] == 'chat-active' for item in data['results'])
    assert chats.load('interrupted').unfinished  # Listing never marks work complete.


def test_reply_preview_is_bounded_and_never_exposes_thinking_or_tool_text(tmp_path):
    store = SessionStore(tmp_path)
    save_chat(store, tmp_path, text='x' * 10000)
    assert len(last_reply(store, 'report')) == PREVIEW_CHARS
    with store.path_for('report').open('a') as stream:
        stream.write(json.dumps({'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'thinking', 'text': 'private reasoning'},
            {'type': 'tool_use', 'name': 'read_file', 'input': {'path': 'private'}}]}) + '\n')
    assert last_reply(store, 'report') == ''
    assert 'private reasoning' not in str(chat_preview(store, 'report'))
    with store.path_for('report').open('a') as stream:
        stream.write('{"type":"message","role":"assistant","content":42}\n')
    assert last_reply(store, 'report') == ''


def test_tail_preview_skips_large_older_records(tmp_path):
    store = SessionStore(tmp_path)
    save_chat(store, tmp_path, text='old text ' * 20000)
    with store.path_for('report').open('a') as stream:
        stream.write(json.dumps({'type': 'message', 'role': 'assistant',
                                 'content': [{'type': 'text', 'text': 'Latest small reply'}]}) + '\n')
    assert last_reply(store, 'report') == 'Latest small reply'
    assert chat_preview(store, 'report')['messages'] == [{'role': 'assistant', 'text': 'Latest small reply'}]


def test_search_pagination_and_scheduled_chat_deduplication(tmp_path):
    chats = SessionStore(tmp_path / 'sessions')
    runs = ScheduleStore(tmp_path / 'schedules.db')
    try:
        for n in range(24):
            save_chat(chats, tmp_path, str(n), text=f'Useful result {n}')
        save_chat(chats, tmp_path, 'scheduled')
        task = runs.save(Schedule(name='Daily report', prompt='Report', root=str(tmp_path), cadence='manual'))
        run = runs.claim(task['id'], manual=True)
        runs.update_run(run['id'], status='completed', summary='Scheduled findings', session_id='scheduled')
        first = list_results(chats, None, runs.runs(), limit=20)
        second = list_results(chats, None, runs.runs(), offset=20, limit=20)
        assert first['total'] == 25 and first['has_more'] and not second['has_more']
        assert not {item['id'] for item in first['results']} & {item['id'] for item in second['results']}
        filtered = list_results(chats, None, runs.runs(), q='scheduled findings')
        assert filtered['total'] == 1 and filtered['results'][0]['kind'] == 'run'
    finally:
        runs.close()


def test_linked_files_are_explicit_existing_files_inside_the_original_project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'my report.txt').write_text('report')
    outside = tmp_path / 'secret.txt'
    outside.write_text('outside')
    text = '[Report](my%20report.txt) [Absolute](<' + str(root / 'my report.txt') + '>) ' \
           '[Escape](../secret.txt) [URL](https://example.com/private) [Missing](missing.txt)'
    files = linked_files(text, str(root))
    assert len(files) == 1 and files[0]['name'] == 'my report.txt'
    try:
        (root / 'symlink.txt').symlink_to(outside)
    except OSError:
        return  # Windows without symlink privileges still checks traversal above.
    assert linked_files('[Linked](symlink.txt)', str(root)) == []


@pytest.fixture
def result_api(tmp_path, monkeypatch):
    chats = SessionStore(tmp_path / 'sessions')
    library = MediaStore(tmp_path / 'media')
    runs = ScheduleStore(tmp_path / 'schedules.db')
    monkeypatch.setattr(manager, 'store', lambda: chats)
    monkeypatch.setattr(manager, 'stored', lambda **kwargs: chats.list(**kwargs))
    monkeypatch.setattr(manager, 'list', lambda: [])
    monkeypatch.setattr(schedules, 'service', SimpleNamespace(store=runs))
    monkeypatch.setattr(media, 'service', SimpleNamespace(store=library, jobs={}))
    app = FastAPI()
    app.include_router(schedules.router)
    yield app, chats, library, runs
    runs.close()


async def test_download_revalidates_link_and_recovery_does_not_resume(result_api, tmp_path):
    app, chats, _, _ = result_api
    project = tmp_path / 'project'
    project.mkdir()
    report = project / 'report.txt'
    report.write_text('only this project')
    save_chat(chats, project, text='[Report](report.txt)')
    save_chat(chats, project, 'stopped', unfinished=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        data = (await client.get('/api/schedules/results')).json()
        item = next(item for item in data['results'] if item['id'] == 'chat-report')
        file_url = item['files'][0]['url']
        downloaded = await client.get(file_url)
        assert downloaded.text == 'only this project'
        assert 'attachment' in downloaded.headers['content-disposition']
        assert downloaded.headers['x-content-type-options'] == 'nosniff'
        activity = (await client.get('/api/schedules')).json()['activity']
        assert activity[0]['id'] == 'chat-stopped' and activity[0]['status'] == 'interrupted'
        preview = (await client.get('/api/schedules/results/chats/stopped')).json()
        assert preview['messages'][0]['text'] == 'Make a report'
        report.unlink()
        assert (await client.get(file_url)).status_code == 404
        assert (await client.get('/api/schedules/results?limit=9999')).status_code == 422
    assert chat_files(chats, '../report') == []


def test_deleted_records_are_not_kept_as_duplicate_inbox_data(tmp_path):
    chats = SessionStore(tmp_path / 'sessions')
    media_store = MediaStore(tmp_path / 'media')
    save_chat(chats, tmp_path)
    item = media_store.add(b'file', kind='video', media_type='video/mp4')
    chats.delete('report')
    media_store.remove(item.id)
    assert list_results(chats, media_store, [])['total'] == 0
