"""Host telemetry, driver failures, caching, and authenticated UI/tool access."""

import asyncio
import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from omote import resources
from omote.agent.tools.system import SystemUsageTool
from omote.config import config
from omote.routers import system
from omote.routers.auth import TokenMiddleware
from tests.test_system import ctx


@pytest.fixture
def counters(monkeypatch):
    monkeypatch.setattr(resources.psutil, 'cpu_percent', lambda interval: 23.5)
    monkeypatch.setattr(resources.psutil, 'virtual_memory', lambda: SimpleNamespace(
        total=32 * 1024**3, available=20 * 1024**3, percent=37.5))
    monkeypatch.setattr(resources.psutil, 'disk_usage', lambda path: SimpleNamespace(
        total=100 * 1024**3, used=60 * 1024**3, free=40 * 1024**3, percent=60.))
    monkeypatch.setattr(resources, 'gpu_usage', lambda: {'status': 'unsupported', 'devices': [], 'note': 'No GPU telemetry'})
    monkeypatch.setattr(resources, 'local_runtimes', lambda: [])


async def test_samples_memory_availability_and_coalesces_concurrent_tabs(counters, tmp_path, monkeypatch):
    calls = []

    def gpu():
        calls.append(True)
        return {'status': 'available', 'devices': [], 'note': ''}

    monkeypatch.setattr(resources, 'gpu_usage', gpu)
    monitor = resources.ResourceMonitor()
    samples = await asyncio.gather(*(monitor.snapshot(tmp_path) for _ in range(8)))
    assert len(calls) == 1
    assert all(row == samples[0] for row in samples)
    data = samples[0]
    assert data['cpu']['percent'] == 23.5
    assert data['memory']['used_bytes'] == 12 * 1024**3
    assert data['disk']['path'] == str(tmp_path)
    # A different project's disk must not get the previous project's reading.
    other = await monitor.snapshot(tmp_path / 'other')
    assert other['disk']['path'] == str(tmp_path / 'other')
    assert len(calls) == 2


async def test_missing_disk_or_cpu_stays_unknown_without_hiding_other_metrics(counters, tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise OSError('not accessible')

    monkeypatch.setattr(resources.psutil, 'cpu_percent', missing)
    monkeypatch.setattr(resources.psutil, 'disk_usage', missing)
    data = await resources.ResourceMonitor().snapshot(tmp_path)
    assert data['cpu'] is None and data['disk'] is None
    assert data['memory']['total_bytes'] > 0
    assert len(data['notes']) == 2


def test_nvidia_multiple_devices_and_unsupported_fields(monkeypatch):
    monkeypatch.setattr(resources, '_amd_devices', lambda: [])
    monkeypatch.setattr(resources.shutil, 'which', lambda _: '/usr/bin/nvidia-smi')
    commands = []

    def run(argv, **kwargs):
        commands.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='"RTX, workstation", 12, 768, 16311, 35, 8.6\nGPU 2, N/A, 0, 8192, [Not Supported], nan\n')

    monkeypatch.setattr(resources.subprocess, 'run', run)
    data = resources.gpu_usage()
    assert data['status'] == 'available'
    first, second = data['devices']
    assert first['name'] == 'RTX, workstation'
    assert first['memory_used_bytes'] == 768 * 1024**2
    assert first['temperature_c'] == 35
    assert second['utilization_percent'] is None and second['power_watts'] is None
    assert second['memory_used_bytes'] == 0
    assert commands[0][1]['timeout'] == 3
    assert 'shell' not in commands[0][1]


@pytest.mark.parametrize('failure', ['driver', 'timeout', 'empty'])
def test_nvidia_failure_is_not_reported_as_idle_gpu(monkeypatch, failure):
    monkeypatch.setattr(resources, '_amd_devices', lambda: [])
    monkeypatch.setattr(resources.shutil, 'which', lambda _: 'nvidia-smi')

    def run(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('nvidia-smi', 3)
        return SimpleNamespace(returncode=1 if failure == 'driver' else 0, stdout='')

    monkeypatch.setattr(resources.subprocess, 'run', run)
    data = resources.gpu_usage()
    assert data['status'] == 'unavailable' and data['devices'] == [] and data['note']


def test_amd_missing_measurements_and_connector_aliases(tmp_path):
    device = tmp_path / 'card0' / 'device'
    device.mkdir(parents=True)
    (device / 'vendor').write_text('0x1002\n')
    (device / 'gpu_busy_percent').write_text('42\n')
    (device / 'mem_info_vram_total').write_text(str(8 * 1024**3))
    (tmp_path / 'card0-DP-1').mkdir()
    (tmp_path / 'card0-DP-1' / 'device').symlink_to(device, target_is_directory=True)
    rows = resources._amd_devices(tmp_path)
    assert len(rows) == 1
    assert rows[0]['utilization_percent'] == 42
    assert rows[0]['memory_used_bytes'] is None


def test_runtime_detection_never_launches_a_runtime(monkeypatch):
    monkeypatch.setattr(resources.shutil, 'which', lambda name: '/bin/ffmpeg' if name == 'ffmpeg' else None)

    def unavailable(*args, **kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(resources.socket, 'create_connection', unavailable)
    rows = {row['name']: row for row in resources.local_runtimes()}
    assert rows['FFmpeg']['command_found'] and rows['FFmpeg']['status'] == 'Command available'
    assert not rows['ComfyUI']['command_found'] and not rows['ComfyUI']['port_open']


async def test_agent_tool_is_read_only_and_uses_the_session_disk(counters, monkeypatch):
    monitor = resources.ResourceMonitor()
    monkeypatch.setattr(resources, 'monitor', monitor)
    tool = SystemUsageTool()
    assert tool.assess({}, ctx()).risk.value == 'read'
    data = json.loads((await tool.run({}, ctx())).content)
    assert data['disk']['path'] == str(ctx().root)


def test_system_api_requires_auth_and_is_not_cached(counters, monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'auth_token', 'resource-test-token')
    monkeypatch.setattr(config, 'workspace', tmp_path)
    monkeypatch.setattr(system, 'monitor', resources.ResourceMonitor())
    monkeypatch.setattr(system.manager, 'list', lambda: [{'id': 'active', 'busy': True, 'background': 0, 'waiting_on': None},
                                                        {'id': 'idle', 'busy': False, 'background': 0, 'waiting_on': None}])
    app = FastAPI()
    app.add_middleware(TokenMiddleware)
    app.include_router(system.router)
    client = TestClient(app)
    assert client.get('/api/system/usage').status_code == 401
    response = client.get('/api/system/usage', headers={'Authorization': 'Bearer resource-test-token'})
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.json()['work_count'] == 1 and response.json()['work'][0]['id'] == 'active'
