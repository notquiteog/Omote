"""Versioned ComfyUI workbench. Only configured connections and owned runs are reachable."""
from __future__ import annotations

import contextlib
import json
import math
import re
import sqlite3
import time
import uuid
from collections import deque
from pathlib import PurePosixPath
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from omote.config import config
from omote.providers.base import Modality, NoProviderError
from omote.providers.comfyui import ComfyUIProvider
from omote.providers.registry import registry

router = APIRouter(prefix='/api/media/comfyui')
MAX_GRAPH = 4 * 1024 * 1024
MAX_OUTPUT = 128 * 1024 * 1024
# Each network operation is limited to 120 seconds; renew between operations.
IMPORT_LEASE_SECONDS = 300


@contextlib.contextmanager
def database():
    config.data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.data_dir / 'workbench.db', timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY,user TEXT,name TEXT,project TEXT,updated REAL);
        CREATE TABLE IF NOT EXISTS versions(workflow_id TEXT REFERENCES workflows(id) ON DELETE CASCADE,
          revision INTEGER,created REAL,notes TEXT,graph TEXT,prompt TEXT,PRIMARY KEY(workflow_id,revision));
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,user TEXT,provider TEXT,prompt_id TEXT,
          workflow_id TEXT,revision INTEGER,created REAL,snapshot TEXT,imports TEXT DEFAULT '{}');
    ''')
    try:
        if db.execute('PRAGMA user_version').fetchone()[0] < 1:
            # Serialize first-use migration across processes. Old snapshots did
            # not record names/projects, so current metadata is the only honest
            # backfill. Graphs, prompts, notes and existing run snapshots remain.
            db.execute('BEGIN IMMEDIATE')
            version_columns = {row[1] for row in db.execute('PRAGMA table_info(versions)')}
            for column in ('name', 'project'):
                if column not in version_columns:
                    db.execute(f'ALTER TABLE versions ADD COLUMN {column} TEXT')
                    db.execute(f'UPDATE versions SET {column}=(SELECT {column} FROM workflows WHERE id=workflow_id)')
            run_columns = {row[1] for row in db.execute('PRAGMA table_info(runs)')}
            for column, kind in (('import_lease', 'TEXT'), ('import_until', 'REAL')):
                if column not in run_columns:
                    db.execute(f'ALTER TABLE runs ADD COLUMN {column} {kind}')
            db.execute('PRAGMA user_version=1')
            db.commit()
        with db:
            yield db
    finally:
        db.close()


def owner(db, ident):
    row = db.execute('SELECT * FROM workflows WHERE id=? AND user=?', (ident, config.default_user)).fetchone()
    if row is None:
        raise HTTPException(404, 'No such workflow')
    return dict(row)


class Snapshot(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    project: str = Field(default='', max_length=300)
    notes: str = Field(default='', max_length=4000)
    graph: dict[str, Any] | None = None
    prompt: dict[str, Any] | None = None
    base_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def check_graph(self):
        self.name = self.name.strip()
        if not self.name or not (self.graph or self.prompt):
            raise ValueError('A name and a ComfyUI graph or API prompt are required')
        if self.graph is not None and not isinstance(self.graph.get('nodes'), list):
            raise ValueError('Editor workflow JSON must contain a nodes array')
        if self.graph is not None and len(self.graph['nodes']) > 5000:
            raise ValueError('At most 5000 nodes per workflow')
        if self.prompt is not None:
            validate_prompt_shape(self.prompt)
        if len(json.dumps([self.graph, self.prompt], allow_nan=False).encode()) > MAX_GRAPH:
            raise ValueError('Workflow exceeds 4 MB')
        return self


def validate_prompt_shape(prompt):
    if not isinstance(prompt, dict) or not prompt or len(prompt) > 5000:
        raise ValueError('API prompt must contain 1–5000 nodes')
    for ident, node in prompt.items():
        if not isinstance(node, dict) or not isinstance(node.get('class_type'), str) or not isinstance(node.get('inputs'), dict):
            raise ValueError(f'Node {ident} needs class_type and inputs; export API format or capture from the editor')


def snapshot(db, ident, revision=None):
    meta = owner(db, ident)
    row = db.execute('SELECT * FROM versions WHERE workflow_id=? AND (? IS NULL OR revision=?) ORDER BY revision DESC LIMIT 1',
                     (ident, revision, revision)).fetchone()
    if row is None:
        raise HTTPException(404, 'No such workflow version')
    return {**meta, 'name': row['name'], 'project': row['project'], 'revision': row['revision'], 'created_at': row['created'], 'notes': row['notes'],
            'graph': json.loads(row['graph']) if row['graph'] else None,
            'prompt': json.loads(row['prompt']) if row['prompt'] else None}


def insert_version(db, ident, revision, body):
    db.execute('INSERT INTO versions(workflow_id,revision,created,notes,graph,prompt,name,project) VALUES(?,?,?,?,?,?,?,?)',
               (ident, revision, time.time(), body.notes, json.dumps(body.graph) if body.graph else None,
                json.dumps(body.prompt) if body.prompt else None, body.name, body.project))


@router.get('/workflows')
def workflows(q: str = '', project: str = '', limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    with database() as db:
        rows = db.execute('SELECT w.*,MAX(v.revision) AS revision FROM workflows w JOIN versions v ON v.workflow_id=w.id '
                          'WHERE w.user=? GROUP BY w.id ORDER BY w.updated DESC', (config.default_user,)).fetchall()
        found = [dict(r) for r in rows if (not project or r['project'] == project)
                 and q.casefold() in (r['name'] + ' ' + r['project']).casefold()]
        return {'workflows': found[offset:offset+limit], 'total': len(found)}


@router.post('/workflows')
def create(body: Snapshot):
    ident = uuid.uuid4().hex
    with database() as db:
        db.execute('INSERT INTO workflows VALUES(?,?,?,?,?)', (ident, config.default_user, body.name, body.project, time.time()))
        insert_version(db, ident, 1, body)
        return snapshot(db, ident)


@router.get('/workflows/{ident}')
def get_workflow(ident: str, revision: int | None = Query(default=None, ge=1)):
    with database() as db:
        return snapshot(db, ident, revision)


@router.get('/workflows/{ident}/versions')
def versions(ident: str):
    with database() as db:
        owner(db, ident)
        return {'versions': [dict(r) for r in db.execute('SELECT revision,created,notes FROM versions WHERE workflow_id=? ORDER BY revision DESC', (ident,))]}


@router.get('/workflows/{ident}/compare')
def compare_versions(ident: str, before: int = Query(ge=1), after: int = Query(ge=1)):
    from omote.workflow_diff import compare

    with database() as db:
        older = snapshot(db, ident, before)
        newer = snapshot(db, ident, after)
    fields = ('name', 'project', 'notes', 'graph', 'prompt')
    return {'before_revision': before, 'after_revision': after,
            **compare({key: older[key] for key in fields}, {key: newer[key] for key in fields})}


@router.post('/workflows/{ident}/versions')
def save_version(ident: str, body: Snapshot):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        current = snapshot(db, ident)
        if body.base_revision != current['revision']:
            raise HTTPException(409, 'This workflow changed. Reload the latest version or save a separate copy.')
        insert_version(db, ident, current['revision'] + 1, body)
        db.execute('UPDATE workflows SET name=?,project=?,updated=? WHERE id=?', (body.name, body.project, time.time(), ident))
        return snapshot(db, ident)


@router.delete('/workflows/{ident}')
def delete_workflow(ident: str):
    with database() as db:
        owner(db, ident)
        db.execute('DELETE FROM workflows WHERE id=?', (ident,))
    return {'ok': True}


def connection(ident: str):
    info = next((item for item in registry.list() if item.id == ident), None)
    if info is None:
        raise HTTPException(404, 'No such ComfyUI connection')
    if registry.defaults.local_only and not info.local:
        raise HTTPException(403, 'Local-only mode refuses this remote connection')
    for modality in (Modality.IMAGE, Modality.VIDEO):
        try:
            impl = registry.impl(ident, modality)
        except NoProviderError:
            continue
        if isinstance(impl, ComfyUIProvider):
            return impl
    raise HTTPException(400, 'This is not a ComfyUI connection')


async def upstream(impl, path, *, body=None):
    try:
        async with impl.transport.session(30) as session:
            method = session.get if body is None else session.post
            options = {'headers': impl._headers(), 'allow_redirects': False}
            if body is not None:
                options['json'] = body
            async with method(f'{impl.base_url}/{path}', **options) as response:
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > 32 * 1024 * 1024:
                        raise HTTPException(502, 'ComfyUI response is too large')
                if response.status != 200:
                    # Return structured validation errors, never headers or credential-bearing URLs.
                    try:
                        problem = json.loads(raw)
                        detail = json.dumps({key: problem[key] for key in ('error', 'node_errors') if problem.get(key)})[:4000]
                    except (ValueError, AttributeError):
                        detail = ''
                    raise HTTPException(502, f'ComfyUI returned HTTP {response.status}. {detail}')
                return json.loads(raw)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise HTTPException(502, f'Could not read ComfyUI response ({type(exc).__name__}). Check the connection.') from exc


@router.get('/connections/{provider}/nodes')
async def nodes(provider: str):
    return {'nodes': await upstream(connection(provider), 'object_info')}


class PromptBody(BaseModel):
    prompt: dict[str, Any]

    @model_validator(mode='after')
    def bounded(self):
        if len(json.dumps(self.prompt, allow_nan=False).encode()) > MAX_GRAPH:
            raise ValueError('API prompt exceeds 4 MB')
        return self


def finite_number(value):
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def inspect_prompt(prompt, catalog):
    errors, warnings = [], []
    try:
        validate_prompt_shape(prompt)
    except ValueError as exc:
        return {'valid': False, 'errors': [str(exc)], 'warnings': []}
    indegree = dict.fromkeys(prompt, 0)
    outgoing = {ident: [] for ident in prompt}
    for ident, node in prompt.items():
        spec = catalog.get(node['class_type'])
        if not isinstance(spec, dict):
            errors.append(f"{ident}: missing node type {node['class_type']}")
            continue
        required = spec.get('input', {}).get('required', {})
        optional = spec.get('input', {}).get('optional', {})
        for name in required:
            if name not in node['inputs']:
                errors.append(f'{ident}.{name}: required input is missing')
        for name, value in node['inputs'].items():
            rule = required.get(name, optional.get(name))
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                target, slot = value
                if target not in prompt:
                    errors.append(f'{ident}.{name}: linked node {target} is missing')
                else:
                    indegree[ident] += 1
                    outgoing[target].append(ident)
                    outputs = catalog.get(prompt[target]['class_type'], {}).get('output', [])
                    if slot < 0 or slot >= len(outputs):
                        errors.append(f'{ident}.{name}: output slot {slot} does not exist on {target}')
                    elif rule and isinstance(rule[0], str) and rule[0] not in ('*', outputs[slot]) and outputs[slot] != '*':
                        warnings.append(f'{ident}.{name}: linked type {outputs[slot]} differs from {rule[0]}; custom nodes may convert it')
                continue
            if not rule:
                continue
            datatype = rule[0]
            options = rule[1] if len(rule) > 1 and isinstance(rule[1], dict) else {}
            if isinstance(datatype, list) and value not in datatype:
                errors.append(f'{ident}.{name}: selection is not installed or available')
            if datatype == 'STRING' and not isinstance(value, str):
                errors.append(f'{ident}.{name}: expected STRING')
            if datatype == 'BOOLEAN' and not isinstance(value, bool):
                errors.append(f'{ident}.{name}: expected BOOLEAN')
            if datatype in ('INT', 'FLOAT'):
                if not isinstance(value, int | float) or isinstance(value, bool) or (datatype == 'INT' and not isinstance(value, int)):
                    errors.append(f'{ident}.{name}: expected {datatype}')
                elif not finite_number(value):
                    errors.append(f'{ident}.{name}: expected a finite number')
                elif ('min' in options and value < options['min']) or ('max' in options and value > options['max']):
                    errors.append(f'{ident}.{name}: value is outside the supported range')
    ready = deque(ident for ident, n in indegree.items() if n == 0)
    visited = 0
    while ready:
        ident = ready.popleft()
        visited += 1
        for child in outgoing[ident]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(prompt):
        errors.append('The API graph contains a dependency cycle')
    return {'valid': not errors, 'errors': errors, 'warnings': warnings}


@router.post('/connections/{provider}/validate')
async def validate(provider: str, body: PromptBody):
    return inspect_prompt(body.prompt, await upstream(connection(provider), 'object_info'))


class RunBody(BaseModel):
    workflow_id: str
    revision: int = Field(ge=1)
    provider: str


@router.post('/runs')
async def run(body: RunBody):
    impl = connection(body.provider)
    with database() as db:
        saved = snapshot(db, body.workflow_id, body.revision)
    if not saved['prompt']:
        raise HTTPException(400, 'This version has editor JSON only. Capture it with the editor bridge or import its API export before running.')
    report = inspect_prompt(saved['prompt'], await upstream(impl, 'object_info'))
    if not report['valid']:
        raise HTTPException(422, report)
    payload = {'prompt': saved['prompt'], 'client_id': f'omote-{uuid.uuid4().hex}'}
    if saved['graph']:
        payload['extra_data'] = {'extra_pnginfo': {'workflow': saved['graph']}}
    queued = await upstream(impl, 'prompt', body=payload)
    prompt_id = queued.get('prompt_id')
    if not isinstance(prompt_id, str) or not prompt_id:
        raise HTTPException(502, 'ComfyUI did not return a prompt ID')
    ident = uuid.uuid4().hex
    with database() as db:
        db.execute('INSERT INTO runs(id,user,provider,prompt_id,workflow_id,revision,created,snapshot) VALUES(?,?,?,?,?,?,?,?)',
                   (ident, config.default_user, body.provider, prompt_id, body.workflow_id, body.revision, time.time(), json.dumps(saved)))
    return {'id': ident, 'prompt_id': prompt_id, 'state': 'queued'}


def owned_run(db, ident):
    row = db.execute('SELECT * FROM runs WHERE id=? AND user=?', (ident, config.default_user)).fetchone()
    if row is None:
        raise HTTPException(404, 'No such run')
    return dict(row)


@router.get('/runs')
def runs():
    with database() as db:
        rows = db.execute('SELECT id,provider,prompt_id,workflow_id,revision,created FROM runs WHERE user=? ORDER BY created DESC LIMIT 100',
                          (config.default_user,)).fetchall()
        return {'runs': [dict(row) for row in rows]}


async def run_state(row):
    impl = connection(row['provider'])
    history = await upstream(impl, 'history')
    record = history.get(row['prompt_id'])
    if record:
        return impl, record, 'failed' if record.get('status', {}).get('status_str') == 'error' else 'done'
    queue = await upstream(impl, 'queue')
    for field, state in (('queue_running', 'running'), ('queue_pending', 'queued')):
        if any(len(item) > 1 and item[1] == row['prompt_id'] for item in queue.get(field, [])):
            return impl, {}, state
    return impl, {}, 'unavailable'


def output_files(record):
    result = []
    raw_outputs = record.get('outputs')
    if not isinstance(raw_outputs, dict):
        return result
    for node_id, outputs in raw_outputs.items():
        if not isinstance(outputs, dict):
            continue
        for items in outputs.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get('filename'), str):
                    continue
                name, subfolder = item['filename'], str(item.get('subfolder', ''))
                if name in ('', '.', '..') or any(ord(c) < 32 for c in name + subfolder) or re.match(r'^[A-Za-z]:', name + subfolder) or PurePosixPath(name).name != name or '\\' in name or any(p == '..' for p in PurePosixPath(subfolder).parts) or subfolder.startswith('/') or re.match(r'^[A-Za-z]:', subfolder) or '\\' in subfolder:
                    continue
                if item.get('type', 'output') not in ('output', 'temp'):
                    continue
                result.append({'node': node_id, 'filename': name, 'subfolder': subfolder, 'type': item.get('type', 'output')})
    return result


@router.get('/runs/{ident}')
async def get_run(ident: str):
    with database() as db:
        row = owned_run(db, ident)
    _, record, state = await run_state(row)
    saved = json.loads(row['snapshot'])
    return {key: row[key] for key in ('id', 'provider', 'prompt_id', 'created')} | {
        'state': state, 'name': saved['name'], 'revision': row['revision'], 'outputs': output_files(record),
        'error': str(record.get('status', {}).get('messages', ''))[:2000] if state == 'failed' else '',
        'imported_assets': list(json.loads(row['imports']).values()),
    }


@router.delete('/runs/{ident}')
async def cancel(ident: str):
    with database() as db:
        row = owned_run(db, ident)
    impl, _, state = await run_state(row)
    if state == 'running':
        raise HTTPException(409, 'This run is executing. Stop it in the native editor; the server-wide interrupt could stop another user’s work.')
    if state == 'queued':
        await upstream(impl, 'queue', body={'delete': [row['prompt_id']]})
    return {'ok': True}


def renew_import(ident: str, user: str, lease: str):
    with database() as db:
        updated = db.execute('UPDATE runs SET import_until=? WHERE id=? AND user=? AND import_lease=?',
                             (time.time() + IMPORT_LEASE_SECONDS, ident, user, lease))
        if not updated.rowcount:
            raise HTTPException(409, 'The output import was interrupted. Refresh the run before retrying.')


@router.post('/runs/{ident}/import')
async def import_outputs(ident: str):
    from omote.routers import creative
    library = creative.store
    if library is None:
        raise HTTPException(503, 'Creative library is unavailable')
    lease = uuid.uuid4().hex
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = owned_run(db, ident)
        user = row['user']
        claimed = db.execute('UPDATE runs SET import_lease=?,import_until=? WHERE id=? AND user=? '
                             'AND COALESCE(import_until,0)<=?',
                             (lease, time.time() + IMPORT_LEASE_SECONDS, ident, user, time.time()))
        if not claimed.rowcount:
            raise HTTPException(409, 'This run already has an output import in progress. Wait for it to finish, then retry.')
    try:
        impl, record, state = await run_state(row)
        if state != 'done':
            raise HTTPException(409, 'The run has not completed')
        saved = json.loads(row['snapshot'])
        imports, errors = json.loads(row['imports']), []
        for item in output_files(record):
            key = json.dumps(item, sort_keys=True)
            if key in imports:
                continue
            try:
                renew_import(ident, user, lease)
                async with impl.transport.session(120) as session:
                    async with session.get(f'{impl.base_url}/view', params={k: v for k, v in item.items() if k != 'node'},
                                           headers=impl._headers(), allow_redirects=False) as response:
                        if response.status != 200:
                            raise ValueError(f'HTTP {response.status}')
                        data = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            data.extend(chunk)
                            if len(data) > MAX_OUTPUT:
                                raise ValueError('output exceeds 128 MB')
                # A resumed worker that lost its lease cannot add a duplicate.
                renew_import(ident, user, lease)
                asset = library.add_asset(user, bytes(data), filename=item['filename'],
                    project=saved['project'], provenance={'source': 'comfyui', 'provider': row['provider'],
                    'prompt_id': row['prompt_id'], 'workflow_id': row['workflow_id'], 'revision': row['revision'], 'node': item['node']})
                imports[key] = asset['id']
                with database() as db:
                    db.execute('UPDATE runs SET imports=? WHERE id=? AND user=? AND import_lease=?',
                               (json.dumps(imports), ident, user, lease))
            except (ValueError, OSError, aiohttp.ClientError, TimeoutError) as exc:
                errors.append(f"{item['filename']}: {type(exc).__name__}: {str(exc)[:200]}")
        return {'asset_ids': list(imports.values()), 'errors': errors}
    finally:
        with database() as db:
            db.execute('UPDATE runs SET import_lease=NULL,import_until=NULL WHERE id=? AND user=? AND import_lease=?',
                       (ident, user, lease))
