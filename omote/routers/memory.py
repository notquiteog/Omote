"""Seeing, correcting and deleting what has been remembered.

Every one of these exists because memory a person cannot inspect is
surveillance rather than a feature. If it can learn about you, you must be
able to read the whole of it, remove one item, and remove all of it — without
asking anyone.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from omote.config import config
from omote.memory.store import KINDS

router = APIRouter(prefix='/api/memory')

# Set at start-up, and left None when the feature is off for the whole install.
service = None


def _service():
    if service is None:
        # 404 rather than 503: when memory is off for this install, the
        # endpoints genuinely do not exist.
        raise HTTPException(status_code=404, detail='memory is not enabled on this server')
    return service


def _user() -> str:
    return config.default_user


class SettingsBody(BaseModel):
    enabled: bool
    # Passive capture from conversation, which is a bigger step than storing
    # what you explicitly asked to be stored — so it is its own switch.
    auto_capture: bool = False


@router.get('/settings')
async def get_settings() -> dict[str, object]:
    svc = _service()
    settings = svc.settings(_user())
    return {
        'enabled': settings.enabled,
        'auto_capture': settings.auto_capture,
        'count': svc.store.count(_user()),
        'embedding_model': svc.embedder() or None,
        'kinds': list(KINDS),
    }


# -- the index ---------------------------------------------------------------
#
# A vector index is derived data, and the three things anyone needs from derived
# data are to see its state, to rebuild it, and to throw it away and make it
# again. None of these touches what the user wrote: `DELETE /api/memory` is the
# only thing here that deletes a memory.


@router.get('/index')
async def index_state() -> dict[str, object]:
    state = _service().index_state()
    return {
        'embedder': state.embedder or None,
        'dims': state.dims,
        'total': state.total,
        'searchable': state.searchable,
        'pending': state.pending,
        'accelerated': state.accelerated,
        'set_at': state.set_at or None,
    }


@router.post('/index/rebuild')
async def rebuild_index() -> dict[str, object]:
    """Rebuild the search index from the vectors already in the store.

    Cheap and local: no provider call, nothing re-embedded. For an index that
    was interrupted, or was never built because the extension arrived later.
    """
    svc = _service()
    indexed = svc.rebuild_index()
    return {'indexed': indexed, 'accelerated': svc.index_state().accelerated}


class ResetBody(BaseModel):
    # Off leaves every memory pending, which is a store that recalls nothing
    # until a re-embed runs. Worth being able to ask for -- a provider that is
    # down should not turn a reset into a failure -- but not the default.
    reembed: bool = True


@router.post('/index/reset')
async def reset_index(body: ResetBody | None = None) -> dict[str, int]:
    """Discard every vector and make them again from the stored text."""
    svc = _service()
    return await svc.reset_index(reembed=(body or ResetBody()).reembed)


@router.post('/index/reembed')
async def reembed() -> dict[str, int]:
    """Embed whatever is still pending. Safe to call repeatedly."""
    return await _service().reembed()


@router.put('/settings')
async def put_settings(body: SettingsBody) -> dict[str, object]:
    svc = _service()
    settings = svc.set_settings(_user(), enabled=body.enabled, auto_capture=body.auto_capture)
    return {'enabled': settings.enabled, 'auto_capture': settings.auto_capture}


@router.get('')
async def list_memories(kind: str | None = None, subject: str | None = None,
                        limit: int = Query(200, ge=1, le=1000),
                        offset: int = Query(0, ge=0)) -> dict[str, object]:
    svc = _service()
    return {
        'memories': [
            {
                'id': m.id,
                'kind': m.kind,
                'text': m.text,
                'source': m.source,
                'subject': m.subject,
                'created_at': m.created_at,
                'hits': m.hits,
            }
            for m in svc.store.list(_user(), kind=kind, subject=subject, limit=limit, offset=offset)
        ],
        'total': svc.store.count(_user()),
        'offset': offset,
    }


class AddBody(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    kind: str = Field(default='fact', min_length=1, max_length=100)
    subject: str | None = Field(default=None, max_length=500)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('memory text cannot be blank')
        return value.strip()

    @field_validator('kind')
    @classmethod
    def valid_kind(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('kind cannot be blank')
        return value.strip()


@router.post('')
async def add_memory(body: AddBody) -> dict[str, object]:
    svc = _service()
    if not svc.settings(_user()).enabled:
        raise HTTPException(status_code=409, detail='memory is switched off for this user')
    memory = await svc.remember(_user(), body.text, kind=body.kind, subject=body.subject, source='user')
    if memory is None:
        raise HTTPException(status_code=503, detail='Could not embed this memory. Check the embedding connection.')
    return asdict(memory)


@router.get('/export')
async def export_memories() -> JSONResponse:
    svc = _service()
    # A full export, including paused memories, without vector blobs or keys.
    return JSONResponse(
        {'version': 1, 'settings': asdict(svc.settings(_user())),
         'embedding_model': svc.embedder() or None,
         'memories': [asdict(m) for m in svc.store.list(_user(), limit=-1)]},
        headers={'Content-Disposition': 'attachment; filename="omote-memories.json"'},
    )


@router.put('/{memory_id}')
async def edit_memory(memory_id: str, body: AddBody) -> dict[str, object]:
    svc = _service()
    memory = svc.store.update(_user(), memory_id, body.text, kind=body.kind, subject=body.subject)
    if memory is None:
        raise HTTPException(status_code=404, detail='no such memory')
    indexed = False
    # Pausing memory also pauses sending its text to the embedding provider.
    if svc.settings(_user()).enabled:
        try:
            svc.sync_embedder()
            embedding_name = svc.embedder()
            vectors = await svc._embed([memory.text])
            # A deletion or another edit may have arrived while embedding.
            current = svc.store.get(_user(), memory_id)
            if (current and current.text == memory.text and svc.settings(_user()).enabled
                    and svc.embedder() == embedding_name):
                indexed = svc.store.set_vector(memory_id, vectors[0], embedding_name)
        except Exception:
            # The correction was saved locally; retry from the index controls.
            pass
    return {'memory': asdict(memory), 'indexed': indexed}


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=20000)
    limit: int = Field(default=5, ge=1, le=100)
    subject: str | None = None
    kind: str | None = None


@router.post('/search')
async def search_memories(body: SearchBody) -> dict[str, object]:
    svc = _service()
    result = await svc.recall(_user(), body.query, limit=body.limit, subject=body.subject, kind=body.kind)
    return {
        'reason': result.reason,
        'memories': [{'id': m.id, 'text': m.text, 'score': round(m.score, 4), 'kind': m.kind} for m in result.memories],
    }


@router.delete('/{memory_id}')
async def delete_memory(memory_id: str) -> dict[str, bool]:
    svc = _service()
    if not svc.store.delete(_user(), memory_id):
        raise HTTPException(status_code=404, detail='no such memory')
    return {'ok': True}


@router.delete('')
async def wipe() -> dict[str, int]:
    """Delete everything, and the key with it.

    This also switches memory back off: a wipe that left the feature on would
    start refilling from the next message, which is not what anyone means.
    """
    svc = _service()
    return {'deleted': svc.store.wipe(_user())}
