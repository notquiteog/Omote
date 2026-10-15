"""Authenticated, read-only telemetry for the computer running the daemon."""

from fastapi import APIRouter, Response

from omote.agent.manager import manager
from omote.config import config
from omote.resources import monitor

router = APIRouter(prefix='/api/system')


@router.get('/usage')
async def usage(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    snapshot = await monitor.snapshot(config.workspace)
    # Copy rather than mutating the shared hardware sample with session data.
    sessions = [s for s in manager.list() if s['busy'] or s['background'] or s['waiting_on']]
    return {**snapshot, 'work': sessions[:30], 'work_count': len(sessions)}
