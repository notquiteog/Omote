"""Detached installer worker: wait for the old app to exit, install, then reopen.

Run by a staged copy of the bundled daemon, never by the conversational agent.
The OS installer owns authentication; passwords never enter this process.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psutil


def read_json(path):
    if path.stat().st_size > 32768:
        raise ValueError('Update metadata is too large.')
    return json.loads(path.read_text())


def record(directory, status, message, version=''):
    target = directory / 'install-result.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'status': status, 'message': message, 'version': version, 'at': time.time()}))
    temporary.replace(target)


def validate(request):
    data = read_json(request)
    installer = Path(data['installer'])
    if installer.is_symlink() or not installer.is_file() or installer.resolve().parent != request.resolve().parent:
        raise ValueError('The installer is not a regular staged download.')
    proof = read_json(installer.with_name(installer.name + '.verified.json'))
    digest = hashlib.sha256()
    with installer.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            digest.update(chunk)
    if (
        proof.get('name') != installer.name
        or proof.get('sha256') != digest.hexdigest()
        or proof.get('bytes') != installer.stat().st_size
    ):
        raise ValueError('The staged installer changed after verification. Download it again.')
    launcher = Path(data['launcher'])
    if not launcher.is_absolute() or type(data['parent']) is not int or data['parent'] < 2:
        raise ValueError('Invalid desktop handoff.')
    return data, installer, launcher, str(proof['version'])


def install_command(installer, *, system=None):
    system = system or sys.platform
    extension = installer.suffix.lower()
    if system == 'win32':
        if extension == '.msi':
            return ['msiexec', '/i', str(installer), '/qb', '/norestart']
        if installer.name.lower().endswith('-setup.exe'):
            return [str(installer)]
    elif system.startswith('linux'):
        pkexec = shutil.which('pkexec')
        if not pkexec:
            raise ValueError(
                'No system authentication helper was found. Install the downloaded package with your package manager.'
            )
        if extension == '.deb' and shutil.which('apt-get'):
            return [pkexec, shutil.which('apt-get'), 'install', '--reinstall', '-y', str(installer)]
        if extension == '.rpm':
            for command in ('dnf', 'zypper'):
                binary = shutil.which(command)
                if binary:
                    return [pkexec, binary, 'install', '-y', str(installer)]
    raise ValueError('This installer needs to be opened manually from the release page.')


def replace_appimage(installer, launcher):
    if launcher.suffix.lower() != '.appimage' or not launcher.is_file() or launcher.is_symlink():
        raise ValueError('Could not identify the original AppImage. Install the downloaded file manually.')
    temporary = launcher.with_name(launcher.name + '.update-' + uuid.uuid4().hex)
    try:
        shutil.copyfile(installer, temporary)
        temporary.chmod(0o755)
        temporary.replace(launcher)
    finally:
        temporary.unlink(missing_ok=True)


def restart(launcher):
    env = dict(os.environ)
    for key in ('OMOTE_EXIT_WITH_STDIN', 'APPIMAGE', 'APPDIR', 'ARGV0', 'OWD'):
        env.pop(key, None)
    subprocess.Popen(
        [str(launcher)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main(request_path):
    request = Path(request_path)
    directory = request.parent
    launcher, version, parent_exited = None, '', False
    try:
        data, installer, launcher, version = validate(request)
        # Capture the process identity before announcing readiness; psutil.wait
        # handles PID reuse and never kills an app or an external daemon.
        try:
            parent = psutil.Process(data['parent'])
        except psutil.NoSuchProcess:
            parent = None
        request.with_suffix('.ready').write_text('ready')
        if parent:
            parent.wait(timeout=180)
        parent_exited = True
        record(directory, 'installing', 'Installing the downloaded update…', version)
        if installer.suffix.lower() == '.appimage':
            replace_appimage(installer, launcher)
        else:
            completed = subprocess.run(install_command(installer), stdin=subprocess.DEVNULL, timeout=1800, check=False)
            if completed.returncode not in ((0, 3010) if sys.platform == 'win32' else (0,)):
                raise ValueError(
                    f'The system installer did not finish (exit {completed.returncode}). It may have been canceled. The downloaded file is still available.'
                )
        record(directory, 'complete', f'Updated to {version}.', version)
        restart(launcher)
        return 0
    except Exception as error:  # noqa: BLE001 — persist failures after the webview exits
        record(directory, 'error', str(error), version)
        if launcher and parent_exited:
            try:
                restart(launcher)
            except OSError:
                pass
        print(f'Update failed: {error}', file=sys.stderr)
        return 1
    finally:
        request.with_suffix('.ready').unlink(missing_ok=True)
        request.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
