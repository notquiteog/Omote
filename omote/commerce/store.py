"""Revisioned project records and a durable at-most-once outgoing ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from omote.commerce.models import MODELS, Listing, check_listing


def user_dir():
    from omote.config import config
    return config.data_dir / 'commerce' / hashlib.sha256(config.default_user.encode()).hexdigest()


def project_store(root: Path):
    return Store(user_dir() / (hashlib.sha256(str(root.resolve()).encode()).hexdigest() + '.db'))


class Store:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.executescript('''
            CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, revision INTEGER NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rules (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        ''')
        db.execute('ATTACH DATABASE ? AS outbox', (str(self.path.parent / 'outgoing.sqlite3'),))
        db.execute('CREATE TABLE IF NOT EXISTS outbox.outgoing (key TEXT PRIMARY KEY, scope TEXT NOT NULL, rule_id TEXT NOT NULL, conversation TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, data TEXT NOT NULL)')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def row(row):
        return {k: row[k] for k in ('id', 'kind', 'revision', 'updated')} | {'fields': json.loads(row['data'])}

    def get(self, ident):
        if not self.path.exists():
            raise ValueError('No such record in this project.')
        with self.connect() as db:
            row = db.execute('SELECT * FROM records WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('No such record in this project.')
        return self.row(row)

    def list(self):
        if not self.path.exists():
            return []
        with self.connect() as db:
            rows = db.execute('SELECT * FROM records ORDER BY updated DESC LIMIT 200').fetchall()
        return [self.row(row) for row in rows]

    def save(self, kind, fields, ident=None, base_revision=None):
        if kind not in MODELS:
            raise ValueError('Choose supplier or listing.')
        item = MODELS[kind].model_validate(fields)
        value = item.model_dump(mode='json')
        if kind == 'supplier' and item.status != 'researching' and not item.evidence:
            raise ValueError('Save the observed communication or agreement evidence before changing status.')
        if kind == 'listing':
            if item.supplier_id and self.get(item.supplier_id)['kind'] != 'supplier':
                raise ValueError('Choose a supplier saved in this project.')
            if item.status == 'ready' and not check_listing(item)['ready']:
                raise ValueError('Complete the listing checks before marking it ready.')
            if item.status in {'published', 'sold', 'ended'} and (not item.published_url or not item.confirmation):
                raise ValueError('Record the real listing URL and observed confirmation before claiming an external outcome.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if ident:
                old = db.execute('SELECT * FROM records WHERE id=?', (ident,)).fetchone()
                if not old or old['kind'] != kind or old['revision'] != base_revision:
                    raise ValueError('This record changed. Reload it before saving.')
                revision = old['revision'] + 1
            else:
                ident, revision = uuid.uuid4().hex, 1
            db.execute('INSERT INTO records VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,updated=excluded.updated,data=excluded.data',
                       (ident, kind, revision, time.time(), json.dumps(value)))
        result = self.get(ident)
        if kind == 'listing':
            result['checks'] = check_listing(Listing.model_validate(value))
        return result

    def delete(self, ident, revision):
        with self.connect() as db:
            if not db.execute('DELETE FROM records WHERE id=? AND revision=?', (ident, revision)).rowcount:
                raise ValueError('This record changed. Reload it before deleting.')

    def rules(self):
        if not self.path.exists():
            return []
        with self.connect() as db:
            return [json.loads(row['data']) for row in db.execute('SELECT data FROM rules ORDER BY id')]

    def put_rule(self, rule):
        with self.connect() as db:
            db.execute('INSERT INTO rules VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (rule['id'], json.dumps(rule)))

    def claim(self, key, rule_id, conversation, data, daily_limit=50, expected_rule=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if rule_id:
                rule = db.execute('SELECT data FROM rules WHERE id=?', (rule_id,)).fetchone()
                rule = json.loads(rule['data']) if rule else {}
                if not rule.get('enabled') or rule.get('expires', 0) <= time.time() or rule != expected_rule:
                    return False
                if db.execute('SELECT 1 FROM outbox.outgoing WHERE rule_id=? AND conversation=?', (rule_id, conversation)).fetchone():
                    return False
                count = db.execute('SELECT COUNT(*) FROM outbox.outgoing WHERE rule_id=? AND created>?', (rule_id, time.time() - 86400)).fetchone()[0]
                if count >= daily_limit:
                    return False
            # Persist BEFORE network I/O. A crash or timeout remains uncertain;
            # no automatic retries can send twice, including after restart.
            return bool(db.execute('INSERT OR IGNORE INTO outbox.outgoing VALUES (?,?,?,?,?,?,?)',
                                   (key, self.path.name, rule_id, conversation, 'uncertain', time.time(), json.dumps(data))).rowcount)

    def finish(self, key, state, data):
        with self.connect() as db:
            db.execute('UPDATE outbox.outgoing SET state=?, data=? WHERE key=? AND scope=?', (state, json.dumps(data), key, self.path.name))

    def activity(self):
        if not self.path.exists():
            return []
        with self.connect() as db:
            return [dict(row) | {'data': json.loads(row['data'])} for row in db.execute('SELECT * FROM outbox.outgoing WHERE scope=? ORDER BY created DESC LIMIT 50', (self.path.name,))]
