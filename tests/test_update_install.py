"""Actual detached-process replacement, with no writes to the machine's install."""

import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from omote import update_install
from omote.update import Asset, Updates, installation_kind, pick_asset


def request(tmp_path, parent, target, payload):
    installer = tmp_path / 'Omote_0.10.0_amd64.AppImage'
    installer.write_bytes(payload)
    installer.chmod(0o755)
    installer.with_name(installer.name + '.verified.json').write_text(
        json.dumps(
            {
                'name': installer.name,
                'version': '0.10.0',
                'sha256': hashlib.sha256(payload).hexdigest(),
                'bytes': len(payload),
            }
        )
    )
    path = tmp_path / 'install-request.json'
    path.write_text(json.dumps({'installer': str(installer), 'launcher': str(target), 'parent': parent.pid}))
    return path, installer


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux AppImage replacement')
def test_worker_waits_replaces_the_original_path_and_launches_new_version(tmp_path):
    target = tmp_path / 'Installed Omote.AppImage'
    target.write_text('#!/bin/sh\nexit 0\n')
    target.chmod(0o755)
    marker = tmp_path / 'new-version'
    # The marker is a positional script argument in production; use a fixed
    # filename relative to the installed path in this isolated executable.
    payload = b'#!/bin/sh\nprintf new-version > "$(dirname "$0")/new-version"\n'
    old = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    path, installer = request(tmp_path, old, target, payload)
    worker = subprocess.Popen([sys.executable, '-m', 'omote.update_install', str(path)])
    try:
        deadline = time.monotonic() + 15
        while not path.with_suffix('.ready').exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert path.with_suffix('.ready').exists()
        assert target.read_bytes() != payload and not marker.exists()
        old.terminate()
        old.wait(5)
        assert worker.wait(15) == 0
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.read_text() == 'new-version'
        assert target.read_bytes() == installer.read_bytes()
        state = Updates('0.10.0', tmp_path).state.public()
        assert state['installation']['status'] == 'complete'
    finally:
        if old.poll() is None:
            old.terminate()
            old.wait(5)
        if worker.poll() is None:
            worker.kill()
            worker.wait(5)


def test_tampered_download_is_refused_before_ready_or_replacement(tmp_path):
    target = tmp_path / 'Installed.AppImage'
    target.write_bytes(b'old')
    path, installer = request(tmp_path, type('Parent', (), {'pid': os.getpid()})(), target, b'new')
    installer.write_bytes(b'tampered')
    assert update_install.main(str(path)) == 1
    assert target.read_bytes() == b'old'
    assert not path.with_suffix('.ready').exists()
    assert Updates('0.9.0', tmp_path).state.public()['installation']['status'] == 'error'


def test_native_installer_commands_preserve_paths_and_authentication(tmp_path, monkeypatch):
    monkeypatch.setattr(update_install.shutil, 'which', lambda command: '/usr/bin/' + command)
    path = tmp_path / 'Omote with spaces.deb'
    assert update_install.install_command(path, system='linux') == [
        '/usr/bin/pkexec',
        '/usr/bin/apt-get',
        'install',
        '--reinstall',
        '-y',
        str(path),
    ]
    assert update_install.install_command(tmp_path / 'Omote.msi', system='win32')[-1] == '/norestart'
    assert update_install.install_command(tmp_path / 'Omote_x64-setup.exe', system='win32') == [
        str(tmp_path / 'Omote_x64-setup.exe')
    ]
    monkeypatch.setattr(update_install.shutil, 'which', lambda _: None)
    with pytest.raises(ValueError, match='authentication'):
        update_install.install_command(path, system='linux')


def test_package_installations_do_not_silently_switch_to_appimage(monkeypatch):
    monkeypatch.setenv('OMOTE_INSTALL_KIND', 'deb')
    assert installation_kind() == 'deb'
    assets = [Asset('Omote_0.10.0_amd64.AppImage', 'image'), Asset('Omote_0.10.0_amd64.deb', 'package')]
    assert pick_asset(assets, key='linux-x64', kind=installation_kind()).url == 'package'
    assert pick_asset(assets[:1], key='linux-x64', kind='deb') is None
