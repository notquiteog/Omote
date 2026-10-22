"""Shop workspace. Rule activation is a direct user action, never an agent tool."""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from omote.commerce.ebay import Ebay, connection, connection_status, save_connection
from omote.commerce.models import MODELS, PLATFORMS, Model, prepare
from omote.commerce.service import ReplyService, Rule, normalized
from omote.commerce.store import Store, project_store, user_dir
from omote.config import config

router = APIRouter(prefix='/api/commerce')
service: ReplyService | None = None


def records(root=''):
    path = Path(root or config.workspace).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(400, 'Choose an existing project folder.')
    return project_store(path)


def call(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('')
def overview(root: str = ''):
    store = records(root)
    return {'root': str(Path(root or config.workspace).expanduser().resolve()), 'records': store.list(),
            'rules': store.rules(), 'activity': store.activity(), 'ebay': connection_status(),
            'last_check': service.last_check if service else 0, 'error': service.error if service else ''}


@router.get('/catalog')
async def catalog():
    return {'platforms': PLATFORMS, 'schemas': {name: model.model_json_schema() for name, model in MODELS.items()},
            'capabilities': {'browser': 'Assisted sourcing, negotiation, listing, relisting and replies on accessible signed-in sites; account/site restrictions may require handover.',
                             'automatic_replies': 'eBay official Message API only. User enables exact replies per item; no LLM or paid generation calls.',
                             'other_sites': 'No unattended inbox connector is installed for other marketplaces. Browser access does not imply API access.'}}


class Save(Model):
    kind: str
    fields: dict
    id: str | None = None
    base_revision: int | None = None


@router.post('/records')
async def save(body: Save, root: str = ''):
    return call(records(root).save, body.kind, body.fields, body.id, body.base_revision)


@router.delete('/records/{ident}')
async def delete(ident: str, revision: int, root: str = ''):
    call(records(root).delete, ident, revision)
    return {'deleted': ident}


@router.get('/records/{ident}/prepare')
async def preparation(ident: str, root: str = ''):
    item = call(records(root).get, ident)
    return prepare(item['kind'], item['fields'])


class Connect(BaseModel):
    # Validate manually so malformed credentials are not echoed in FastAPI's
    # validation error input field.
    token: object = None


@router.post('/ebay/connect')
async def connect(body: Connect):
    if not isinstance(body.token, str) or not 10 <= len(body.token.strip()) <= 16000:
        raise HTTPException(400, 'Enter a valid eBay user access token.')
    token = body.token.strip()
    try:
        username = await Ebay(token).verify()
        def persist():
            previous = connection_status()
            if previous.get('username') != username:
                for path in user_dir().glob('*.db'):
                    store = Store(path)
                    for rule in store.rules():
                        store.put_rule(rule | {'enabled': False})
            save_connection({'token': token, 'username': username})
        if service:
            async with service.lock:
                persist()
        else:
            persist()
        return connection_status()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete('/ebay/connect')
async def disconnect():
    async def remove():
        for path in user_dir().glob('*.db'):
            store = Store(path)
            for rule in store.rules():
                store.put_rule(rule | {'enabled': False})
        (user_dir() / 'ebay.json').unlink(missing_ok=True)
    if service:
        async with service.lock:
            await remove()
    else:
        await remove()
    return {'connected': False}


@router.get('/ebay/inbox')
async def inbox(item: str = '', offset: int = 0):
    if offset < 0 or offset > 10000:
        raise HTTPException(400, 'Invalid inbox offset.')
    try:
        return await Ebay().conversations(item, offset)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/rules')
async def enable_rule(body: Rule, root: str = ''):
    if config.local_only:
        raise HTTPException(400, 'Turn off local-only mode before enabling eBay replies.')
    settings = call(connection)
    if not normalized(body.question):
        raise HTTPException(400, 'Enter the complete question to match.')
    for path in user_dir().glob('*.db'):
        for rule in Store(path).rules():
            if (rule.get('enabled') and rule['expires'] > time.time() and rule['username'] == settings['username']
                    and rule['item_id'] == body.item_id and normalized(rule['question']) == normalized(body.question)):
                raise HTTPException(409, 'This item and question already have an active rule. Pause it before replacing it.')
    rule = body.model_dump() | {'id': uuid.uuid4().hex, 'username': settings['username'], 'enabled': True,
                                'started': time.time(), 'expires': time.time() + body.days * 86400}
    records(root).put_rule(rule)
    return rule


@router.post('/rules/{ident}/pause')
async def pause(ident: str, root: str = ''):
    store = records(root)
    rule = next((r for r in store.rules() if r['id'] == ident), None)
    if not rule:
        raise HTTPException(404, 'No such rule in this project.')
    store.put_rule(rule | {'enabled': False})
    return {'paused': ident}
