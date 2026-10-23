"""One-click setup, health checks and lifecycle for Omote-owned local tools."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import socket
import sys
import tempfile
import uuid
from pathlib import Path

import aiohttp
import psutil

from omote.local_models.catalog import GIB
from omote.local_models.downloads import Downloads, DownloadStore, checksum
from omote.providers.connections import Connection, ConnectionStore, build
from omote.setup.archive import extract
from omote.setup.browser import activate, version_supported
from omote.setup.catalog import COMFY_REVISION, COMFY_SOURCE, PLAYWRIGHT_VERSION, comfy_host_error, uv_artifact
from omote.setup.process import SetupError, SetupHelp, clean, environment, run, spawn, terminate

PROVIDER = 'omote-managed-comfyui'
TARGETS = ('comfyui', 'playwright')
HEALTH_NODE = '''import os
from aiohttp import web
from server import PromptServer
@PromptServer.instance.routes.get("/omote/health")
async def health(request):
    return web.json_response({"instance": os.environ["OMOTE_RUNTIME_ID"]})
NODE_CLASS_MAPPINGS = {}
'''
BROWSER_CHECK = '''import asyncio, sys
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        browser = await getattr(p, sys.argv[1]).launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content("<button>Ready</button>")
            await page.get_by_role("button", name="Ready").click()
            assert await page.text_content("button") == "Ready"
        finally:
            await browser.close()
asyncio.run(main())
'''
DEVICE_CHECK = '''import json, torch
print(json.dumps({"device": "GPU" if torch.cuda.is_available() else
    "Apple GPU" if hasattr(torch.backends, "mps") and torch.backends.mps.is_available() else
    "Intel GPU" if hasattr(torch, "xpu") and torch.xpu.is_available() else "CPU",
    "torch": torch.__version__}))
'''


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
    temporary.replace(path)


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def python_in(venv, windows=None):
    return venv / ('Scripts/python.exe' if (os.name == 'nt' if windows is None else windows) else 'bin/python')


async def in_thread(fn, *args):
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Never remove staging files while an extraction thread still uses them.
        await task
        raise


class ToolSetup:
    def __init__(self, data_dir, registry, connections=None):
        self.data_dir = Path(data_dir).resolve()
        self.root = self.data_dir / 'tools' / 'setup'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_file = (self.root / 'manager.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.lock_file.write(b'0')
                self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.lock_file.close()
            raise SetupError('Another Omote window is managing local setup.') from error
        self.registry = registry
        self.connections = connections or ConnectionStore(self.data_dir / 'connections.json')
        self.store = DownloadStore(self.root / 'downloads')
        self.downloads = Downloads(self.store)
        self.state_path = self.root / 'state.json'
        self.state = read_json(self.state_path)
        for name in TARGETS:
            row = self.state.setdefault(name, {'state': 'missing', 'note': '', 'error': '', 'log': ''})
            if row['state'] in ('installing', 'starting'):
                row.update(state='interrupted', note='Setup was interrupted. Choose Retry setup to continue.')
        if self.state['comfyui']['state'] == 'ready':
            self.state['comfyui'].update(state='stopped', note='Starting your local studio…')
        self.task = None
        self.target = None
        self.repair_comfy = False
        self.operations = asyncio.Lock()
        self.process = self.pump = None
        self.record = self.root / 'comfy-process.json'
        self._recover()
        self._disconnect()
        activate(self.data_dir)
        manifest = read_json(self.root / 'browser.json')
        if self.state['playwright']['state'] == 'ready':
            try:
                from playwright._repo_version import version
                if not version_supported(version) or version != manifest.get('version') or manifest.get('python') != f'{sys.version_info.major}.{sys.version_info.minor}':
                    self.state['playwright'].update(state='interrupted', note='Browser tools changed. Retry setup to get the matching browser.')
            except ImportError:
                self.state['playwright'].update(state='interrupted', note='Browser tools are missing. Retry setup to repair them.')

    def _save(self):
        write_json(self.state_path, self.state)

    def update(self, name, **changes):
        self.state[name].update(changes)
        self._save()

    def snapshot(self):
        result = json.loads(json.dumps(self.state))
        for name in TARGETS:
            result[name]['busy'] = bool(self.task and not self.task.done() and self.target == name)
            result[name]['waiting'] = bool(self.task and not self.task.done() and self.target != name)
        if self.downloads.active:
            row = self.store.get(self.downloads.active)
            part = self.store.partial(row['spec'])
            result[self.target]['download'] = {
                'bytes': part.stat().st_size if part.exists() else row['downloaded'], 'total': row['spec'].size,
            }
        return result

    def _recover(self):
        info = read_json(self.record)
        try:
            process = psutil.Process(info['pid'])
            if (abs(process.create_time() - info['created']) < .01 and
                    Path(process.exe()).resolve() == Path(info['executable']).resolve() and
                    info['nonce_path'] in process.cmdline()):
                children = process.children(recursive=True)
                for child in reversed(children):
                    child.kill()
                process.kill()
                psutil.wait_procs([process, *children], timeout=3)
        except (OSError, KeyError, ValueError, psutil.Error):
            pass
        self.record.unlink(missing_ok=True)

    def _disconnect(self):
        self.registry.unregister(PROVIDER)
        connection = self.connections.get(PROVIDER)
        if connection and connection.enabled:
            connection.enabled = False
            self.connections.put(connection)

    async def restore(self):
        """Only restart an installed, explicitly enabled service. No downloads."""
        if self.state['comfyui'].get('autostart') and (self.root / 'comfy-installed.json').is_file():
            await self.start('comfyui', device=self.state['comfyui'].get('preference', 'auto'), installed_only=True)

    async def start(self, name, *, engine='chromium', device='auto', installed_only=False):
        if name not in TARGETS or engine not in ('chromium', 'firefox', 'webkit') or device not in ('auto', 'cpu'):
            raise SetupError('Choose ComfyUI or a supported browser.')
        async with self.operations:
            if self.task and not self.task.done():
                if self.target == name:
                    return self.snapshot()[name]
                raise SetupError('Another setup is running. Wait for it to finish or cancel it.')
            if name == 'comfyui' and self.process and self.process.returncode is None:
                return self.snapshot()[name]
            uv_artifact()  # Fail before changing anything on an unsupported host.
            if name == 'comfyui' and (reason := comfy_host_error()):
                raise SetupHelp(reason)
            installed = (self.root / ('browser.json' if name == 'playwright' else 'comfy-installed.json')).is_file()
            gpu_packages = device == 'auto' and (shutil.which('nvidia-smi') or shutil.which('rocminfo'))
            needed = GIB // 2 if installed else (2 if name == 'playwright' else 15 if gpu_packages else 4) * GIB
            if not installed_only and shutil.disk_usage(self.root).free < needed:
                raise SetupError(f'Free at least {(needed + GIB - 1) // GIB} GB on this computer, then try setup again.')
            self.target = name
            if name == 'comfyui':
                self.repair_comfy = not installed_only and self.state[name]['state'] in ('failed', 'interrupted')
            self.update(name, state='starting' if installed_only else 'installing',
                        note='Getting things ready', error='', log='', engine=engine, preference=device)
            self.task = asyncio.create_task(self._setup(name, engine, device, installed_only))
            return self.snapshot()[name]

    async def _setup(self, name, engine, device, installed_only):
        try:
            if name == 'playwright':
                await self._browser(engine)
            else:
                await self._comfy(device, installed_only)
        except asyncio.CancelledError:
            if name == 'comfyui':
                await self._stop_process()
            self.update(name, state='cancelled', note='Setup paused. Retry setup to continue.', autostart=False)
            raise
        except Exception as error:
            if name == 'comfyui':
                await self._stop_process()
            log = clean(self.state[name].get('log', '') + '\n' + str(error))
            message = str(error) if isinstance(error, SetupHelp) else (
                'Setup could not finish. Retry setup to continue; your downloaded files are kept.')
            if any(phrase in str(error).lower() for phrase in ('no space left', 'not enough disk space', 'disk full')):
                message = 'This computer ran out of storage. Free some space, then choose Retry setup. Your downloaded files are kept.'
            self.update(name, state='failed', error=message,
                        note='Needs attention', log=log)

    async def cancel(self, name):
        async with self.operations:
            if self.target == name and self.task and not self.task.done():
                self.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.task
                if self.downloads.active:
                    await self.downloads.cancel(self.downloads.active)
                self.update(name, state='cancelled', note='Setup paused. Retry setup to continue.', autostart=False)
            return self.snapshot()[name]

    def env(self):
        env = environment()
        env.update(UV_PYTHON_INSTALL_DIR=str(self.root / 'python'), UV_CACHE_DIR=str(self.root / 'cache'),
                   UV_PYTHON_DOWNLOADS='automatic', UV_NO_PROGRESS='1', UV_NO_CONFIG='1',
                   UV_DEFAULT_INDEX='https://pypi.org/simple', PYTORCH_ENABLE_MPS_FALLBACK='1')
        return env

    async def command(self, args, *, env=None, timeout=1800):  # noqa: ASYNC109 - bounded subprocess wrapper
        name = self.target
        return await run(args, cwd=self.root, env=env or self.env(), timeout=timeout,
                         log=lambda value: self.state[name].update(log=value))

    async def fetch(self, artifact):
        self.store.add(artifact)
        final = self.store.final(artifact)
        # The transfer engine verifies, repairs corrupted caches and resumes.
        self.store.update(artifact.id, state='available')
        await self.downloads.start(artifact.id)
        await self.downloads.task
        row = self.store.get(artifact.id)
        if row['state'] != 'ready':
            raise SetupError(row['error'])
        return final

    async def uv(self):
        spec = uv_artifact()
        directory = self.root / spec.id
        manifest = read_json(directory / 'installed.json')
        binary = directory / ('uv.exe' if os.name == 'nt' else 'uv')
        if binary.is_file() and await in_thread(checksum, binary) == manifest.get('sha256'):
            return binary
        self.update(self.target, note='Downloading the setup tools for your computer')
        archive = await self.fetch(spec)
        with tempfile.TemporaryDirectory(dir=self.root, prefix='unpack-') as temporary:
            staging = Path(temporary)
            await in_thread(extract, archive, staging)
            binaries = await in_thread(lambda: list(staging.rglob(binary.name)))
            if len(binaries) != 1:
                raise SetupError('The setup package does not contain the expected executable.')
            source = binaries[0]
            source.chmod(0o755)
            await self.command([source, '--version'], timeout=20)
            directory.mkdir(exist_ok=True)
            source.replace(binary)
            write_json(directory / 'installed.json', {'sha256': await in_thread(checksum, binary)})
        return binary

    async def venv(self, uv, name, version):
        directory = self.root / name
        python = python_in(directory)
        if not python.is_file():
            self.update(self.target, note='Installing a private Python runtime')
            await self.command([uv, 'venv', '--python', version, '--python-preference', 'only-managed', directory])
        return python

    async def _browser(self, engine):
        activate(self.data_dir)
        try:
            from playwright._repo_version import version
        except ImportError:
            version = PLAYWRIGHT_VERSION
        if not version_supported(version):
            version = PLAYWRIGHT_VERSION
        uv = await self.uv()
        abi = f'{sys.version_info.major}.{sys.version_info.minor}'
        python = await self.venv(uv, f'browser-env-{version}-py{abi}', abi)
        self.update('playwright', note='Installing browser automation')
        await self.command([uv, 'pip', 'install', '--python', python, '--only-binary', ':all:', f'playwright=={version}'])
        env = self.env()
        env['PLAYWRIGHT_BROWSERS_PATH'] = os.environ.get('PLAYWRIGHT_BROWSERS_PATH', str(self.root / 'browsers'))
        self.update('playwright', note=f'Downloading {engine.title()}')
        await self.command([python, '-m', 'playwright', 'install', engine], env=env)
        self.update('playwright', note='Opening a test page')
        try:
            await self.command([python, '-c', BROWSER_CHECK, engine], env=env, timeout=90)
        except SetupError as error:
            # Only a missing-library failure calls the OS package installer.
            if sys.platform != 'linux' or not any(word in str(error).lower() for word in
                    ('missing dependencies', 'error while loading shared libraries', 'install-deps')):
                raise
            self.update('playwright', note='Installing browser support. Your computer may ask for administrator permission.')
            prefix = await self.elevation()
            await self.command([*prefix, python, '-m', 'playwright', 'install-deps', engine], env=env)
            await self.command([python, '-c', BROWSER_CHECK, engine], env=env, timeout=90)
        packages = json.loads((await self.command(
            [python, '-c', 'import json,sysconfig;print(json.dumps(sysconfig.get_path("purelib")))'])).strip())
        relative = Path(packages).relative_to(self.root)
        write_json(self.root / 'browser.json', {'packages': str(relative), 'version': version, 'python': abi})
        activate(self.data_dir)
        # Prove the actual daemon can import and use it too, including frozen builds.
        from playwright.async_api import async_playwright
        async with async_playwright() as driver:
            browser = await getattr(driver, engine).launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('<button>Ready</button>')
                await page.get_by_role('button', name='Ready').click()
            finally:
                await browser.close()
        from omote.agent.browsers import BrowserChoice, settings
        settings().save(BrowserChoice(engine=engine))
        self.update('playwright', state='ready', note=f'{engine.title()} is ready for web tasks', error='', version=version)

    async def elevation(self):
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            return []
        if shutil.which('sudo'):
            try:
                await self.command(['sudo', '-n', 'true'], timeout=10)
                return ['sudo', '-n']
            except SetupError:
                pass
        if shutil.which('pkexec'):
            return ['pkexec']
        raise SetupHelp('This computer needs administrator permission to add browser support. Ask its administrator to open Omote and choose Retry setup.')

    async def _comfy(self, device, installed_only):
        installed = read_json(self.root / 'comfy-installed.json')
        source = self.root / 'comfy-source' / ('ComfyUI-' + COMFY_REVISION)
        python = python_in(self.root / 'comfy-env')
        if not installed_only and (self.repair_comfy or installed.get('preference', 'auto') != device or
                installed.get('revision') != COMFY_REVISION or not python.is_file() or not (source / 'main.py').is_file()):
            uv = await self.uv()
            python = await self.venv(uv, 'comfy-env', '3.13')
            if not (source / 'main.py').is_file():
                self.update('comfyui', note='Downloading ComfyUI')
                archive = await self.fetch(COMFY_SOURCE)
                with tempfile.TemporaryDirectory(dir=self.root, prefix='comfy-unpack-') as temporary:
                    await in_thread(extract, archive, Path(temporary))
                    destination = self.root / 'comfy-source'
                    destination.mkdir(exist_ok=True)
                    await in_thread(Path(temporary, source.name).replace, source)
            self.update('comfyui', note='Installing the generation engine for your computer. This can take several minutes.')
            # uv selects a compatible PyTorch index from local GPU/driver data.
            args = [uv, 'pip', 'install', '--python', python, '--torch-backend', 'auto' if device == 'auto' else 'cpu',
                    '-r', source / 'requirements.txt']
            if self.repair_comfy:
                args.append('--reinstall')
            elif installed and installed.get('preference', 'auto') != device:
                args.extend(['--reinstall-package', 'torch', '--reinstall-package', 'torchvision'])
            await self.command(args, timeout=3600)
        if not python.is_file() or not (source / 'main.py').is_file():
            raise SetupError('The local studio installation is incomplete. Retry setup to repair it.')
        self.update('comfyui', state='starting', note='Checking your graphics hardware')
        from omote.setup.windows import ensure_runtime
        await ensure_runtime(self, python)
        details = json.loads((await self.command([python, '-c', DEVICE_CHECK], timeout=60)).strip().splitlines()[-1])
        actual_device = 'CPU' if device == 'cpu' else details['device']
        await self._launch_comfy(python, source, actual_device)
        write_json(self.root / 'comfy-installed.json', {'revision': COMFY_REVISION, 'preference': device})
        self.update('comfyui', state='ready', note=f'Local studio is ready · {actual_device}',
                    device=actual_device, autostart=True, error='')

    async def _launch_comfy(self, python, source, device):
        self.update('comfyui', note='Starting ComfyUI and connecting it to Studio')
        from omote.local_models.catalog import Artifact
        model_root = self.data_dir / 'local-models' / 'comfy' / 'models'
        categories = Artifact.model_fields['category'].annotation.__args__
        paths = self.root / 'comfy-model-paths.json'
        write_json(paths, {'omote': {'base_path': str(model_root), **{name: name for name in categories}}})
        for name in categories:
            (model_root / name).mkdir(parents=True, exist_ok=True)
        node = source / 'custom_nodes' / 'omote_managed'
        node.mkdir(parents=True, exist_ok=True)
        (node / '__init__.py').write_text(HEALTH_NODE)
        # Bundle Omote's own editor bridge; never fetch arbitrary custom nodes.
        bridge = Path(__file__).parents[1] / 'static' / 'omote-comfyui-bridge.zip'
        if not (source / 'custom_nodes' / 'omote_bridge').is_dir():
            await in_thread(extract, bridge, source / 'custom_nodes')
        instance = uuid.uuid4().hex
        temporary = self.root / 'comfy-temp' / instance
        user = self.root / 'comfy-user'
        user.mkdir(exist_ok=True)
        # The managed editor's bridge trusts this local workspace automatically.
        # Keep any origins the user explicitly added to their own settings.
        from omote.config import config
        settings_path = user / 'default' / 'comfy.settings.json'
        settings = read_json(settings_path)
        origins = {s.strip() for s in str(settings.get('Omote.AllowedOrigins', '')).split(',') if s.strip()}
        origins.update(f'http://{host}:{config.port}' for host in ('127.0.0.1', 'localhost', '[::1]'))
        settings['Omote.AllowedOrigins'] = ','.join(sorted(origins))
        write_json(settings_path, settings)
        temporary.mkdir(parents=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = self.env()
        env['OMOTE_RUNTIME_ID'] = instance
        args = [python, source / 'main.py', '--listen', '127.0.0.1', '--port', str(port),
                '--disable-auto-launch', '--extra-model-paths-config', paths,
                '--user-directory', user, '--temp-directory', temporary,
                '--output-directory', self.root / 'comfy-output',
                '--disable-all-custom-nodes', '--whitelist-custom-nodes', 'omote_managed', 'omote_bridge']
        if device == 'CPU':
            args.append('--cpu')
        process = await spawn(args, cwd=source, env=env)
        self.process = process
        self.pump = asyncio.create_task(self._pump_comfy(process))
        owner = psutil.Process(process.pid)
        write_json(self.record, {'pid': process.pid, 'created': owner.create_time(), 'executable': owner.exe(),
                                 'nonce_path': str(temporary)})
        base = f'http://127.0.0.1:{port}'
        try:
            # transport-exempt: the locally-managed ComfyUI instance, not a
            # model server, so it never crosses a provider or Tor.
            async with asyncio.timeout(180), aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                while True:
                    if process.returncode is not None:
                        raise SetupError('ComfyUI stopped during startup. ' + self.state['comfyui'].get('log', '')[-2000:])
                    try:
                        async with session.get(base + '/omote/health') as response:
                            if response.status == 200 and (await response.json()).get('instance') == instance:
                                async with session.get(base + '/object_info') as nodes:
                                    catalog = await nodes.json()
                                    if nodes.status == 200 and 'KSampler' in catalog:
                                        break
                    except (aiohttp.ClientError, TimeoutError, ValueError):
                        pass
                    await asyncio.sleep(.5)
            connection = Connection(id=PROVIDER, label='Local studio', adapter='comfyui', base_url=base,
                                    frontend_url=base, local=True, modalities=['image', 'video'])
            info, impls = build(connection)
            self.connections.put(connection)
            self.registry.register(info, impls, connection)
            self.state['comfyui']['url'] = base
            self.state['comfyui']['provider'] = PROVIDER
        except BaseException:
            await self._stop_process()
            raise

    async def _pump_comfy(self, process):
        while chunk := await process.stdout.read(4096):
            self.state['comfyui']['log'] = clean(self.state['comfyui'].get('log', '') + chunk.decode(errors='replace'))
        await process.wait()
        if self.process is process and self.state['comfyui']['state'] == 'ready':
            self._disconnect()
            self.update('comfyui', state='failed', note='Local studio stopped', error='Choose Start studio to reconnect.')

    async def _stop_process(self):
        process, pump = self.process, self.pump
        self.process = self.pump = None
        await terminate(process)
        if pump:
            await pump
        self.record.unlink(missing_ok=True)
        self._disconnect()

    async def stop_comfy(self):
        await self.cancel('comfyui')
        async with self.operations:
            if self.process and self.process.returncode is None:
                # transport-exempt: the locally-managed ComfyUI instance, not
                # a model server, so it never crosses a provider or Tor.
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                    async with session.get(self.state['comfyui']['url'] + '/queue') as response:
                        queue = await response.json()
                        if queue.get('queue_running') or queue.get('queue_pending'):
                            raise SetupError('A creation is still running. Finish or cancel it before stopping the studio.')
            await self._stop_process()
            self.update('comfyui', state='stopped', note='Local studio is stopped', autostart=False)

    async def close(self):
        if self.task and not self.task.done():
            await self.cancel(self.target)
        await self._stop_process()
        await self.downloads.close()
        self.store.db.close()
        self.lock_file.close()
