"""Manage local scheduled work through the same authenticated UI as chat."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from omote.scheduling import Schedule, Scheduler

router = APIRouter(prefix='/api/schedules')
service: Scheduler | None = None


def live() -> Scheduler:
    if service is None:
        raise HTTPException(503, 'Scheduled tasks are not ready. Restart Omote and try again.')
    return service


def validate_root(spec):
    root = Path(spec.root).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(400, 'Choose an existing project folder on the computer running Omote.')
    return spec.model_copy(update={'root': str(root)})


@router.get('')
async def list_schedules():
    from omote.agent.manager import manager
    from omote.config import config
    from omote.routers import media

    runs = live().store.runs()
    scheduled_chats = {run['session_id'] for run in runs if run['session_id']}
    # A read-only index: opening Tasks never merges conversation context or
    # resumes, retries, or grants permissions to any of the work shown here.
    chats = manager.list()
    activity = [
        {'id': 'chat-' + item['id'], 'kind': 'chat', 'title': item['title'] or 'Untitled chat',
         'status': 'waiting' if item['waiting_on'] else 'running',
         'note': 'Open this chat to respond.' if item['waiting_on'] else
                 f'{item["background"]} background task(s)' if item['background'] else 'Working in this chat',
         'session_id': item['id']}
        for item in chats
        if item['id'] not in scheduled_chats and (item['busy'] or item['waiting_on'] or item['background'])
    ]
    active_chats = {item['id'] for item in chats if item['busy'] or item['waiting_on'] or item['background']}
    for saved in await asyncio.to_thread(manager.stored, limit=100):
        if saved.get('unfinished') and saved['id'] not in scheduled_chats | active_chats:
            activity.append({'id': 'chat-' + saved['id'], 'kind': 'chat',
                             'title': saved.get('title') or 'Untitled chat', 'status': 'interrupted',
                             'note': 'Omote stopped during this chat. Review it before continuing; work is not retried automatically.',
                             'session_id': saved['id']})
    if media.service:
        generations = sorted(media.service.jobs.values(), key=lambda job: job.started_at, reverse=True)
        recent = 0
        for job in generations:
            if job.state not in ('queued', 'running'):
                recent += 1
                if recent > 20:
                    continue
            activity.append({'id': 'media-' + job.id, 'job_id': job.id, 'kind': 'media',
                             'title': job.prompt[:160] or 'Studio creation',
                             'status': {'queued': 'starting', 'done': 'completed'}.get(job.state, job.state),
                             'note': job.note, 'error': job.error, 'started': job.started_at,
                             'progress': job.progress})

    return {
        'schedules': live().store.list(),
        'runs': runs,
        'activity': activity,
        'workspace': str(config.workspace),
        'local': True,
    }


@router.get('/results')
async def saved_results(q: str = Query('', max_length=200), offset: int = Query(0, ge=0, le=1500),
                        limit: int = Query(20, ge=1, le=60)):
    from omote.agent.manager import manager
    from omote.results import SOURCE_LIMIT, list_results
    from omote.routers import media

    active_ids = {item['id'] for item in manager.list()
                  if item['busy'] or item['waiting_on'] or item['background']}
    runs = live().store.runs(limit=SOURCE_LIMIT)
    return await asyncio.to_thread(list_results, manager.store(), media.service.store if media.service else None,
                                   runs, live_ids=active_ids, q=q, offset=offset, limit=limit)


@router.get('/results/chats/{session_id}/files/{file_id}')
async def result_file(session_id: str, file_id: str):
    from omote.agent.manager import manager
    from omote.results import chat_files

    files = await asyncio.to_thread(chat_files, manager.store(), session_id)
    found = next((item for item in files if item['id'] == file_id), None)
    if not found:
        raise HTTPException(404, 'This linked file is no longer available in the original project.')
    return FileResponse(found['path'], media_type='application/octet-stream', filename=found['name'],
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/results/chats/{session_id}')
async def saved_chat_preview(session_id: str):
    from omote.agent.manager import manager
    from omote.results import chat_preview

    store = manager.store()
    if store is None:
        raise HTTPException(404, 'Saved conversations are not available.')
    return await asyncio.to_thread(chat_preview, store, session_id)


@router.post('')
async def create_schedule(spec: Schedule):
    try:
        return live().store.save(validate_root(spec))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put('/{ident}')
async def update_schedule(ident: str, spec: Schedule):
    try:
        return live().store.save(validate_root(spec), ident)
    except KeyError as exc:
        raise HTTPException(404, 'No such scheduled task.') from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/{ident}/runs')
async def run_history(ident: str, limit: int = Query(100, ge=1, le=500)):
    if not live().store.get(ident):
        raise HTTPException(404, 'No such scheduled task.')
    return {'runs': live().store.runs(ident, limit=limit)}


@router.post('/{ident}/run')
async def run_now(ident: str):
    try:
        return live().launch(ident)
    except KeyError as exc:
        raise HTTPException(404, 'No such scheduled task.') from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/runs/{ident}/stop')
async def stop_run(ident: str):
    try:
        await live().stop_run(ident)
        return live().store.run(ident)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete('/{ident}')
async def delete_schedule(ident: str):
    try:
        live().store.delete(ident)
        return {'ok': True}
    except KeyError as exc:
        raise HTTPException(404, 'No such scheduled task.') from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
