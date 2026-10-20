"""Private installations, restart/cancel semantics and real owned process checks."""
import asyncio
import json
import os
import sys
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omote.config import config
from omote.providers.connections import Connection
from omote.providers.registry import ProviderRegistry
from omote.routers import auth, setup
from omote.setup.archive import extract
from omote.setup.browser import activate
from omote.setup.catalog import UV_BUILDS, uv_artifact
from omote.setup.process import SetupError, SetupHelp, environment, run
from omote.setup.service import COMFY_REVISION, DEVICE_CHECK, PROVIDER, ToolSetup, python_in, read_json, write_json


@pytest.fixture
async def service(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'data_dir', tmp_path)
    monkeypatch.setattr('omote.setup.service.shutil.disk_usage', lambda _: SimpleNamespace(free=100 * 1024**3))
    manager = ToolSetup(tmp_path, ProviderRegistry())
    yield manager
    await manager.close()


@pytest.mark.parametrize('system,machine', UV_BUILDS)
def test_pinned_bootstrap_for_each_native_platform(system, machine):
    artifact = uv_artifact(system, machine)
    assert artifact.url.startswith('https://github.com/astral-sh/uv/releases/download/0.13.3/')
    assert len(artifact.sha256) == 64
    assert artifact.size > 10 * 1024**2
    assert ('windows' in artifact.url) == (system == 'windows')


def test_unsupported_platform_is_explicit_and_paths_are_native(tmp_path):
    with pytest.raises(ValueError, match='not available'):
        uv_artifact('linux', 'mips')
    assert python_in(tmp_path, True) == tmp_path / 'Scripts/python.exe'
    assert python_in(tmp_path, False) == tmp_path / 'bin/python'


@pytest.mark.parametrize('name', ['../../escape', '/absolute', 'C:\\outside', 'safe/../../outside'])
def test_archives_cannot_escape_staging(tmp_path, name):
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(name, b'unsafe')
    with pytest.raises(SetupError, match='unsafe'):
        extract(archive, tmp_path / 'stage')
    assert not (tmp_path / 'escape').exists()


def test_archive_links_are_rejected(tmp_path):
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        member = zipfile.ZipInfo('link')
        member.external_attr = (0o120777 << 16)
        output.writestr(member, '../../escape')
    with pytest.raises(SetupError, match='links'):
        extract(archive, tmp_path / 'stage')


def test_frozen_process_environment_excludes_app_python_and_pip_configuration(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/frozen/libraries')
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', '/original/libraries')
    monkeypatch.setenv('PYTHONHOME', '/wrong/python')
    monkeypatch.setenv('PIP_EXTRA_INDEX_URL', 'https://user:secret@example.test')
    monkeypatch.setenv('UV_INDEX_URL', 'https://elsewhere.test')
    env = environment()
    assert env['LD_LIBRARY_PATH'] == '/original/libraries'
    assert 'PYTHONHOME' not in env and 'PIP_EXTRA_INDEX_URL' not in env and 'UV_INDEX_URL' not in env


async def test_subprocess_cancel_reaps_process(tmp_path):
    pid = tmp_path / 'pid'
    script = f'import os,time,pathlib; pathlib.Path({str(pid)!r}).write_text(str(os.getpid())); time.sleep(60)'
    task = asyncio.create_task(run([sys.executable, '-c', script], cwd=tmp_path, env=environment()))
    async with asyncio.timeout(5):
        while not pid.exists():  # noqa: ASYNC110 - observe a real child process
            await asyncio.sleep(.01)
    import psutil
    child = psutil.Process(int(pid.read_text()))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not child.is_running()


async def test_setup_is_idempotent_and_cancel_prevents_ready(service, monkeypatch):
    entered = asyncio.Event()
    async def install(engine):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(service, '_browser', install)
    await service.start('playwright')
    first = service.task
    await service.start('playwright')
    assert service.task is first
    with pytest.raises(SetupError, match='Another setup'):
        await service.start('comfyui')
    await entered.wait()
    await service.cancel('playwright')
    assert service.snapshot()['playwright']['state'] == 'cancelled'
    assert not service.snapshot()['playwright']['busy']
    assert read_json(service.state_path)['playwright']['state'] == 'cancelled'


async def test_duplicate_daemons_cannot_install_into_same_directory(service):
    with pytest.raises(SetupError, match='Another Omote'):
        ToolSetup(service.data_dir, ProviderRegistry())


async def test_restore_only_starts_installed_enabled_runtime(service, monkeypatch):
    seen = []
    async def start(name, **kwargs):
        seen.append((name, kwargs))
    monkeypatch.setattr(service, 'start', start)
    await service.restore()
    assert seen == []
    service.state['comfyui']['autostart'] = True
    write_json(service.root / 'comfy-installed.json', {'revision': 'test'})
    await service.restore()
    assert seen == [('comfyui', {'device': 'auto', 'installed_only': True})]


async def test_installer_failure_is_retryable_without_registering_a_provider(service, monkeypatch):
    async def fail(device, installed_only):
        raise SetupError('download failed https://user:secret@example.test/private?token=hidden')
    monkeypatch.setattr(service, '_comfy', fail)
    await service.start('comfyui')
    await service.task
    result = service.snapshot()['comfyui']
    assert result['state'] == 'failed'
    assert 'Retry' in result['error']
    assert 'secret' not in result['log'] and 'hidden' not in result['log']
    assert service.connections.get(PROVIDER) is None


FAKE_COMFY = '''
import json, os, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        response = {"instance":os.environ["OMOTE_RUNTIME_ID"]} if self.path == "/omote/health" else (
            {"KSampler":{}} if self.path == "/object_info" else {"queue_running":[],"queue_pending":[]})
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())
HTTPServer(("127.0.0.1",int(sys.argv[sys.argv.index("--port")+1])),Handler).serve_forever()
'''


async def test_launch_connects_model_paths_bridge_and_only_its_owned_server(service, tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'main.py').write_text(FAKE_COMFY)
    external = Connection(id='someone-elses-comfy', label='Existing', adapter='comfyui', base_url='http://localhost:8188')
    service.connections.put(external)
    service.target = 'comfyui'
    await service._launch_comfy(sys.executable, source, 'CPU')
    connection = service.connections.get(PROVIDER)
    assert connection.local and connection.enabled
    assert connection.base_url.startswith('http://127.0.0.1:')
    assert service.registry.connection(PROVIDER).frontend_url == connection.base_url
    paths = read_json(service.root / 'comfy-model-paths.json')['omote']
    assert paths['base_path'] == str(tmp_path / 'local-models/comfy/models')
    assert (source / 'custom_nodes/omote_bridge/web/bridge.js').is_file()
    settings = read_json(service.root / 'comfy-user/default/comfy.settings.json')
    assert f'http://127.0.0.1:{config.port}' in settings['Omote.AllowedOrigins'].split(',')
    process = service.process
    await service.stop_comfy()
    assert process.returncode is not None
    assert service.connections.get(PROVIDER).enabled is False
    assert service.connections.get(external.id).base_url == external.base_url
    assert not service.record.exists()
    assert not service.snapshot()['comfyui']['autostart']


def test_setup_actions_are_authenticated_and_inputs_are_bounded(monkeypatch):
    monkeypatch.setattr(config, 'auth_token', 'setup-test-token')
    app = FastAPI()
    app.add_middleware(auth.TokenMiddleware)
    app.include_router(setup.router)
    with TestClient(app) as client:
        assert client.post('/api/setup/comfyui/start', json={}).status_code == 401
        assert client.post('/api/setup/playwright/cancel', json={}).status_code == 401
        headers = {'Authorization': 'Bearer setup-test-token'}
        assert client.post('/api/setup/playwright/start', json={'engine': '; arbitrary'}, headers=headers).status_code == 422
        assert client.post('/api/setup/unrelated/start', json={}, headers=headers).status_code == 422


@pytest.mark.parametrize('abi,outside,activated', [('current', False, True), ('3.10', False, False), ('current', True, False)])
def test_private_browser_activation_requires_matching_python_and_owned_path(tmp_path, monkeypatch, abi, outside, activated):
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH', raising=False)
    # Track the environment mutation for cleanup even when activation sets it.
    monkeypatch.setattr(os, 'environ', os.environ.copy())
    root = tmp_path / 'tools/setup'
    packages = tmp_path / 'outside' if outside else root / 'packages'
    packages.mkdir(parents=True)
    write_json(root / 'browser.json', {'packages': str(packages), 'python':
        f'{sys.version_info.major}.{sys.version_info.minor}' if abi == 'current' else abi})
    activate(tmp_path)
    assert (str(packages) in sys.path) is activated
    assert ('PLAYWRIGHT_BROWSERS_PATH' in os.environ) is activated


async def test_failed_comfy_setup_is_not_committed_and_retry_repairs_packages(service, monkeypatch):
    source = service.root / 'comfy-source' / ('ComfyUI-' + COMFY_REVISION)
    source.mkdir(parents=True)
    (source / 'main.py').write_text('# source already downloaded')
    python = python_in(service.root / 'comfy-env')
    python.parent.mkdir(parents=True)
    python.touch()
    calls = []
    async def command(args, **kwargs):
        calls.append(args)
        return json.dumps({'device': 'CPU'}) if DEVICE_CHECK in args else ''
    monkeypatch.setattr(service, 'uv', AsyncMock(return_value=service.root / 'uv'))
    monkeypatch.setattr(service, 'command', command)
    monkeypatch.setattr('omote.setup.windows.ensure_runtime', AsyncMock())
    monkeypatch.setattr(service, '_launch_comfy', AsyncMock(side_effect=SetupError('unhealthy server')))
    await service.start('comfyui')
    await service.task
    assert service.snapshot()['comfyui']['state'] == 'failed'
    assert not (service.root / 'comfy-installed.json').exists()
    monkeypatch.setattr(service, '_launch_comfy', AsyncMock())
    await service.start('comfyui')
    await service.task
    assert service.snapshot()['comfyui']['state'] == 'ready'
    assert any('--reinstall' in args for args in calls)
    assert read_json(service.root / 'comfy-installed.json')['preference'] == 'auto'


@pytest.mark.parametrize('result,message', [('OMOTE_PERMISSION_NEEDED', 'permission dialog'), ('OMOTE_RESTART_NEEDED', 'Restart this computer')])
async def test_windows_runtime_guides_permission_and_restart_in_app(service, monkeypatch, result, message):
    from omote.setup import windows
    monkeypatch.setattr(windows, 'os', SimpleNamespace(name='nt', environ={}))
    monkeypatch.setattr(windows, 'vc_artifact', lambda: uv_artifact('windows', 'x86_64'))
    payload = service.root / 'verified-installer'
    payload.write_bytes(b'verified fixture')
    monkeypatch.setattr(service, 'fetch', AsyncMock(return_value=payload))
    calls = []
    async def command(args, **kwargs):
        calls.append((args, kwargs))
        return 'missing' if '-c' in args else result
    monkeypatch.setattr(service, 'command', command)
    with pytest.raises(SetupHelp, match=message):
        await windows.ensure_runtime(service, 'private-python')
    args, kwargs = calls[-1]
    assert '-NoProfile' in args and '-NonInteractive' in args
    assert kwargs['env']['OMOTE_VC_INSTALLER'] == str(service.root / 'vc-runtime-setup.exe')
    assert 'Get-AuthenticodeSignature' in (service.root / 'install-windows-support.ps1').read_text(encoding='utf-8-sig')


async def test_actionable_setup_errors_are_visible_without_opening_logs(service, monkeypatch):
    monkeypatch.setattr(service, '_browser', AsyncMock(side_effect=SetupHelp('Allow the system permission dialog, then Retry setup.')))
    await service.start('playwright')
    await service.task
    assert 'permission dialog' in service.snapshot()['playwright']['error']
