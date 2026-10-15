"""User-controlled, local characters and a library of image, video and 3D assets."""

from __future__ import annotations

import json
import tempfile
import zipfile
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from omote.config import config
from omote.creative.store import CreativeStore
from omote.creative.validation import MAX_UPLOAD, TYPES, InvalidAsset, filename

router = APIRouter(prefix='/api/creative')
store: CreativeStore | None = None
media_store: Any = None


def _store() -> CreativeStore:
    if store is None:
        raise HTTPException(503, 'The creative library is not initialized.')
    return store


def _user() -> str:
    return config.default_user


def _require(item):
    if item is None:
        raise HTTPException(404, 'No such character or asset.')
    return item


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (ValueError, InvalidAsset) as exc:
        raise HTTPException(400, str(exc)) from exc


class CharacterBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=20000)
    appearance_prompt: str = Field(default='', max_length=20000)
    negative_prompt: str = Field(default='', max_length=20000)
    consistency_notes: str = Field(default='', max_length=20000)
    project: str = Field(default='', max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=30)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=100)


class AssetPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    project: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = Field(default=None, max_length=30)
    character_ids: list[str] | None = Field(default=None, max_length=100)


class MediaImportBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    media_id: str = Field(min_length=1, max_length=100)
    name: str = Field(default='', max_length=200)
    project: str = Field(default='', max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=30)
    character_ids: list[str] = Field(default_factory=list, max_length=100)


@router.get('/capabilities')
async def capabilities():
    return {'max_upload_bytes': MAX_UPLOAD, 'extensions': list(TYPES),
            'kinds': ['image', 'video', 'model'], 'self_contained_models': True}


@router.get('/characters')
async def characters(q: str = Query('', max_length=500), project: str = Query('', max_length=500),
                     offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    return _store().list_characters(_user(), q=q, project=project, offset=offset, limit=limit)


@router.post('/characters', status_code=201)
async def create_character(body: CharacterBody):
    return _call(_store().save_character, _user(), body.model_dump())


@router.get('/characters/{ident}')
async def get_character(ident: str):
    return _require(_store().get_character(_user(), ident))


@router.put('/characters/{ident}')
async def update_character(ident: str, body: CharacterBody):
    return _require(_call(_store().save_character, _user(), body.model_dump(), ident))


@router.delete('/characters/{ident}')
async def delete_character(ident: str):
    if not _store().delete_character(_user(), ident):
        raise HTTPException(404, 'No such character.')
    return {'ok': True}


@router.get('/characters/{ident}/recipe')
async def character_recipe(ident: str):
    return _require(_store().recipe(_user(), ident))


@router.post('/characters/{ident}/prepare')
async def prepare_character(ident: str):
    if media_store is None:
        raise HTTPException(503, 'The media store is not initialized.')
    return _require(_call(_store().prepare, _user(), ident, media_store))


@router.get('/apps')
async def creative_apps():
    from omote.creative.apps import catalog

    return catalog()


@router.get('/assets')
async def assets(q: str = Query('', max_length=500), project: str = Query('', max_length=500),
                 kind: Literal['', 'image', 'video', 'model'] = '', character_id: str = '',
                 offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    return _store().list_assets(_user(), q=q, project=project, kind=kind, character_id=character_id,
                                offset=offset, limit=limit)


@router.post('/assets/upload', status_code=201)
async def upload(request: Request, filename_: str = Query(alias='filename', min_length=1, max_length=200),
                 name: str = Query('', max_length=200), project: str = Query('', max_length=500),
                 character_id: str = Query('', max_length=100)):
    """Raw-body uploads are bounded while receiving, even without Content-Length."""
    library = _store()
    _call(filename, filename_)
    length = request.headers.get('content-length')
    if length:
        try:
            size = int(length)
        except ValueError as exc:
            raise HTTPException(400, 'Content-Length must be an integer.') from exc
        if size < 0:
            raise HTTPException(400, 'Content-Length cannot be negative.')
        if size > MAX_UPLOAD:
            raise HTTPException(413, 'Upload exceeds 128 MB.')
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_UPLOAD:
            raise HTTPException(413, 'Upload exceeds 128 MB.')
        data.extend(chunk)
    # Content is validated by signature and extension, not by trusting the
    # browser's MIME string. Unknown MIME defaults are common for 3D files.
    return _call(library.add_asset, _user(), bytes(data), filename=filename_, name=name,
                 project=project, character_ids=[character_id] if character_id else [],
                 provenance={'source': 'upload'})


@router.post('/assets/from-media', status_code=201)
async def from_media(body: MediaImportBody):
    if media_store is None:
        raise HTTPException(503, 'The media store is not initialized.')
    media = media_store.get(body.media_id)
    path = media_store.path(body.media_id)
    if (media is None or path is None or not path.is_file() or path.is_symlink()
            or not path.resolve().is_relative_to(media_store.root.resolve())):
        raise HTTPException(404, 'No such owned generated media.')
    if path.stat().st_size > MAX_UPLOAD:
        raise HTTPException(413, 'Media exceeds 128 MB.')
    with path.open('rb') as stream:
        data = stream.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, 'Media exceeds 128 MB.')
    return _call(_store().add_asset, _user(), data, filename=media.filename,
                 name=body.name or media.prompt[:200] or media.filename, project=body.project,
                 tags=body.tags, character_ids=body.character_ids,
                 provenance={'source': 'generated', 'source_media_id': media.id,
                             'provider': media.provider, 'model': media.model,
                             'prompt': media.prompt, 'seed': media.seed, 'params': media.params,
                             'generated_at': media.created_at})


@router.get('/assets/{ident}')
async def get_asset(ident: str):
    return _require(_store().get_asset(_user(), ident))


@router.patch('/assets/{ident}')
async def update_asset(ident: str, body: AssetPatch):
    # Explicit null is invalid; omission leaves a field unchanged.
    return _require(_call(_store().update_asset, _user(), ident, body.model_dump(exclude_unset=True)))


def asset_response(ident: str, *, download=False):
    library = _store()
    asset = _require(library.get_asset(_user(), ident))
    path = library.asset_path(_user(), ident)
    if path is None:
        raise HTTPException(404, 'The asset file is missing.')
    return FileResponse(path, media_type=asset['media_type'], filename=asset['filename'],
                        content_disposition_type='attachment' if download else 'inline',
                        headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-cache',
                                 'Content-Security-Policy': "default-src 'none'; sandbox"})


@router.get('/assets/{ident}/file')
async def file(ident: str):
    return asset_response(ident)


@router.get('/assets/{ident}/download')
async def download(ident: str):
    return asset_response(ident, download=True)


@router.delete('/assets/{ident}')
async def delete_asset(ident: str):
    if not _store().delete_asset(_user(), ident):
        raise HTTPException(404, 'No such asset.')
    return {'ok': True}


@router.get('/export')
async def export():
    return JSONResponse(_store().export(_user()),
                        headers={'Content-Disposition': 'attachment; filename="omote-creative-library.json"'})


@router.get('/export/archive')
async def export_archive():
    """Portable originals plus an inspectable manifest, with bounded RAM use."""
    library = _store()
    manifest = library.export(_user())
    paths = [(a, library.asset_path(_user(), a['id'])) for a in manifest['assets']]
    manifest['missing_assets'] = [a['id'] for a, p in paths if p is None]
    for asset, path in paths:
        asset['archive_path'] = f'assets/{asset["id"]}/{asset["filename"]}' if path else None

    def stream():
        with tempfile.TemporaryFile() as output:
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for asset, path in paths:
                    if path:
                        archive.write(path, asset['archive_path'])
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            output.seek(0)
            while chunk := output.read(1024 * 1024):
                yield chunk

    return StreamingResponse(stream(), media_type='application/zip',
                             headers={'Content-Disposition': 'attachment; filename="omote-creative-library.zip"'})
