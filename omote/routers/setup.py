"""Authenticated setup controls. Downloads happen only after an explicit action."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from omote.setup.process import SetupError
from omote.setup.service import ToolSetup

router = APIRouter(prefix='/api/setup')
service: ToolSetup | None = None


def live():
    if service is None:
        raise HTTPException(503, 'Local setup is unavailable. Restart Omote to try again.')
    return service


class SetupRequest(BaseModel):
    engine: Literal['chromium', 'firefox', 'webkit'] = 'chromium'
    device: Literal['auto', 'cpu'] = 'auto'


@router.get('')
async def status():
    return live().snapshot()


@router.post('/{name}/start', status_code=202)
async def start(name: Literal['comfyui', 'playwright'], body: SetupRequest):
    try:
        return await live().start(name, engine=body.engine, device=body.device)
    except (SetupError, ValueError, OSError) as error:
        raise HTTPException(409, str(error)) from error


@router.post('/{name}/cancel')
async def cancel(name: Literal['comfyui', 'playwright']):
    return await live().cancel(name)


@router.post('/comfyui/stop')
async def stop():
    try:
        await live().stop_comfy()
        return live().snapshot()['comfyui']
    except (SetupError, OSError) as error:
        raise HTTPException(409, str(error)) from error
