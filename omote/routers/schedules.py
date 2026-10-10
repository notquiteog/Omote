"""Manage local scheduled work through the same authenticated UI as chat."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

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
    from omote.config import config

    return {
        'schedules': live().store.list(),
        'runs': live().store.runs(),
        'workspace': str(config.workspace),
        'local': True,
    }


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
