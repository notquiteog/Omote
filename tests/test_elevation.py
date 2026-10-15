"""Native elevation routes plus the real bounded worker, without elevating tests."""

import asyncio
import base64
import ctypes
import os
import shlex
import sys
import threading
from types import SimpleNamespace

import pytest

from omote import elevation
from omote.agent.approval import ApprovalPolicy, Mode
from omote.agent.session import AgentSession
from omote.agent.tools.base import ToolError
from omote.agent.tools.shell import ShellTool
from omote.protocol.agent import Risk, ToolCall
from tests.test_agent import ScriptedProvider
from tests.test_system import ctx


@pytest.fixture
def no_native_elevation(monkeypatch):
    # Exercise real IPC/process control as the current user. OS prompts are
    # tested by inspecting launch arguments; no test suite asks for root.
    monkeypatch.setattr(elevation, 'launch_argv', lambda worker: worker)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX command syntax')
async def test_worker_streams_unicode_stderr_exit_status_and_approved_cwd(no_native_elevation, tmp_path):
    context = ctx()
    events = []

    async def emit(text, stream):
        events.append((text, stream))

    context.emit = emit
    command = shlex.join([sys.executable, '-c', "import os,sys; print('héllo'); print(os.getcwd()); print('problem',file=sys.stderr); sys.exit(7)"])
    result = await elevation.run_admin(command, tmp_path, 5, context)
    assert result.display['exit_code'] == 7
    assert str(tmp_path) in result.content and 'héllo' in result.content and 'problem' in result.content
    assert any(stream == 'stderr' and 'problem' in text for text, stream in events)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process groups')
async def test_worker_timeout_stops_children(no_native_elevation, tmp_path):
    marker = tmp_path / 'should-not-exist'
    code = f'import time,pathlib; time.sleep(2); pathlib.Path({str(marker)!r}).touch()'
    result = await elevation.run_admin(shlex.join([sys.executable, '-c', code]), tmp_path, 1, ctx())
    assert result.display['timed_out'] and 'timed out' in result.content
    await asyncio.sleep(1.2)
    assert not marker.exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process groups')
async def test_interrupt_disconnect_cancels_inside_the_worker(no_native_elevation, tmp_path):
    ready, marker = tmp_path / 'ready', tmp_path / 'should-not-exist'
    code = f'import time,pathlib; pathlib.Path({str(ready)!r}).touch(); time.sleep(2); pathlib.Path({str(marker)!r}).touch()'
    task = asyncio.create_task(elevation.run_admin(shlex.join([sys.executable, '-c', code]), tmp_path, 10, ctx()))
    async with asyncio.timeout(5):
        while not ready.exists():  # noqa: ASYNC110 - observe a real child process, not another coroutine
            await asyncio.sleep(.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.1)
    assert not marker.exists()


def test_elevation_is_always_execute_and_keeps_destructive_classification():
    tool = ShellTool()
    assert tool.assess({'command': 'whoami', 'elevated': True}, ctx()).risk is Risk.EXECUTE
    assert 'administrator' in tool.assess({'command': 'whoami', 'elevated': True}, ctx()).summary
    assert tool.assess({'command': 'rm -rf build', 'elevated': True}, ctx()).risk is Risk.DESTRUCTIVE
    for arguments in ({'elevated': 'false'}, {'elevated': True, 'background': True}, {'elevated': True, 'timeout': -1}):
        assert tool.assess({'command': 'whoami', **arguments}, ctx()).invalid


async def test_read_only_policy_never_enters_admin_launcher(monkeypatch, tmp_path):
    async def forbidden(*args, **kwargs):
        pytest.fail('read-only policy reached the administrator launcher')

    monkeypatch.setattr(elevation, 'run_admin', forbidden)
    session = AgentSession(root=tmp_path, provider=ScriptedProvider([]), model='test', tools=[ShellTool()],
                           policy=ApprovalPolicy(mode=Mode.READ_ONLY))
    result = await session.invoke(ToolCall(id='admin', name='shell', arguments={'command': 'whoami', 'elevated': True}), 't')
    assert result.is_error


@pytest.mark.parametrize('hello', [b'{"token":"wrong"}\n', b'[]\n'])
async def test_bad_worker_token_receives_no_command(monkeypatch, tmp_path, hello):
    ready = asyncio.Event()
    gate = threading.Event()
    details = {}
    original = elevation.worker_argv

    def worker_argv(port, token):
        details['port'] = port
        ready.set()
        return original(port, token)

    def delayed(worker):
        assert gate.wait(timeout=5)
        return worker

    monkeypatch.setattr(elevation, 'worker_argv', worker_argv)
    monkeypatch.setattr(elevation, 'launch_argv', delayed)
    task = asyncio.create_task(elevation.run_admin('echo authenticated', tmp_path, 3, ctx()))
    try:
        await asyncio.wait_for(ready.wait(), timeout=3)
        reader, writer = await asyncio.open_connection('127.0.0.1', details['port'])
        writer.write(hello)
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), timeout=3) == b''
        writer.close()
        await writer.wait_closed()
    finally:
        gate.set()
    result = await task
    assert 'authenticated' in result.content


async def test_declined_native_launcher_does_not_send_command(monkeypatch, tmp_path):
    monkeypatch.setattr(elevation, 'launch_argv', lambda worker: [sys.executable, '-c', 'raise SystemExit(3)'])
    with pytest.raises(ToolError, match='declined or unavailable'):
        await asyncio.wait_for(elevation.run_admin('never execute this', tmp_path, 3, ctx()), timeout=5)


async def test_elevated_output_is_bounded(no_native_elevation, tmp_path):
    if os.name == 'nt':
        pytest.skip('POSIX command quoting')
    command = shlex.join([sys.executable, '-c', "print('x' * 200000)"])
    result = await elevation.run_admin(command, tmp_path, 3, ctx(), max_output=2000)
    assert result.truncated and len(result.content) < 2100


@pytest.fixture
def ordinary_user(monkeypatch):
    monkeypatch.setattr(elevation.os, 'geteuid', lambda: 1000, raising=False)
    monkeypatch.setattr(elevation.shutil, 'which', lambda name: None)


def test_linux_root_sudo_and_polkit_routes(ordinary_user, monkeypatch):
    worker = ['/python with spaces', '-I', '-c', 'fixed worker']
    monkeypatch.setattr(elevation.os, 'geteuid', lambda: 0)
    assert elevation.launch_argv(worker, 'linux') == worker
    monkeypatch.setattr(elevation.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(elevation.shutil, 'which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(elevation.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    assert elevation.launch_argv(worker, 'linux') == ['/usr/bin/sudo', '-n', '--', *worker]
    monkeypatch.setattr(elevation.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    assert elevation.launch_argv(worker, 'linux') == ['/usr/bin/pkexec', '--disable-internal-agent', *worker]


def test_linux_without_auth_agent_fails_instead_of_prompting_in_chat(ordinary_user):
    with pytest.raises(ToolError, match='No password is accepted through chat'):
        elevation.launch_argv(['/python', '-I'], 'linux')


def test_mac_uses_native_admin_auth_and_two_layers_of_quoting(ordinary_user):
    argv = elevation.launch_argv(["/Applications/Omote's App/python", '-c', 'print("a\\nb")'], 'darwin')
    assert argv[:2] == ['/usr/bin/osascript', '-e']
    assert 'with administrator privileges' in argv[2]
    assert 'with timeout of 1940 seconds' in argv[2]
    assert '\\"' in argv[2] and "Omote" in argv[2]


def test_windows_uac_preserves_argument_boundaries(ordinary_user, monkeypatch):
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: False)), raising=False)
    monkeypatch.setattr(elevation.shutil, 'which', lambda name: 'powershell.exe' if name == 'powershell' else None)
    argv = elevation.launch_argv([r'C:\Program Files\Omote\daemon.exe', '--elevated-worker', '12345', 'token'], 'win32')
    script = base64.b64decode(argv[-1]).decode('utf-16-le')
    assert argv[:3] == ['powershell.exe', '-NoProfile', '-NonInteractive']
    assert '-Verb RunAs -Wait -PassThru' in script
    assert r"'C:\Program Files\Omote\daemon.exe'" in script
    assert "'--elevated-worker 12345 token'" in script


def test_frozen_sidecar_worker_uses_its_own_entry_point(monkeypatch):
    monkeypatch.setattr(elevation.sys, 'frozen', True, raising=False)
    assert elevation.worker_argv(12345, 'token') == [sys.executable, '--elevated-worker', '12345', 'token']


async def test_system_package_installs_share_the_cancellable_admin_worker(monkeypatch):
    from omote.agent.tools.base import Output
    from omote.agent.tools.system import PackageInstallTool
    from omote.system import WINDOWS_MANAGERS, Platform
    from tests.test_system import linux

    commands = []

    async def fake_admin(command, cwd, deadline, context):
        commands.append(command)
        return Output(content='installed', display={'exit_code': 0, 'timed_out': False})

    monkeypatch.setattr(elevation, 'run_admin', fake_admin)
    await PackageInstallTool(linux(elevation='pkexec')).run({'what': 'steam'}, ctx())
    assert commands[-1].startswith('env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install')
    windows = Platform(system='windows', managers=WINDOWS_MANAGERS, elevation='uac')
    await PackageInstallTool(windows).run({'what': 'demo', 'manager': 'choco', 'package': "demo & 'quoted'"}, ctx())
    encoded = commands[-1].split()[-1]
    script = base64.b64decode(encoded).decode('utf-16-le')
    assert "'demo & ''quoted'''" in script
    assert "'choco'" in script
