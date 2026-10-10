"""Durable local schedules. Every execution is a normal, inspectable agent chat."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

log = logging.getLogger(__name__)
ACTIVE = ('starting', 'running', 'waiting')


class Schedule(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=32000)
    root: str = Field(min_length=1, max_length=4096)
    cadence: Literal['manual', 'once', 'interval', 'daily', 'weekly'] = 'daily'
    timezone: str = 'UTC'
    at: str = '09:00'
    weekdays: list[int] = Field(default_factory=lambda: [0], min_length=1, max_length=7)
    interval_minutes: int = Field(default=60, ge=1, le=525600)
    start_at: datetime | None = None
    enabled: bool = True
    provider: str = Field(default='', max_length=200)
    model: str = Field(default='', max_length=300)
    mode: Literal['read_only', 'ask', 'auto_edit', 'trusted'] = 'ask'
    tools: list[str] = Field(default_factory=list, max_length=100)
    max_runtime_minutes: int = Field(default=60, ge=1, le=1440)

    @field_validator('timezone')
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('Choose a valid IANA time zone, such as Asia/Tokyo.') from exc
        return value

    @field_validator('at')
    @classmethod
    def clock(cls, value):
        if len(value) != 5 or value[2] != ':':
            raise ValueError('Use a 24-hour time such as 09:00.')
        try:
            datetime.strptime(value, '%H:%M')
        except ValueError as exc:
            raise ValueError('Use a valid 24-hour time.') from exc
        return value

    @field_validator('weekdays')
    @classmethod
    def days(cls, values):
        if any(day < 0 or day > 6 for day in values):
            raise ValueError('Weekdays run from Monday (0) to Sunday (6).')
        return sorted(set(values))

    @model_validator(mode='after')
    def once(self):
        if self.cadence == 'once':
            if self.start_at is None or self.start_at.utcoffset() is None:
                raise ValueError('A one-time task needs a date and time with a time zone offset.')
        return self


def next_time(spec: Schedule, after: float) -> float | None:
    if not spec.enabled or spec.cadence == 'manual':
        return None
    if spec.cadence == 'once':
        stamp = spec.start_at.timestamp()
        return stamp if stamp > after else None
    if spec.cadence == 'interval':
        return after + spec.interval_minutes * 60
    zone = ZoneInfo(spec.timezone)
    today = datetime.fromtimestamp(after, UTC).astimezone(zone).date()
    hour, minute = map(int, spec.at.split(':'))
    for offset in range(9):
        day = today + timedelta(days=offset)
        if spec.cadence == 'weekly' and day.weekday() not in spec.weekdays:
            continue
        # fold=0 executes repeated wall times once. Nonexistent spring times
        # normalize forward by the DST gap when converted through UTC.
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=0).timestamp()
        if candidate > after:
            return candidate
    raise ValueError('Could not calculate the next scheduled time.')


class ScheduleStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.runner_lock = None
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY, spec TEXT NOT NULL, next_run REAL,
                created REAL NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schedule_runs (
                id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                status TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '',
                started REAL NOT NULL, finished REAL, summary TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS schedule_run_history ON schedule_runs(schedule_id, started DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS schedule_one_active ON schedule_runs(schedule_id)
                WHERE status IN ('starting','running','waiting');
        """)

    @staticmethod
    def job(row):
        return {**dict(row), 'spec': json.loads(row['spec'])} if row else None

    def get(self, ident):
        return self.job(self.db.execute('SELECT * FROM schedules WHERE id=?', (ident,)).fetchone())

    def list(self):
        return [self.job(row) for row in self.db.execute('SELECT * FROM schedules ORDER BY created DESC')]

    def save(self, spec: Schedule, ident: str | None = None, *, now: float | None = None):
        now = time.time() if now is None else now
        old = self.get(ident) if ident else None
        if ident and not old:
            raise KeyError(ident)
        timing = ('cadence', 'timezone', 'at', 'weekdays', 'interval_minutes', 'start_at', 'enabled')
        payload = spec.model_dump(mode='json')
        following = (
            old['next_run'] if old and all(old['spec'][k] == payload[k] for k in timing) else next_time(spec, now)
        )
        if (
            spec.enabled
            and spec.cadence == 'once'
            and following is None
            and (not old or old['spec']['start_at'] != payload['start_at'])
        ):
            raise ValueError('Choose a future date for a one-time task.')
        ident = ident or uuid.uuid4().hex
        self.db.execute(
            'INSERT INTO schedules VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET spec=excluded.spec,next_run=excluded.next_run,updated=excluded.updated',
            (ident, spec.model_dump_json(), following, old['created'] if old else now, now),
        )
        return self.get(ident)

    def runs(self, ident: str | None = None, limit: int = 100):
        if ident:
            rows = self.db.execute(
                'SELECT * FROM schedule_runs WHERE schedule_id=? ORDER BY started DESC LIMIT ?', (ident, limit)
            )
        else:
            rows = self.db.execute('SELECT * FROM schedule_runs ORDER BY started DESC LIMIT ?', (limit,))
        return [{**dict(row), 'snapshot': json.loads(row['snapshot'])} for row in rows]

    def run(self, ident):
        row = self.db.execute('SELECT * FROM schedule_runs WHERE id=?', (ident,)).fetchone()
        return {**dict(row), 'snapshot': json.loads(row['snapshot'])} if row else None

    def update_run(self, ident, **changes):
        if not changes or not set(changes) <= {'status', 'session_id', 'finished', 'summary', 'error'}:
            raise ValueError('Invalid run update')
        fields = ','.join(f'{key}=?' for key in changes)
        self.db.execute(f'UPDATE schedule_runs SET {fields} WHERE id=?', (*changes.values(), ident))

    def claim(self, ident, *, manual=False, now=None):
        now = time.time() if now is None else now
        self.db.execute('BEGIN IMMEDIATE')
        try:
            job = self.get(ident)
            if not job:
                raise KeyError(ident)
            if self.db.execute(
                "SELECT 1 FROM schedule_runs WHERE schedule_id=? AND status IN ('starting','running','waiting')",
                (ident,),
            ).fetchone():
                raise ValueError('This task is already running or waiting for your input.')
            if not manual and (job['next_run'] is None or job['next_run'] > now):
                return None
            spec = Schedule(**job['spec'])
            run_id = uuid.uuid4().hex
            self.db.execute(
                'INSERT INTO schedule_runs (id,schedule_id,snapshot,status,started) VALUES (?,?,?,?,?)',
                (run_id, ident, spec.model_dump_json(), 'starting', now),
            )
            if not manual:
                self.db.execute(
                    'UPDATE schedules SET next_run=?,updated=? WHERE id=?', (next_time(spec, now), now, ident)
                )
            return self.run(run_id)
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        finally:
            if self.db.in_transaction:
                self.db.execute('COMMIT')

    def delete(self, ident):
        if self.db.execute(
            "SELECT 1 FROM schedule_runs WHERE schedule_id=? AND status IN ('starting','running','waiting')", (ident,)
        ).fetchone():
            raise ValueError('Stop the active run before deleting this task.')
        if not self.db.execute('DELETE FROM schedules WHERE id=?', (ident,)).rowcount:
            raise KeyError(ident)
        # Explicit deletion removes task prompts and run metadata; chats remain
        # in the conversation history where users can export or delete them.
        self.db.execute('DELETE FROM schedule_runs WHERE schedule_id=?', (ident,))

    def recover(self):
        for row in self.db.execute(
            "SELECT id,schedule_id FROM schedule_runs WHERE status IN ('starting','running','waiting')"
        ).fetchall():
            self.update_run(
                row['id'],
                status='interrupted',
                finished=time.time(),
                error='Omote restarted during this run. Review its chat before running again.',
            )
            job = self.get(row['schedule_id'])
            if job:
                self.save(Schedule(**{**job['spec'], 'enabled': False}), job['id'])

    def lock_runner(self):
        import os

        handle = open(self.path.with_suffix('.lock'), 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt

                if not handle.seek(0, 2):
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError('Another Omote scheduler is already using this data folder.') from exc
        self.runner_lock = handle

    def close(self):
        self.db.close()
        if self.runner_lock:
            self.runner_lock.close()
            self.runner_lock = None


class Scheduler:
    def __init__(self, store: ScheduleStore, create_session, *, finish_session=None, max_concurrent=3):
        self.store = store
        self.create_session = create_session
        self.finish_session = finish_session
        self.max_concurrent = max_concurrent
        self.active: dict[str, asyncio.Task] = {}
        self.sessions: dict[str, Any] = {}
        self.loop_task = None
        self.closed = False

    def start(self):
        self.store.lock_runner()
        self.store.recover()
        self.loop_task = asyncio.create_task(self._loop())

    async def _loop(self):
        while True:
            try:
                self.tick()
            except Exception:
                log.exception('Schedule check failed')
            await asyncio.sleep(5)

    def tick(self, now=None):
        now = time.time() if now is None else now
        for job in self.store.list():
            if len(self.active) >= self.max_concurrent:
                break
            if job['next_run'] is not None and job['next_run'] <= now:
                try:
                    self.launch(job['id'], manual=False, now=now)
                except ValueError:
                    pass  # An earlier run still owns this schedule.

    def launch(self, ident, *, manual=True, now=None):
        if self.closed:
            raise ValueError('Omote is shutting down. Try again after restarting.')
        if len(self.active) >= self.max_concurrent:
            raise ValueError('All scheduled worker slots are busy. Try again when a run finishes.')
        run = self.store.claim(ident, manual=manual, now=now)
        if run:
            self.active[run['id']] = asyncio.create_task(self._execute(run))
        return run

    async def _execute(self, run):
        ident = run['id']
        spec = Schedule(**run['snapshot'])
        session = None
        try:
            async with asyncio.timeout(spec.max_runtime_minutes * 60):
                session = await self.create_session(spec)
                self.sessions[ident] = session
                self.store.update_run(ident, session_id=session.id, status='running')
                since = session.seq
                session.submit(spec.prompt)
                async for event in session.events(since=since):
                    kind = getattr(event, 'type', '')
                    if (
                        kind == 'task.updated'
                        and not session.busy
                        and session.has_background_results
                        and not session.tasks.running
                    ):
                        session.submit(
                            'Background work has finished. Use its results to complete the scheduled task and report the outcome.'
                        )
                    if kind == 'turn.completed' and not getattr(event, 'agent', ''):
                        if event.stop_reason == 'end_turn' and session.has_background_results:
                            session.submit(
                                'Use the completed background work to finish the scheduled task and report the outcome.'
                            )
                            continue
                        if event.stop_reason == 'end_turn' and (
                            (session.tasks is not None and session.tasks.running) or session.queued
                        ):
                            continue
                        status = (
                            'completed'
                            if event.stop_reason == 'end_turn'
                            else 'interrupted'
                            if event.stop_reason == 'interrupted'
                            else 'failed'
                        )
                        summary = '\n'.join(
                            block.text
                            for msg in session.messages
                            if msg.role == 'assistant'
                            for block in msg.content
                            if hasattr(block, 'text')
                        )[-12000:]
                        self.store.update_run(
                            ident,
                            status=status,
                            finished=time.time(),
                            summary=summary,
                            error='' if status == 'completed' else f'Run ended: {event.stop_reason}. Review its chat.',
                        )
                        return
                    if kind == 'session.ended':
                        self.store.update_run(
                            ident, status='interrupted', finished=time.time(), error='The run’s chat was closed.'
                        )
                        return
                    if kind == 'question.asked' or (
                        kind == 'tool.proposed' and getattr(event, 'needs_approval', False)
                    ):
                        self.store.update_run(ident, status='waiting')
                    elif kind in ('tool.completed', 'tool.denied', 'text.delta'):
                        self.store.update_run(ident, status='running')
                self.store.update_run(
                    ident, status='interrupted', finished=time.time(), error='The chat event stream ended.'
                )
        except asyncio.CancelledError:
            if session:
                session.interrupt()
            self.store.update_run(
                ident,
                status='interrupted' if self.closed else 'cancelled',
                finished=time.time(),
                error='Omote shut down during this run.' if self.closed else 'Stopped by you.',
            )
            raise
        except TimeoutError:
            if session:
                session.interrupt()
            self.store.update_run(
                ident,
                status='failed',
                finished=time.time(),
                error='Run reached its time limit. Review the chat before retrying.',
            )
        except Exception:
            # Provider errors can contain credentials; full details belong to
            # the normal session error channel, never the scheduler listing.
            log.warning('Scheduled run %s failed to start or continue', ident)
            self.store.update_run(
                ident,
                status='failed',
                finished=time.time(),
                error='Could not complete this run. Check its chat, provider and project folder.',
            )
        finally:
            if session and self.finish_session:
                try:
                    await self.finish_session(session)
                except Exception:
                    log.warning('Scheduled chat %s could not be fully closed', session.id)
            self.active.pop(ident, None)
            self.sessions.pop(ident, None)

    async def stop_run(self, ident):
        task = self.active.get(ident)
        if not task:
            raise ValueError('This run is no longer active.')
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # Cancellation before the coroutine’s first instruction still needs a record.
        if self.store.run(ident)['status'] in ACTIVE:
            self.store.update_run(
                ident,
                status='interrupted' if self.closed else 'cancelled',
                finished=time.time(),
                error='Omote shut down during this run.' if self.closed else 'Stopped by you.',
            )
        self.active.pop(ident, None)

    async def stop(self):
        self.closed = True
        if self.loop_task:
            self.loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.loop_task
        for ident in list(self.active):
            await self.stop_run(ident)
