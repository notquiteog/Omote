"""Verified runtime installation and lifecycle for Omote's own server only."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import os
import re
import secrets
import shutil
import socket
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

import psutil

from omote.agent.tools.shell import _kill_tree, _spawn_kwargs
from omote.local_models.catalog import CONTEXT, Artifact
from omote.local_models.downloads import ModelError, checksum
from omote.net.transport import Transport
from omote.providers.base import Modality, ProviderRefused
from omote.providers.connections import Connection, build

PROVIDER_ID = 'managed-local'
MAX_UNPACKED = 512 * 1024**2


class ManagedChat:
    """Existing chats follow a restarted owned runtime, never another model."""

    managed_context_window = CONTEXT

    def __init__(self, runtime):
        self.runtime = runtime

    async def models(self):
        runtime = self.runtime
        return [{'id': runtime.model_id, 'context_window': CONTEXT}] if runtime.state == 'running' else []

    async def stream(self, request):
        runtime = self.runtime
        if runtime.state != 'running' or request.model != runtime.model_id:
            raise ProviderRefused('This local model is stopped. Start the same model in System → Local models to continue.')
        implementation = runtime.implementation
        async for event in implementation.stream(request):
            yield event


def install_archive(archive: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='unpack-', dir=target.parent))
    try:
        total = 0
        links = []

        def destination(name: str, size: int) -> Path:
            nonlocal total
            normalized = name.replace('\\', '/')
            parts = PurePosixPath(normalized)
            if parts.is_absolute() or '..' in parts.parts or ':' in normalized:
                raise ModelError('Unsafe path in runtime archive.')
            path = staging.joinpath(*parts.parts)
            total += size
            if total > MAX_UNPACKED:
                raise ModelError('Runtime archive exceeds its unpacking limit.')
            path.parent.mkdir(parents=True, exist_ok=True)
            return path

        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as source:
                if len(source.infolist()) > 10000:
                    raise ModelError('Runtime archive has too many entries.')
                for member in source.infolist():
                    if member.is_dir():
                        continue
                    if (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ModelError('Runtime ZIP may not contain symbolic links.')
                    path = destination(member.filename, member.file_size)
                    with source.open(member) as incoming, path.open('xb') as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
        else:
            with tarfile.open(archive) as source:
                for count, member in enumerate(source):
                    if count >= 10000:
                        raise ModelError('Runtime archive has too many entries.')
                    path = destination(member.name, member.size)
                    if member.isdir():
                        path.mkdir(exist_ok=True)
                    elif member.issym() or member.islnk():
                        base = path.parent if member.issym() else staging
                        link = base.joinpath(member.linkname).resolve()
                        if not link.is_relative_to(staging.resolve()):
                            raise ModelError('Runtime archive link escapes its installation.')
                        links.append((path, link))
                    elif member.isfile():
                        with source.extractfile(member) as incoming, path.open('xb') as outgoing:
                            shutil.copyfileobj(incoming, outgoing)
                    else:
                        raise ModelError('Runtime archive contains a special file.')
                # Materialize safe internal library aliases as ordinary copies.
                # No symlink in the archive can redirect subsequent writes.
                while links:
                    pending = []
                    for path, link in links:
                        if link.is_file():
                            total += link.stat().st_size
                            if total > MAX_UNPACKED:
                                raise ModelError('Runtime library aliases exceed the unpacking limit.')
                            with link.open('rb') as incoming, path.open('xb') as outgoing:
                                shutil.copyfileobj(incoming, outgoing)
                        else:
                            pending.append((path, link))
                    if len(pending) == len(links):
                        raise ModelError('Runtime archive has unresolved library links.')
                    links = pending
        name = 'llama-server.exe' if os.name == 'nt' else 'llama-server'
        binaries = list(staging.rglob(name))
        if len(binaries) != 1:
            raise ModelError('Runtime archive must contain exactly one llama-server executable.')
        binary = binaries[0]
        binary.chmod(0o755)
        relative = binary.relative_to(staging)
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
        return target / relative
    finally:
        if staging.exists():
            shutil.rmtree(staging)


class Runtime:
    def __init__(self, root: Path, registry):
        self.root = root
        self.registry = registry
        self.process = None
        self.pump_task = None
        self.start_task = None
        self.lock = asyncio.Lock()
        self.state = 'stopped'
        self.model_id = None
        self.port = None
        self.key = ''
        self.key_file = None
        self.error = ''
        self.logs = ''
        self.device = ''
        self.implementation = None
        self.transport = Transport(timeout=3)
        self.record = root / 'runtime.json'
        self._recover()

    def _recover(self):
        if not self.record.exists():
            return
        try:
            info = json.loads(self.record.read_text())
            process = psutil.Process(info['pid'])
            key_path = Path(info['key_file']) if info.get('key_file') else None
            credential_matches = (key_path is not None and key_path.resolve().is_relative_to(self.root.resolve()) and
                                  key_path.name.startswith('runtime-key-') and key_path.read_text().strip() == info['key'] and
                                  str(key_path) in process.cmdline()) if key_path else info['key'] in process.cmdline()
            # Only stop an orphan if creation time, executable and unique API
            # key all match. A recycled PID or another user's server is untouched.
            if (abs(process.create_time() - info['created']) < .01 and
                    Path(process.exe()).resolve() == Path(info['executable']).resolve() and
                    credential_matches):
                children = process.children(recursive=True)
                for child in reversed(children):
                    child.kill()
                process.kill()
                psutil.wait_procs([process, *children], timeout=3)
                if key_path:
                    key_path.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, psutil.Error):
            pass
        self.record.unlink(missing_ok=True)

    def snapshot(self):
        if self.state == 'running' and self.process and self.process.returncode is not None:
            self.registry.unregister(PROVIDER_ID)
            self.state = 'error'
            self.error = f'Local runtime exited (code {self.process.returncode}).'
        return {'state': self.state, 'model_id': self.model_id, 'device': self.device,
                'provider_id': PROVIDER_ID if self.state == 'running' else None,
                'error': self.error, 'log_tail': self.logs[-6000:]}

    async def devices(self, binary: Path) -> list[str]:
        proc = await asyncio.create_subprocess_exec(str(binary), '--list-devices',
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                    **_spawn_kwargs())
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        except (TimeoutError, asyncio.CancelledError):
            await _kill_tree(proc)
            await proc.wait()
            raise
        if proc.returncode:
            raise ModelError('Runtime device probe failed. Check GPU drivers and system library compatibility. '
                             + output.decode('utf-8', 'replace')[-1200:])
        text = output.decode('utf-8', 'replace')
        self.logs = (self.logs + 'Device probe:\n' + text)[-200000:]
        # llama.cpp prints `Vulkan0: ...`, `Metal: ...`, or `CUDA0: ...`.
        return [match[1] for line in text.splitlines()
                if (match := re.match(r'^\s*((?:Vulkan|CUDA|ROCm|SYCL)\d+|Metal)\s*:', line))
                and not re.search(r'llvmpipe|lavapipe|software|\bcpu\b', line, re.I)]

    async def start(self, spec: Artifact, path: Path, binary: Path, mode='gpu'):
        async with self.lock:
            if self.state in ('starting', 'running', 'stopping'):
                raise ModelError('Stop the current local runtime before starting another model.')
            self.state, self.error, self.logs = 'starting', '', ''
            self.model_id = spec.id
            self.start_task = asyncio.create_task(self._start(spec, path, binary, mode))

    async def _start(self, spec, path, binary, mode):
        try:
            # Recheck ready files before executing/using them, including after
            # a restart or manual disk change. Never run an unverified model.
            if await asyncio.to_thread(checksum, path) != spec.sha256:
                raise ModelError('Model integrity check failed. Remove it and download again.')
            devices = await self.devices(binary) if mode == 'gpu' else []
            if mode == 'gpu' and not devices:
                raise ModelError('No compatible GPU was found by the installed runtime. Check drivers or choose CPU explicitly.')
            self.device = devices[0] if devices else 'CPU'
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                self.port = sock.getsockname()[1]
            self.key = secrets.token_hex(32)
            self.key_file = self.root / f'runtime-key-{secrets.token_hex(16)}.txt'
            fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(self.key + '\n')
            argv = [str(binary), '-m', str(path), '--alias', spec.id, '--host', '127.0.0.1', '--port', str(self.port),
                    '--api-key-file', str(self.key_file), '-c', str(CONTEXT), '-np', '1', '-ngl', '99' if devices else '0',
                    '--device', self.device if devices else 'none', '--jinja']
            self.process = await asyncio.create_subprocess_exec(*argv, cwd=binary.parent,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, **_spawn_kwargs())
            created = psutil.Process(self.process.pid).create_time()
            payload = {'pid': self.process.pid, 'created': created, 'key': self.key,
                       'key_file': str(self.key_file), 'executable': str(binary.resolve())}
            fd = os.open(self.record, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(payload, stream)
            self.pump_task = asyncio.create_task(self._pump(self.process))
            deadline = time.monotonic() + 120
            async with self.transport.session() as client:
                while time.monotonic() < deadline:
                    if self.process.returncode is not None:
                        raise ModelError('The local runtime exited during startup. See runtime details for the driver or model error.')
                    try:
                        async with client.get(f'http://127.0.0.1:{self.port}/health',
                                              headers={'Authorization': f'Bearer {self.key}'}) as response:
                            if response.status == 200 and (await response.json()).get('status') == 'ok':
                                break
                    except (OSError, ValueError, TimeoutError):
                        pass
                    await asyncio.sleep(.25)
                else:
                    raise ModelError('Local runtime did not become healthy within two minutes.')
            connection = Connection(id=PROVIDER_ID, label=f'Local · {spec.label}', adapter='openai',
                                    base_url=f'http://127.0.0.1:{self.port}/v1', api_key=self.key,
                                    modalities=['chat'], local=True)
            info, implementations = build(connection)
            self.implementation = implementations[Modality.CHAT]
            self.registry.register(info, {Modality.CHAT: ManagedChat(self)})
            self.state = 'running'
        except asyncio.CancelledError:
            await self._terminate()
            self.state = 'stopped'
            raise
        except Exception as error:
            self.error = str(error) if isinstance(error, ModelError) else f'{type(error).__name__}: could not start the local runtime.'
            await self._terminate()
            self.state = 'error'

    async def _pump(self, process):
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        held = ''
        while chunk := await process.stdout.read(4096):
            held = (held + decoder.decode(chunk)).replace(self.key, '[redacted]')
            emit = max(0, len(held) - len(self.key) + 1)
            self.logs = (self.logs + held[:emit])[-200000:]
            held = held[emit:]
        self.logs = (self.logs + (held + decoder.decode(b'', final=True)).replace(self.key, '[redacted]'))[-200000:]
        await process.wait()
        if self.state == 'running' and process is self.process:
            self.registry.unregister(PROVIDER_ID)
            self.state = 'error'
            self.error = f'Local runtime exited (code {process.returncode}).'
            self.record.unlink(missing_ok=True)
            if self.key_file:
                self.key_file.unlink(missing_ok=True)
                self.key_file = None

    async def _terminate(self):
        self.registry.unregister(PROVIDER_ID)
        if self.process and self.process.returncode is None:
            await _kill_tree(self.process)
            await self.process.wait()
        if self.pump_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self.pump_task
        self.process = None
        self.record.unlink(missing_ok=True)
        if self.key_file:
            self.key_file.unlink(missing_ok=True)
            self.key_file = None

    async def stop(self):
        async with self.lock:
            self.state = 'stopping'
            if self.start_task and not self.start_task.done():
                self.start_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.start_task
            await self._terminate()
            self.state, self.model_id, self.device, self.error = 'stopped', None, '', ''
