"""On-demand host telemetry. Never starts a runtime, downloads, or elevates.

One cache shared by the UI and tools bounds probing even with several tabs.
All blocking work runs off the event loop; unknown measurements stay null.
"""

from __future__ import annotations

import asyncio
import csv
import math
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil


def _number(value: str) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except ValueError:
        return None


def _amd_devices(root: Path | None = None) -> list[dict[str, Any]]:
    devices = []
    seen = set()
    for card in sorted((root or Path('/sys/class/drm')).glob('card[0-9]*')):
        device = card / 'device'
        try:
            resolved = device.resolve()
            if resolved in seen or (device / 'vendor').read_text().strip() != '0x1002':
                continue
            seen.add(resolved)

            def read(name, device=device):
                try:
                    return _number((device / name).read_text().strip())
                except OSError:
                    return None

            devices.append({'name': f'AMD GPU ({card.name})', 'vendor': 'amd',
                            'utilization_percent': read('gpu_busy_percent'),
                            'memory_used_bytes': read('mem_info_vram_used'),
                            'memory_total_bytes': read('mem_info_vram_total'),
                            'temperature_c': None, 'power_watts': None})
        except OSError:
            continue
    return devices


def gpu_usage() -> dict[str, Any]:
    devices = _amd_devices() if sys.platform.startswith('linux') else []
    executable = shutil.which('nvidia-smi')
    error = ''
    if executable:
        try:
            result = subprocess.run(
                [executable, '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=3, check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode:
                error = 'The NVIDIA driver did not provide telemetry. Check driver access on the Omote host.'
            else:
                for row in csv.reader(result.stdout.splitlines()):
                    if len(row) != 6:
                        continue
                    name, busy, used, total, temperature, power = (value.strip() for value in row)
                    used, total = _number(used), _number(total)
                    devices.append({'name': name, 'vendor': 'nvidia', 'utilization_percent': _number(busy),
                                    'memory_used_bytes': used * 1024**2 if used is not None else None,
                                    'memory_total_bytes': total * 1024**2 if total is not None else None,
                                    'temperature_c': _number(temperature), 'power_watts': _number(power)})
                if not devices:
                    error = 'The GPU driver returned no readable devices.'
        except (OSError, subprocess.TimeoutExpired):
            error = 'NVIDIA telemetry is unavailable or timed out.'
    return {'status': 'available' if devices else ('unavailable' if executable else 'unsupported'),
            'devices': devices, 'note': error or ('' if devices else
            'GPU telemetry needs NVIDIA nvidia-smi or Linux AMD sysfs. Other GPUs may still run workloads.')}


def local_runtimes() -> list[dict[str, Any]]:
    rows = []
    for name, commands, port in (
        ('ComfyUI', ('comfy', 'comfy-cli'), 8188),
        ('Ollama', ('ollama',), 11434),
        ('FFmpeg', ('ffmpeg',), None),
        ('Python', ('python3', 'python'), None),
    ):
        installed = any(shutil.which(command) for command in commands)
        listening = False
        if port:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    listening = True
            except OSError:
                pass
        rows.append({'name': name, 'command_found': installed, 'port': port,
                     'port_open': listening,
                     'status': f'Local port {port} reachable' if listening else
                               ('Command available' if installed else 'Not detected')})
    return rows


class ResourceMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        self._root: Path | None = None
        self._expires = 0.
        self._runtimes: list[dict[str, Any]] = []
        self._runtime_expires = 0.

    async def snapshot(self, root: Path) -> dict[str, Any]:
        return await asyncio.to_thread(self._sample, root)

    def _sample(self, root: Path) -> dict[str, Any]:
        with self._lock:
            if self._cache is not None and self._root == root and time.monotonic() < self._expires:
                return self._cache
            # A short explicit interval avoids psutil's meaningless first
            # sample, including when to_thread chooses a different worker.
            data: dict[str, Any] = {'sampled_at': time.time(), 'host': 'Computer running Omote',
                                    'cpu': None, 'memory': None, 'disk': None, 'notes': []}
            try:
                data['cpu'] = {'percent': psutil.cpu_percent(interval=.1), 'logical_cores': os.cpu_count()}
            except (OSError, psutil.Error):
                data['notes'].append('CPU usage is unavailable.')
            try:
                memory = psutil.virtual_memory()
                data['memory'] = {'total_bytes': memory.total, 'available_bytes': memory.available,
                                  'used_bytes': memory.total - memory.available, 'percent': memory.percent}
            except (OSError, psutil.Error):
                data['notes'].append('Memory usage is unavailable.')
            try:
                disk = psutil.disk_usage(str(root))
                data['disk'] = {'path': str(root), 'total_bytes': disk.total, 'used_bytes': disk.used,
                                'free_bytes': disk.free, 'percent': disk.percent}
            except (OSError, psutil.Error):
                data['notes'].append('Workspace disk usage is unavailable.')
            data['gpu'] = gpu_usage()
            from omote.system import detect

            host = detect()
            data['admin'] = {'method': host.elevation, 'note': host.elevation_note}
            if time.monotonic() >= self._runtime_expires:
                self._runtimes = local_runtimes()
                self._runtime_expires = time.monotonic() + 10
            data['runtimes'] = self._runtimes
            self._root, self._cache = root, data
            self._expires = time.monotonic() + 2
            return data


monitor = ResourceMonitor()
