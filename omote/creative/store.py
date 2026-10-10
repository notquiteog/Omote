"""A local SQLite catalogue and opaque, immutable asset files.

Every lookup is scoped to the configured user. Original filenames are metadata,
never paths. Uploaded content is validated before either the DB or disk changes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from omote.creative.validation import InvalidAsset, validate

CHARACTER_FIELDS = ('name', 'description', 'appearance_prompt', 'negative_prompt', 'consistency_notes', 'project', 'tags')
ASSET_FIELDS = ('name', 'description', 'project', 'tags')
SECRET_KEYS = {'api_key', 'apikey', 'authorization', 'password', 'secret', 'access_token', 'refresh_token', 'token'}


def _text(value: Any, name: str, maximum: int = 20000, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{name} must be text with at most {maximum} characters.')
    value = value.strip()
    if required and not value:
        raise ValueError(f'{name} cannot be blank.')
    return value


def _tags(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 30:
        raise ValueError('Use at most 30 tags.')
    return list(dict.fromkeys(_text(tag, 'tag', 80, required=True) for tag in value))


def _provenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError('Provenance must be a JSON object.')

    def clean(item, depth=0):
        if depth > 20:
            raise ValueError('Provenance is nested too deeply.')
        if isinstance(item, dict):
            return {str(k): clean(v, depth + 1) for k, v in item.items()
                    if str(k).lower().replace('-', '_') not in SECRET_KEYS}
        if isinstance(item, list):
            return [clean(v, depth + 1) for v in item]
        return item

    result = clean(value)
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('Provenance must contain valid JSON values.') from exc
    if len(encoded.encode()) > 65536:
        raise ValueError('Provenance exceeds 64 KB.')
    return result


class CreativeStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blobs = self.root / 'assets'
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.database = self.root / 'library.sqlite3'
        with self._db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS characters (
                    id TEXT PRIMARY KEY, user TEXT NOT NULL, name TEXT NOT NULL,
                    description TEXT NOT NULL, appearance_prompt TEXT NOT NULL,
                    negative_prompt TEXT NOT NULL, consistency_notes TEXT NOT NULL,
                    project TEXT NOT NULL, tags TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS characters_user ON characters(user, updated_at DESC);
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, user TEXT NOT NULL, name TEXT NOT NULL,
                    description TEXT NOT NULL, kind TEXT NOT NULL, media_type TEXT NOT NULL,
                    filename TEXT NOT NULL, blob TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL, project TEXT NOT NULL, tags TEXT NOT NULL,
                    provenance TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS assets_user ON assets(user, updated_at DESC);
                CREATE TABLE IF NOT EXISTS references_to_characters (
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(asset_id, character_id)
                );
                CREATE TABLE IF NOT EXISTS character_references (
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(asset_id, character_id)
                );
                CREATE TABLE IF NOT EXISTS prepared_media (
                    asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
                    media_id TEXT NOT NULL
                );
            ''')
            if 'position' not in {row[1] for row in db.execute('PRAGMA table_info(references_to_characters)')}:
                db.execute('ALTER TABLE references_to_characters ADD COLUMN position INTEGER NOT NULL DEFAULT 0')
            if db.execute('PRAGMA user_version').fetchone()[0] < 1:
                # Earlier catalogues conflated associated artwork and identity
                # references. Preserve existing image references once, while
                # retaining videos/models solely as general associations.
                db.execute('''INSERT OR IGNORE INTO character_references
                    SELECT r.asset_id, r.character_id, r.position
                    FROM references_to_characters r
                    JOIN assets a ON a.id=r.asset_id
                    JOIN characters c ON c.id=r.character_id
                    WHERE a.kind='image' AND a.user=c.user''')
                db.execute('PRAGMA user_version=1')
        self.database.chmod(0o600)

    @contextlib.contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.create_function('casefold', 1, lambda s: str(s).casefold(), deterministic=True)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _associations(db, user: str, table: str, ids: list[str]) -> list[str]:
        if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(i, str) for i in ids):
            raise ValueError('Use at most 100 character or reference IDs.')
        ids = list(dict.fromkeys(ids))
        for ident in ids:
            if db.execute(f'SELECT id FROM {table} WHERE id=? AND user=?', (ident, user)).fetchone() is None:
                raise ValueError('A referenced character or asset does not exist for this user.')
        return ids

    @staticmethod
    def _character(db, row) -> dict[str, Any]:
        item = dict(row)
        item.pop('user')
        item['tags'] = json.loads(item['tags'])
        item['reference_asset_ids'] = [r[0] for r in db.execute(
            'SELECT asset_id FROM character_references WHERE character_id=? ORDER BY position, asset_id', (item['id'],)
        )]
        return item

    @staticmethod
    def _asset(db, row) -> dict[str, Any]:
        item = dict(row)
        item.pop('user')
        item.pop('blob')
        item['tags'] = json.loads(item['tags'])
        item['provenance'] = json.loads(item['provenance'])
        item['character_ids'] = [r[0] for r in db.execute(
            'SELECT character_id FROM references_to_characters WHERE asset_id=? ORDER BY character_id', (item['id'],)
        )]
        item['url'] = f'/api/creative/assets/{item["id"]}/file'
        item['download_url'] = f'/api/creative/assets/{item["id"]}/download'
        return item

    def save_character(self, user: str, fields: dict[str, Any], ident: str | None = None) -> dict[str, Any] | None:
        values = {k: _text(fields.get(k, ''), k, 200 if k == 'name' else 500 if k == 'project' else 20000,
                           required=k == 'name') for k in CHARACTER_FIELDS if k != 'tags'}
        values['tags'] = json.dumps(_tags(fields.get('tags', [])), ensure_ascii=False)
        now = time.time()
        with self._db() as db:
            if ident and not db.execute('SELECT id FROM characters WHERE id=? AND user=?', (ident, user)).fetchone():
                return None
            refs = self._associations(db, user, 'assets', fields.get('reference_asset_ids', []))
            if any(db.execute('SELECT kind FROM assets WHERE id=?', (ref,)).fetchone()[0] != 'image' for ref in refs):
                raise ValueError('Character identity references must be images. Associate video and 3D artwork from asset details.')
            if ident:
                db.execute('UPDATE characters SET ' + ','.join(k + '=?' for k in CHARACTER_FIELDS)
                           + ', updated_at=? WHERE id=? AND user=?',
                           (*[values[k] for k in CHARACTER_FIELDS], now, ident, user))
                db.execute('DELETE FROM character_references WHERE character_id=?', (ident,))
            else:
                ident = uuid.uuid4().hex
                db.execute('INSERT INTO characters VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (ident, user, *[values[k] for k in CHARACTER_FIELDS], now, now))
            db.executemany('INSERT INTO character_references VALUES (?,?,?)',
                           [(r, ident, position) for position, r in enumerate(refs)])
            db.executemany('INSERT OR IGNORE INTO references_to_characters VALUES (?,?,?)',
                           [(r, ident, position) for position, r in enumerate(refs)])
            return self._character(db, db.execute('SELECT * FROM characters WHERE id=?', (ident,)).fetchone())

    def get_character(self, user: str, ident: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute('SELECT * FROM characters WHERE id=? AND user=?', (ident, user)).fetchone()
            return self._character(db, row) if row else None

    def list_characters(self, user: str, *, q: str = '', project: str = '', offset: int = 0, limit: int = 100):
        return self._list('characters', user, q=q, project=project, offset=offset, limit=limit)

    def delete_character(self, user: str, ident: str) -> bool:
        with self._db() as db:
            return db.execute('DELETE FROM characters WHERE id=? AND user=?', (ident, user)).rowcount > 0

    def add_asset(self, user: str, data: bytes, *, filename: str, name: str = '', description: str = '',
                  project: str = '', tags: list[str] | None = None, character_ids: list[str] | None = None,
                  provenance: dict | None = None) -> dict[str, Any]:
        kind, mime = validate(data, filename)
        title = _text(name or filename, 'name', 200, required=True)
        description = _text(description, 'description')
        project = _text(project, 'project', 500)
        tags_json = json.dumps(_tags(tags or []), ensure_ascii=False)
        provenance_json = json.dumps(_provenance(provenance or {'source': 'upload'}), ensure_ascii=False)
        ident, now = uuid.uuid4().hex, time.time()
        blob = ident + Path(filename).suffix.lower()
        target = self.blobs / blob
        temporary = None
        try:
            with self._db() as db:
                refs = self._associations(db, user, 'characters', character_ids or [])
                fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=self.blobs)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                temporary = None
                db.execute('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    ident, user, title, description, kind, mime, filename, blob, len(data),
                    hashlib.sha256(data).hexdigest(), project, tags_json, provenance_json, now, now,
                ))
                for character in refs:
                    position = db.execute(
                        'SELECT COALESCE(MAX(position), -1) + 1 FROM references_to_characters WHERE character_id=?',
                        (character,),
                    ).fetchone()[0]
                    db.execute('INSERT INTO references_to_characters VALUES (?,?,?)', (ident, character, position))
                return self._asset(db, db.execute('SELECT * FROM assets WHERE id=?', (ident,)).fetchone())
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    def get_asset(self, user: str, ident: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute('SELECT * FROM assets WHERE id=? AND user=?', (ident, user)).fetchone()
            return self._asset(db, row) if row else None

    def asset_path(self, user: str, ident: str) -> Path | None:
        with self._db() as db:
            row = db.execute('SELECT blob FROM assets WHERE id=? AND user=?', (ident, user)).fetchone()
        if not row or not re.fullmatch(r'[0-9a-f]{32}\.[a-z0-9]+', row['blob']):
            return None
        path = self.blobs / row['blob']
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.blobs.resolve()):
            return None
        return path

    def update_asset(self, user: str, ident: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute('SELECT * FROM assets WHERE id=? AND user=?', (ident, user)).fetchone()
            if row is None:
                return None
            if any(k not in (*ASSET_FIELDS, 'character_ids') for k in fields):
                raise ValueError('Only the name, description, project, tags and character associations may be edited.')
            assignments, values = [], []
            for key in ASSET_FIELDS:
                if key not in fields:
                    continue
                value = (json.dumps(_tags(fields[key]), ensure_ascii=False) if key == 'tags'
                         else _text(fields[key], key, 200 if key == 'name' else 500 if key == 'project' else 20000,
                                    required=key == 'name'))
                assignments.append(key + '=?')
                values.append(value)
            if 'character_ids' in fields:
                refs = self._associations(db, user, 'characters', fields['character_ids'])
                old_positions = dict(db.execute(
                    'SELECT character_id, position FROM references_to_characters WHERE asset_id=?', (ident,)
                ))
                db.execute('DELETE FROM references_to_characters WHERE asset_id=?', (ident,))
                for removed in old_positions.keys() - set(refs):
                    db.execute('DELETE FROM character_references WHERE asset_id=? AND character_id=?', (ident, removed))
                for character in refs:
                    position = old_positions.get(character)
                    if position is None:
                        position = db.execute(
                            'SELECT COALESCE(MAX(position), -1) + 1 FROM references_to_characters WHERE character_id=?',
                            (character,),
                        ).fetchone()[0]
                    db.execute('INSERT INTO references_to_characters VALUES (?,?,?)', (ident, character, position))
            assignments.append('updated_at=?')
            db.execute('UPDATE assets SET ' + ','.join(assignments) + ' WHERE id=? AND user=?',
                       (*values, time.time(), ident, user))
            return self._asset(db, db.execute('SELECT * FROM assets WHERE id=?', (ident,)).fetchone())

    def list_assets(self, user: str, *, q: str = '', project: str = '', kind: str = '', character_id: str = '',
                    offset: int = 0, limit: int = 100):
        return self._list('assets', user, q=q, project=project, kind=kind, character_id=character_id,
                          offset=offset, limit=limit)

    def _list(self, table, user, *, q='', project='', kind='', character_id='', offset=0, limit=100):
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError('Use a positive limit of at most 1000 and a nonnegative offset.')
        where, args = ['user=?'], [user]
        if q:
            where.append("instr(casefold(name || ' ' || description || ' ' || tags || ' ' || project), casefold(?)) > 0")
            args.append(q)
        if project:
            where.append('project=?')
            args.append(project)
        if kind:
            where.append('kind=?')
            args.append(kind)
        if character_id:
            where.append('id IN (SELECT asset_id FROM references_to_characters WHERE character_id=?)')
            args.append(character_id)
        condition = ' AND '.join(where)
        with self._db() as db:
            total = db.execute(f'SELECT COUNT(*) FROM {table} WHERE {condition}', args).fetchone()[0]
            rows = db.execute(f'SELECT * FROM {table} WHERE {condition} ORDER BY updated_at DESC, id LIMIT ? OFFSET ?',
                              (*args, limit, offset))
            convert = self._character if table == 'characters' else self._asset
            return {table: [convert(db, r) for r in rows], 'total': total, 'offset': offset}

    def delete_asset(self, user: str, ident: str) -> bool:
        path = self.asset_path(user, ident)
        with self._db() as db:
            deleted = db.execute('DELETE FROM assets WHERE id=? AND user=?', (ident, user)).rowcount > 0
        if deleted and path:
            path.unlink(missing_ok=True)
        return deleted

    def export(self, user: str) -> dict[str, Any]:
        with self._db() as db:
            return {
                'version': 1, 'exported_at': time.time(),
                'characters': [self._character(db, r) for r in db.execute(
                    'SELECT * FROM characters WHERE user=? ORDER BY created_at, id', (user,))],
                'assets': [self._asset(db, r) for r in db.execute(
                    'SELECT * FROM assets WHERE user=? ORDER BY created_at, id', (user,))],
            }

    def recipe(self, user: str, ident: str) -> dict[str, Any] | None:
        character = self.get_character(user, ident)
        if not character:
            return None
        references = [self.get_asset(user, i) for i in character['reference_asset_ids']]
        images = [a for a in references if a and a['kind'] == 'image']
        return {
            'character': character, 'character_id': ident,
            'prompt': '\n'.join(character[k] for k in ('description', 'appearance_prompt', 'consistency_notes') if character[k]),
            'negative_prompt': character['negative_prompt'],
            'reference_asset_ids': [a['id'] for a in images], 'references': images,
            'reference_media_ids': [],
        }

    def prepare(self, user: str, ident: str, media_store) -> dict[str, Any] | None:
        recipe = self.recipe(user, ident)
        if recipe is None:
            return None
        media_ids = []
        for asset in recipe['references']:
            with self._db() as db:
                row = db.execute('SELECT media_id FROM prepared_media WHERE asset_id=?', (asset['id'],)).fetchone()
            media_id = row[0] if row else None
            path = media_store.path(media_id) if media_id else None
            if not (path and path.is_file()):
                path = self.asset_path(user, asset['id'])
                if path is None:
                    raise InvalidAsset('A character reference file is missing. Restore or remove it before generating.')
                media = media_store.add(
                    path.read_bytes(), kind='image', media_type=asset['media_type'],
                    prompt=asset['name'], source='upload',
                    params={'creative_asset_id': asset['id'], 'character_id': ident},
                )
                media_id = media.id
                with self._db() as db:
                    db.execute('INSERT OR REPLACE INTO prepared_media VALUES (?,?)', (asset['id'], media_id))
            media_ids.append(media_id)
        return {**recipe, 'reference_media_ids': media_ids, 'image': media_ids[0] if media_ids else None}
