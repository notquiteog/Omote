"""The shared download library, preflight checks, and managed runtime."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

from omote.local_models.catalog import CATALOG, CONTEXT, GIB, VRAM_OVERHEAD, runtime_artifact
from omote.local_models.downloads import Downloads, DownloadStore, ModelError, checksum
from omote.local_models.runtime import Runtime, install_archive
from omote.resources import monitor


class LocalModels:
    def __init__(self, root: Path, registry, *, allow_loopback=False):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._file_lock = (self.root / 'manager.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt

                self._file_lock.write(b'0')
                self._file_lock.flush()
                self._file_lock.seek(0)
                msvcrt.locking(self._file_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._file_lock.close()
            raise ModelError('Another Omote process is managing this local model directory.') from error
        self.store = DownloadStore(root)
        for spec in CATALOG:
            self.store.add(spec)
        self.runtime_spec = runtime_artifact()
        if self.runtime_spec:
            self.store.add(self.runtime_spec)
        self.downloads = Downloads(self.store, allow_loopback=allow_loopback)
        self.downloads.after_download = self._installed
        self.operations = asyncio.Lock()
        self.runtime = Runtime(self.root, registry)
        self.setup_task = None
        self.setup = {'state': 'idle', 'model_id': None, 'note': '', 'error': '', 'mode': ''}
        self.manifest = self.root / 'installed-runtime.json'
        from omote.local_models.comfy import ComfyAssets

        self.comfy = ComfyAssets(self.root / 'comfy', allow_loopback=allow_loopback)

    def binary(self):
        if not self.runtime_spec:
            return None
        try:
            data = json.loads(self.manifest.read_text())
            path = (self.root / data['path']).resolve()
            if data['id'] == self.runtime_spec.id and path.is_relative_to(self.root / 'runtime') and path.is_file():
                return path
        except (OSError, ValueError, KeyError):
            pass
        return None

    async def _installed(self, spec):
        if spec.kind != 'runtime':
            return
        self.store.update(spec.id, state='installing')
        target = self.root / 'runtime' / spec.id
        work = asyncio.create_task(asyncio.to_thread(install_archive, self.store.final(spec), target))
        try:
            binary = await asyncio.shield(work)
        except asyncio.CancelledError:
            # An extraction thread cannot be cancelled mid-write. Let it finish
            # before another transfer/retry can touch its installation path.
            await work
            raise
        digest = await asyncio.to_thread(checksum, binary)
        payload = {'id': spec.id, 'path': str(binary.relative_to(self.root)), 'sha256': digest}
        temporary = self.manifest.with_suffix('.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(payload, stream)
        temporary.replace(self.manifest)

    def compatibility(self, spec, hardware, mode='gpu'):
        errors, warnings = [], []
        disk = self.downloads.disk_check(spec)
        if not disk['ok']:
            errors.append('Not enough disk space, including the safety reserve.')
        if not self.runtime_spec:
            errors.append('No managed runtime build is available for this operating system and architecture.')
        ram = spec.ram_bytes or int(spec.size * 1.2) + GIB
        if spec.kind == 'model':
            memory = hardware.get('memory')
            if memory and memory['available_bytes'] < ram:
                errors.append('Not enough available RAM for this model estimate. Free memory before continuing.')
            elif not memory:
                warnings.append('Available RAM could not be measured.')
            if mode == 'gpu':
                devices = hardware.get('gpu', {}).get('devices', [])
                free = [gpu['memory_total_bytes'] - gpu['memory_used_bytes'] for gpu in devices
                        if gpu.get('memory_total_bytes') is not None and gpu.get('memory_used_bytes') is not None]
                required_vram = spec.size + VRAM_OVERHEAD
                if free and max(free) < required_vram:
                    errors.append('Not enough free VRAM for the estimated model and context. Free GPU memory or choose CPU.')
                elif not free:
                    warnings.append('GPU memory is unknown. The runtime will check for a supported device at startup.')
        return {'ok': not errors, 'errors': errors, 'warnings': warnings, 'disk': disk,
                'estimated_ram_bytes': ram if spec.kind == 'model' else 0,
                'estimated_vram_bytes': spec.size + VRAM_OVERHEAD if spec.kind == 'model' else 0,
                'note': f'Memory estimates use an {CONTEXT}-token context. Architecture and driver support are checked by the runtime at startup.'}

    def device_mode(self, spec, hardware, mode):
        if mode != 'auto':
            return mode
        # Unknown GPU memory is not proof that a GPU exists. Probe the installed
        # runtime later; until then CPU is a safe memory estimate.
        devices = hardware.get('gpu', {}).get('devices', [])
        return 'gpu' if devices and self.compatibility(spec, hardware, 'gpu')['ok'] else 'cpu'

    @property
    def setting_up(self):
        return self.setup_task is not None and not self.setup_task.done()

    def _require_idle_setup(self):
        if self.setting_up:
            raise ModelError('A model is being set up. Cancel setup or wait for it to finish.')

    async def snapshot(self, mode='auto'):
        hardware = await monitor.snapshot(self.root)
        rows = []
        runtime = None
        for row in self.store.all():
            spec = row['spec']
            if spec.kind == 'runtime' and (not self.runtime_spec or spec.id != self.runtime_spec.id):
                continue
            part = self.store.partial(spec)
            if spec.kind == 'model' and row['state'] == 'ready' and not self.store.final(spec).is_file():
                self.store.update(spec.id, state='error', downloaded=0, error='Model file is missing. Download it again.')
                row = self.store.get(spec.id)
            downloaded = spec.size if row['state'] == 'ready' else part.stat().st_size if part.exists() else row['downloaded']
            item = {**spec.model_dump(), 'state': row['state'], 'downloaded': downloaded, 'error': row['error'],
                    'preflight': self.compatibility(spec, hardware, self.device_mode(spec, hardware, mode))}
            if spec.kind == 'runtime':
                runtime = item
            else:
                rows.append(item)
        order = {spec.id: index for index, spec in enumerate(CATALOG)}
        rows.sort(key=lambda row: (order.get(row['id'], len(order)), row['label'].lower()))
        return {'models': rows, 'runtime_download': runtime, 'runtime_installed': self.binary() is not None,
                'setup': dict(self.setup),
                'runtime': self.runtime.snapshot(), 'directory': str(self.root),
                'inference_local': True, 'estimates_note': 'Memory estimates are approximate; actual architecture and backend support are validated at startup.'}

    async def repair_runtime(self):
        async with self.operations:
            self._require_idle_setup()
            return await self._repair_runtime()

    async def _repair_runtime(self):
        if not self.runtime_spec:
            raise ModelError('No runtime build is available for this platform.')
        if self.runtime.state in ('running', 'starting', 'stopping'):
            raise ModelError('Stop the runtime before repairing it.')
        if self.downloads.task and not self.downloads.task.done():
            raise ModelError('Wait for the active download before repairing the runtime.')
        self.store.update(self.runtime_spec.id, state='available')
        await self._download(self.runtime_spec.id, 'gpu')

    async def download(self, ident: str, mode='gpu'):
        async with self.operations:
            self._require_idle_setup()
            return await self._download(ident, mode)

    async def _download(self, ident, mode):
        spec = self.store.get(ident)['spec']
        hardware = await monitor.snapshot(self.root)
        check = self.compatibility(spec, hardware, self.device_mode(spec, hardware, mode))
        if not check['ok']:
            raise ModelError(' '.join(check['errors']))
        if spec.kind == 'runtime' and self.runtime.state in ('starting', 'running', 'stopping'):
            raise ModelError('Stop the runtime before installing its files.')
        if self.store.get(ident)['state'] == 'ready' and (not self.store.final(spec).exists() or
                (spec.kind == 'runtime' and not self.binary())):
            self.store.update(ident, state='available', downloaded=0)
        await self.downloads.start(ident)

    async def start(self, ident, mode='gpu'):
        async with self.operations:
            self._require_idle_setup()
            return await self._start(ident, mode)

    async def _start(self, ident, mode):
        row = self.store.get(ident)
        spec = row['spec']
        if spec.kind != 'model' or row['state'] != 'ready':
            raise ModelError('Download and verify the model before starting it.')
        binary = self.binary()
        if not binary:
            raise ModelError('Install the local runtime first.')
        if self.store.get(self.runtime_spec.id)['state'] != 'ready':
            raise ModelError('Wait for the runtime installation to finish before starting a model.')
        data = json.loads(self.manifest.read_text())
        if await asyncio.to_thread(checksum, binary) != data['sha256']:
            raise ModelError('Runtime executable integrity check failed. Reinstall the runtime.')
        hardware = await monitor.snapshot(self.root)
        mode = self.device_mode(spec, hardware, mode)
        check = self.compatibility(spec, hardware, mode)
        if not check['ok']:
            raise ModelError(' '.join(check['errors']))
        await self.runtime.start(spec, self.store.final(spec), binary, mode)

    async def remove(self, ident):
        async with self.operations:
            self._require_idle_setup()
            return await self._remove(ident)

    async def cancel(self, ident):
        async with self.operations:
            self._require_idle_setup()
            await self.downloads.cancel(ident)

    async def prepare(self, ident, mode='auto'):
        """Install dependencies, resume the chosen model, and wait for health.

        One background operation owns these steps. Existing managed or external
        servers are never replaced by choosing another model.
        """
        async with self.operations:
            if self.setting_up:
                if self.setup['model_id'] == ident and self.setup['mode'] == mode:
                    return dict(self.setup)
                raise ModelError('Another model is being set up. Cancel it or wait for it to finish.')
            spec = self.store.get(ident)['spec']
            if spec.kind != 'model':
                raise ModelError('Choose a chat model from the library.')
            if mode not in ('auto', 'gpu', 'cpu'):
                raise ModelError('Choose automatic, GPU or CPU mode.')
            if self.runtime.state == 'running' and self.runtime.model_id == ident:
                self.setup = {'state': 'done', 'model_id': ident, 'mode': mode, 'note': 'Ready for local chat', 'error': ''}
                return dict(self.setup)
            if self.runtime.state in ('starting', 'running', 'stopping'):
                raise ModelError('Stop the current local model before setting up another one.')
            if self.downloads.task and not self.downloads.task.done():
                raise ModelError('A download is already running. Wait for it or cancel it before setup.')
            hardware = await monitor.snapshot(self.root)
            check = self.compatibility(spec, hardware, self.device_mode(spec, hardware, mode))
            if not check['ok']:
                raise ModelError(' '.join(check['errors']))
            self.setup = {'state': 'running', 'model_id': ident, 'mode': mode, 'note': 'Preparing your model', 'error': ''}
            self.setup_task = asyncio.create_task(self._prepare(spec, mode))
            return dict(self.setup)

    async def _prepare(self, spec, mode):
        try:
            binary = self.binary()
            valid = (binary is not None and self.store.get(self.runtime_spec.id)['state'] == 'ready' and
                     await asyncio.to_thread(checksum, binary) == json.loads(self.manifest.read_text()).get('sha256'))
            if not valid:
                self.setup['note'] = 'Installing the tools for your computer'
                self.store.update(self.runtime_spec.id, state='available')
                await self._download(self.runtime_spec.id, mode)
                await self._finish_download(self.runtime_spec.id)
            self.setup['note'] = 'Downloading and checking your model'
            # Reverify cached files through the downloader too: it removes a
            # damaged cache so the next setup click can repair it automatically.
            if self.store.get(spec.id)['state'] == 'ready':
                self.store.update(spec.id, state='available')
            await self._download(spec.id, mode)
            await self._finish_download(spec.id)
            if mode == 'auto':
                hardware = await monitor.snapshot(self.root)
                # Metal/unified memory and GPUs without telemetry still get an
                # actual device probe. Unsupported drivers fall back locally.
                try:
                    devices = await self.runtime.devices(self.binary())
                except (ModelError, TimeoutError):
                    devices = []
                mode = 'gpu' if devices and self.compatibility(spec, hardware, 'gpu')['ok'] else 'cpu'
            self.setup['note'] = f'Starting your model on {"GPU" if mode == "gpu" else "CPU"}'
            await self._start(spec.id, mode)
            await self.runtime.start_task
            if self.runtime.state != 'running':
                raise ModelError(self.runtime.error or 'The local model did not become ready.')
            self.setup.update(state='done', note=f'Ready for local chat · {self.runtime.device}')
        except asyncio.CancelledError:
            self.setup.update(state='cancelled', note='Setup paused. Set up & start will resume your downloads.')
            raise
        except Exception as error:
            message = str(error) if isinstance(error, (ModelError, OSError)) else 'Setup could not finish. Try again.'
            self.setup.update(state='error', error=message, note='Setup needs attention')

    async def _finish_download(self, ident):
        if self.store.get(ident)['state'] != 'ready' and self.downloads.task:
            await self.downloads.task
        row = self.store.get(ident)
        if row['state'] != 'ready':
            raise ModelError(row['error'] or 'Download did not finish. Try again to resume.')

    async def cancel_setup(self):
        async with self.operations:
            await self._cancel_setup()

    async def _cancel_setup(self):
        if self.setting_up:
            self.setup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.setup_task
            if self.downloads.active:
                await self.downloads.cancel(self.downloads.active)
            if self.runtime.model_id == self.setup['model_id'] and self.runtime.state in ('starting', 'running'):
                await self.runtime.stop()
            # Also covers cancellation before the task's first instruction.
            self.setup.update(state='cancelled', note='Setup paused. Set up & start will resume your downloads.')

    async def stop(self):
        async with self.operations:
            await self._cancel_setup()
            await self.runtime.stop()

    async def _remove(self, ident):
        row = self.store.get(ident)
        if row['spec'].kind == 'runtime':
            raise ModelError('Runtime files are managed separately from models.')
        if self.runtime.model_id == ident and self.runtime.state in ('starting', 'running', 'stopping'):
            raise ModelError('Stop this model before removing its files.')
        await self.downloads.cancel(ident)
        self.store.partial(row['spec']).unlink(missing_ok=True)
        self.store.final(row['spec']).unlink(missing_ok=True)
        self.store.update(ident, state='available', downloaded=0, etag='', error='')

    async def close(self):
        await self._cancel_setup()
        await self.comfy.close()
        await self.downloads.close()
        await self.runtime.stop()
        self.store.db.close()
        self._file_lock.close()
