"""Read saved work on demand. Listing a result never resumes an agent.

Existing transcripts, run records and media sidecars remain the source of truth.
Reads are bounded; previews are plain text, and file links are resolved only inside
the originating chat's project. No arbitrary path or model-provided URL is served.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote

SOURCE_LIMIT = 500
TAIL_BYTES = 64 * 1024
PREVIEW_CHARS = 6000
_LINK = re.compile(r'!?\[[^\]\n]*\]\((<[^>\n]+>|[^\s)]+)\)')
_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


def _tail(store, session_id: str) -> list[dict]:
    if not _ID.fullmatch(session_id):
        return []
    try:
        with store.path_for(session_id).open('rb') as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - TAIL_BYTES))
            lines = stream.read(TAIL_BYTES).splitlines()
            if size > TAIL_BYTES:
                lines = lines[1:]  # The first record may begin outside our window.
        records = []
        for line in lines:
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(record, dict) and record.get('type') == 'message':
                records.append(record)
        return records
    except (OSError, ValueError):
        return []


def last_reply(store, session_id: str) -> str:
    records = _tail(store, session_id)
    if records:
        record = records[-1]
        blocks = record.get('content') or []
        if not isinstance(blocks, list) or record.get('role') != 'assistant' or any(
            isinstance(block, dict) and block.get('type') == 'tool_use' for block in blocks
        ):
            return ''
        return '\n\n'.join(str(block.get('text') or '') for block in blocks
                           if isinstance(block, dict) and block.get('type') == 'text')[:PREVIEW_CHARS]
    return ''


def chat_preview(store, session_id: str) -> dict:
    messages = []
    for record in _tail(store, session_id):
        blocks = record.get('content')
        if not isinstance(blocks, list) or record.get('role') not in ('user', 'assistant'):
            continue
        text = '\n\n'.join(str(block.get('text') or '') for block in blocks
                           if isinstance(block, dict) and block.get('type') == 'text')[:2000]
        if text:
            messages.append({'role': record['role'], 'text': text})
    return {'messages': messages[-12:]}


def linked_files(text: str, root: str) -> list[dict]:
    """Existing project files explicitly linked in a reply, with opaque IDs."""
    if not root:
        return []
    try:
        base = Path(root).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return []
    files = {}
    for match in _LINK.finditer(text[:PREVIEW_CHARS]):
        raw = unquote(match[1].strip('<>'))
        # Reject URLs and UNC/device paths before interpreting this as a path.
        if '://' in raw or raw.startswith(('//', '\\')) or '\x00' in raw:
            continue
        try:
            path = Path(raw)
            resolved = (path if path.is_absolute() else base / path).resolve(strict=True)
            if not resolved.is_relative_to(base) or not resolved.is_file():
                continue
            relative = resolved.relative_to(base).as_posix()
            ident = hashlib.sha256(relative.encode()).hexdigest()
            files[ident] = {'id': ident, 'name': resolved.name, 'bytes': resolved.stat().st_size,
                            'path': resolved}
        except (OSError, ValueError, RuntimeError):
            continue
        if len(files) >= 8:
            break
    return list(files.values())


def chat_files(store, session_id: str) -> list[dict]:
    if not store or not _ID.fullmatch(session_id):
        return []
    # Load only this chat to verify its own root; no global path is accepted.
    saved = store.load(session_id)
    return linked_files(last_reply(store, session_id), saved.root) if saved else []


def list_results(session_store, media_store, runs, *, live_ids=(), q='', offset=0, limit=20):
    items = []
    scheduled_ids = {run['session_id'] for run in runs if run.get('session_id')}
    for run in runs:
        if run['status'] in ('starting', 'running', 'waiting'):
            continue
        items.append({'id': 'run-' + run['id'], 'kind': 'run', 'title': run['snapshot']['name'],
                      'status': run['status'], 'summary': run['summary'][:PREVIEW_CHARS],
                      'error': run['error'], 'created': run['finished'] or run['started'],
                      'session_id': run['session_id'], 'files': []})
    stored = session_store.list(limit=SOURCE_LIMIT) if session_store else []
    for chat in stored:
        sid = str(chat.get('id') or '')
        if sid in scheduled_ids or sid in live_ids or not _ID.fullmatch(sid):
            continue
        if not session_store.path_for(sid).is_file():
            continue
        interrupted = bool(chat.get('unfinished'))
        reply = '' if interrupted else last_reply(session_store, sid)
        if not interrupted and not reply:
            continue
        files = linked_files(reply, chat.get('root', ''))
        items.append({'id': 'chat-' + sid, 'kind': 'chat', 'title': chat.get('title') or 'Untitled chat',
                      'status': 'interrupted' if interrupted else 'saved', 'summary': reply,
                      'error': 'Omote stopped while this chat was working. Review its saved conversation before continuing.' if interrupted else '',
                      'created': chat.get('updated') or 0, 'session_id': sid,
                      'files': [{'name': f['name'], 'bytes': f['bytes'],
                                 'url': f'/api/schedules/results/chats/{sid}/files/{f["id"]}'} for f in files]})
    media = media_store.list(limit=SOURCE_LIMIT) if media_store else []
    for item in media:
        if not media_store.path(item.id):
            continue
        items.append({'id': 'file-' + item.id, 'kind': 'media', 'title': item.prompt[:160] or 'Studio creation',
                      'status': 'saved', 'summary': item.notes[:PREVIEW_CHARS], 'error': '',
                      'created': item.created_at, 'session_id': '',
                      'files': [{'name': item.filename, 'bytes': item.bytes,
                                 'url': f'/api/media/{item.id}/file'}]})
    terms = q.casefold().split()
    items = [item for item in items if all(term in ' '.join((item['title'], item['summary'], item['error'],
             ' '.join(f['name'] for f in item['files']))).casefold() for term in terms)]
    items.sort(key=lambda item: (-item['created'], item['id']))
    return {'results': items[offset:offset + limit], 'total': len(items),
            'has_more': len(items) > offset + limit,
            'limited': len(stored) >= SOURCE_LIMIT or len(media) >= SOURCE_LIMIT or len(runs) >= SOURCE_LIMIT}
