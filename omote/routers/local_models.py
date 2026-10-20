"""Explicit user operations; mounting inherits the daemon's token middleware."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from omote.local_models.catalog import Artifact
from omote.local_models.downloads import ModelError
from omote.local_models.service import LocalModels

router = APIRouter(prefix='/api/local-models')
service: LocalModels | None = None


def live():
    if service is None:
        raise HTTPException(503, 'Local model management is unavailable. Check the daemon log or restart Omote.')
    return service


class DeviceMode(BaseModel):
    mode: Literal['auto', 'gpu', 'cpu'] = 'auto'


class CustomModel(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2048)
    size: int = Field(gt=0, le=1024**4)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('')
async def library(response: Response, mode: Literal['auto', 'gpu', 'cpu'] = 'auto'):
    response.headers['Cache-Control'] = 'no-store'
    return await live().snapshot(mode)


@router.post('/models')
async def custom(spec: CustomModel):
    manager = live()
    try:
        artifact = Artifact(id='custom-' + uuid.uuid4().hex[:16], **spec.model_dump())
        await manager.downloads.validate_url(artifact.url)
        manager.store.add(artifact)
        return artifact.model_dump()
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error


async def operation(name, ident=None, mode='auto'):
    manager = live()
    try:
        if name == 'download':
            await manager.download(ident, mode)
        elif name == 'cancel':
            await manager.cancel(ident)
        elif name == 'remove':
            await manager.remove(ident)
        elif name == 'start':
            await manager.start(ident, mode)
        elif name == 'stop':
            await manager.stop()
        elif name == 'prepare':
            return await manager.prepare(ident, mode)
        elif name == 'cancel_setup':
            await manager.cancel_setup()
        return {'ok': True}
    except KeyError as error:
        raise HTTPException(404, 'No such managed model or runtime.') from error
    except (ModelError, OSError) as error:
        raise HTTPException(409, str(error)) from error


@router.post('/{ident}/download')
async def download(ident: str, spec: DeviceMode):
    return await operation('download', ident, spec.mode)


@router.post('/{ident}/cancel')
async def cancel(ident: str):
    return await operation('cancel', ident)


@router.delete('/{ident}')
async def remove(ident: str):
    return await operation('remove', ident)


@router.post('/{ident}/start')
async def start(ident: str, spec: DeviceMode):
    return await operation('start', ident, spec.mode)


@router.post('/{ident}/prepare', status_code=202)
async def prepare(ident: str, spec: DeviceMode):
    return await operation('prepare', ident, spec.mode)


@router.post('/setup/cancel')
async def cancel_setup():
    return await operation('cancel_setup')


@router.post('/runtime/stop')
async def stop():
    return await operation('stop')


@router.post('/runtime/repair')
async def repair():
    try:
        await live().repair_runtime()
        return {'ok': True}
    except (ModelError, OSError) as error:
        raise HTTPException(409, str(error)) from error
