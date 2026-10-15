"""Persistent transfers with validated ranges, atomic promotion and SHA-256."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import os
import re
import shutil
import socket
import sqlite3
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp

from omote.local_models.catalog import MARGIN, Artifact
from omote.net.transport import Transport


class ModelError(ValueError):
    pass


class PublicResolver(aiohttp.resolver.ThreadedResolver):
    """Validate addresses used by the actual connection, preventing DNS rebinding."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        rows = await super().resolve(host, port, family)
        if not rows or any(not ipaddress.ip_address(row['host']).is_global for row in rows):
            raise ModelError('Download DNS resolved to a private or local network address.')
        return rows


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class DownloadStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.parts = self.root / 'partial'
        self.files = self.root / 'files'
        self.parts.mkdir(exist_ok=True)
        self.files.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / 'models.db')
        if os.name != 'nt':
            (self.root / 'models.db').chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, spec TEXT NOT NULL, state TEXT NOT NULL, '
                        'downloaded INTEGER NOT NULL DEFAULT 0, etag TEXT NOT NULL DEFAULT "", error TEXT NOT NULL DEFAULT "")')
        self.db.execute('UPDATE artifacts SET state="cancelled",error="Interrupted. Resume to continue." '
                        'WHERE state IN ("downloading", "verifying", "installing")')
        self.db.commit()

    def add(self, spec: Artifact):
        try:
            existing = self.get(spec.id)
        except KeyError:
            existing = None
        if existing:
            identity = ('kind', 'url', 'size', 'sha256', 'category', 'filename')
            if any(getattr(existing['spec'], name) != getattr(spec, name) for name in identity):
                raise ModelError('An artifact with this ID already has different download metadata.')
            # Display labels and memory estimates can improve without losing
            # verified files or invalidating resumable byte identity.
            self.db.execute('UPDATE artifacts SET spec=? WHERE id=?', (spec.model_dump_json(), spec.id))
            self.db.commit()
            return
        self.db.execute('INSERT OR IGNORE INTO artifacts(id,spec,state) VALUES(?,?,?)',
                        (spec.id, spec.model_dump_json(), 'available'))
        self.db.commit()

    def get(self, ident: str):
        row = self.db.execute('SELECT * FROM artifacts WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        result = dict(row)
        result['spec'] = Artifact.model_validate_json(result['spec'])
        return result

    def all(self):
        return [self.get(row[0]) for row in self.db.execute('SELECT id FROM artifacts ORDER BY rowid')]

    def update(self, ident: str, **values):
        if not values or not set(values) <= {'state', 'downloaded', 'etag', 'error'}:
            raise ValueError('Invalid transfer update')
        fields = ','.join(f'{key}=?' for key in values)
        self.db.execute(f'UPDATE artifacts SET {fields} WHERE id=?', [*values.values(), ident])
        self.db.commit()

    def partial(self, spec: Artifact) -> Path:
        return self.parts / f'{spec.id}.part'

    def final(self, spec: Artifact) -> Path:
        if spec.kind == 'safetensors':
            directory = self.root / 'models' / spec.category
            directory.mkdir(parents=True, exist_ok=True)
            return directory / (spec.filename or f'{spec.id}.safetensors')
        suffix = {'model': '.gguf', 'runtime': '.archive', 'workflow': '.json'}[spec.kind]
        return self.files / (spec.id + suffix)


class Downloads:
    def __init__(self, store: DownloadStore, *, allow_loopback=False):
        self.store = store
        self.allow_loopback = allow_loopback  # test injection, never an API/config flag
        self.active: str | None = None
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.after_download = None
        self.resumed_from = 0
        self.transport = Transport(timeout=24 * 3600)

    async def validate_url(self, url: str):
        parsed = urlsplit(url)
        if parsed.username or parsed.password or not parsed.hostname:
            raise ModelError('Download URLs cannot contain credentials.')
        if self.allow_loopback and parsed.hostname in ('localhost', '127.0.0.1') and parsed.scheme == 'http':
            return
        if parsed.scheme != 'https':
            raise ModelError('Downloads require a public HTTPS URL.')
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443, 0, socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
            raise ModelError('Downloads cannot reach private or local network addresses.')

    def disk_check(self, spec: Artifact):
        partial = self.store.partial(spec)
        done = partial.stat().st_size if partial.exists() else 0
        final = self.store.final(spec)
        remaining = 0 if final.exists() and final.stat().st_size == spec.size else max(0, spec.size - done)
        unpack = 512 * 1024**2 if spec.kind == 'runtime' else 0
        free = shutil.disk_usage(self.store.root).free
        needed = remaining + unpack + MARGIN
        return {'free_bytes': free, 'required_bytes': needed, 'remaining_bytes': remaining,
                'reserve_bytes': MARGIN, 'ok': free >= needed}

    async def start(self, ident: str):
        async with self.lock:
            row = self.store.get(ident)
            if self.task and not self.task.done():
                raise ModelError('Another download is active. Cancel it first or wait for it to finish.')
            if row['state'] == 'ready':
                return
            if not self.disk_check(row['spec'])['ok']:
                raise ModelError('Not enough free disk space, including the safety reserve.')
            await self.validate_url(row['spec'].url)
            self.active = ident
            self.resumed_from = 0
            self.store.update(ident, state='downloading', error='')
            self.task = asyncio.create_task(self._run(row['spec']))

    async def cancel(self, ident: str):
        self.store.get(ident)
        if self.active == ident and self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        elif self.store.get(ident)['state'] != 'ready':
            self.store.update(ident, state='cancelled', error='Partial file kept. Resume to continue.')

    async def close(self):
        if self.active:
            await self.cancel(self.active)

    async def _run(self, spec: Artifact):
        try:
            path = self.store.final(spec)
            if not path.exists():
                await self._transfer(spec)
                path = self.store.partial(spec)
            self.store.update(spec.id, state='verifying')
            work = asyncio.create_task(asyncio.to_thread(checksum, path))
            try:
                digest = await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                raise
            if path.stat().st_size != spec.size or digest != spec.sha256:
                path.unlink(missing_ok=True)
                self.store.update(spec.id, downloaded=0, etag='')
                raise ModelError('Checksum or size mismatch. The unverified file was removed; retry the download.')
            if spec.kind == 'model':
                with path.open('rb') as stream:
                    header = stream.read(8)
                if header[:4] != b'GGUF' or int.from_bytes(header[4:], 'little') not in (2, 3):
                    path.unlink(missing_ok=True)
                    self.store.update(spec.id, downloaded=0, etag='')
                    raise ModelError('This file is not a supported GGUF v2/v3 model.')
            if spec.kind in ('safetensors', 'workflow'):
                from omote.local_models.comfy import validate_file

                try:
                    validate_file(path, spec.kind)
                except (ValueError, OSError) as error:
                    path.unlink(missing_ok=True)
                    self.store.update(spec.id, downloaded=0, etag='')
                    raise ModelError(str(error)) from error
            if path != self.store.final(spec):
                path.replace(self.store.final(spec))
            if self.after_download:
                await self.after_download(spec)
            self.store.update(spec.id, state='ready', downloaded=spec.size, error='')
        except asyncio.CancelledError:
            self.store.update(spec.id, state='cancelled', error='Partial file kept. Resume to continue.')
            raise
        except Exception as error:
            # Do not persist signed redirect URLs or credentials in exceptions.
            message = str(error) if isinstance(error, ModelError) else f'{type(error).__name__}: download interrupted. Resume to retry.'
            self.store.update(spec.id, state='error', error=message)
        finally:
            self.active = None

    async def _transfer(self, spec: Artifact):
        path = self.store.partial(spec)
        offset = path.stat().st_size if path.exists() else 0
        if offset > spec.size:
            path.unlink()
            offset = 0
        if offset == spec.size:
            return
        etag = self.store.get(spec.id)['etag']
        headers = {'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
            if etag:
                headers['If-Range'] = etag
        resolver = None if self.allow_loopback else PublicResolver()
        async with self.transport.session(resolver=resolver) as session:
            url = spec.url
            for _ in range(6):
                await self.validate_url(url)
                response = await session.get(url, headers=headers, allow_redirects=False,
                                             timeout=aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=30))
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    response.release()
                    if not location:
                        raise ModelError('Download redirect had no destination.')
                    url = urljoin(url, location)
                    continue
                break
            else:
                raise ModelError('Too many download redirects.')
            async with response:
                if response.status not in (200, 206):
                    raise ModelError(f'Download server returned HTTP {response.status}. Resume to retry.')
                if response.headers.get('Content-Encoding', 'identity') != 'identity':
                    raise ModelError('Compressed transfer rejected; model byte ranges must remain exact.')
                if response.status == 206:
                    expected = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if not expected or int(expected[1]) != offset or int(expected[3]) != spec.size or int(expected[2]) != spec.size - 1:
                        raise ModelError('Invalid resume range returned by server. Partial file was kept.')
                    self.resumed_from = offset
                elif offset:
                    # Range ignored or validator changed: replace, never append.
                    offset = 0
                length = response.content_length
                if length is not None and length != spec.size - offset:
                    raise ModelError('Server download size differs from the verified artifact metadata.')
                self.store.update(spec.id, etag=response.headers.get('ETag', ''), downloaded=offset)
                last_saved = time.monotonic()
                with path.open('ab' if offset else 'wb') as out:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        if offset + len(chunk) > spec.size:
                            raise ModelError('Download exceeded its expected size.')
                        if shutil.disk_usage(self.store.root).free < len(chunk) + MARGIN:
                            raise ModelError('Disk space dropped below the safety reserve. Partial file kept.')
                        out.write(chunk)
                        offset += len(chunk)
                        if time.monotonic() - last_saved > .25:
                            out.flush()
                            self.store.update(spec.id, downloaded=offset)
                            last_saved = time.monotonic()
                    out.flush()
                    os.fsync(out.fileno())
                self.store.update(spec.id, downloaded=offset)
                if offset != spec.size:
                    raise ModelError('Download ended early. Resume to continue from the saved partial file.')
