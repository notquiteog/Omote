"""Per-command elevation, with OS authentication and a bounded worker.

The worker receives the approved command over an authenticated loopback socket,
not a root-writable script or log in a user's temp folder. Closing the channel
requests cancellation inside the elevated worker, which can kill its own child.
No password is read, transmitted, or stored by Omote.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ctypes
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from omote.agent.tools.base import Output, ToolContext, ToolError, truncate

# Only stdlib imports: also runs under `python -I`, without user site packages.
# Kept as source so an editable install and the frozen sidecar use the same worker.
WORKER = r'''
import codecs
import contextlib
import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time

def worker(port, token):
    sock = socket.create_connection(('127.0.0.1', int(port)), timeout=10)
    sock.settimeout(10)
    stream = sock.makefile('rb')
    lock = threading.Lock()
    lost = threading.Event()

    def send(value):
        try:
            with lock:
                sock.sendall((json.dumps(value) + '\n').encode())
        except OSError:
            lost.set()

    send({'token': token})
    request = json.loads(stream.readline(262144))
    if request.get('token') != token:
        return
    command, cwd = request['command'], request['cwd']
    timeout = max(1, min(int(request['timeout']), 1800))
    windows = os.name == 'nt'
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if windows else {'start_new_session': True}
    proc = None

    def kill():
        if proc is None:
            return
        if windows:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)

    try:
        proc = subprocess.Popen(command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)

        def pump(pipe, which):
            decoder = codecs.getincrementaldecoder('utf-8')('replace')
            while True:
                chunk = os.read(pipe.fileno(), 4096)
                if not chunk:
                    break
                send({'stream': which, 'text': decoder.decode(chunk)})
            tail = decoder.decode(b'', final=True)
            if tail:
                send({'stream': which, 'text': tail})

        threads = [threading.Thread(target=pump, args=(pipe, name), daemon=True)
                   for pipe, name in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr'))]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        while proc.poll() is None:
            if lost.is_set() or time.monotonic() >= deadline:
                timed_out = not lost.is_set()
                kill()
                break
            readable, _, _ = select.select([sock], [], [], .1)
            if readable:
                # The parent closes its channel on interrupt/shutdown. No
                # additional commands can ever be sent over this channel.
                sock.recv(1)
                kill()
                break
        proc.wait(timeout=10)
        # Foreground commands may not strand children holding output pipes.
        if not windows:
            kill()
        for thread in threads:
            thread.join(timeout=2)
        send({'exit_code': proc.returncode, 'timed_out': timed_out})
    except Exception as error:
        with contextlib.suppress(Exception):
            kill()
        send({'error': str(error)})
    finally:
        stream.close()
        sock.close()
'''


def worker_main(port: str, token: str) -> None:
    namespace: dict[str, Any] = {}
    exec(WORKER, namespace)  # noqa: S102 - fixed, package-owned code
    namespace['worker'](port, token)


def worker_argv(port: int, token: str) -> list[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--elevated-worker', str(port), token]
    source = WORKER + '\nworker(' + repr(str(port)) + ', ' + repr(token) + ')\n'
    encoded = base64.b64encode(source.encode()).decode()
    return [sys.executable, '-I', '-c', f'import base64;exec(base64.b64decode("{encoded}"))']


def launch_argv(worker: list[str], system: str | None = None) -> list[str]:
    system = system or sys.platform
    if system == 'win32':
        if ctypes.windll.shell32.IsUserAnAdmin():
            return worker
        powershell = shutil.which('powershell') or shutil.which('pwsh')
        if not powershell:
            raise ToolError('Administrator commands need PowerShell for the native Windows UAC prompt.')
        def quote(value):
            return "'" + value.replace("'", "''") + "'"
        arguments = subprocess.list2cmdline(worker[1:])
        script = ("$ErrorActionPreference='Stop'; "
                  f'$p = Start-Process -FilePath {quote(worker[0])} -ArgumentList {quote(arguments)} '
                  '-Verb RunAs -Wait -PassThru; exit $p.ExitCode')
        encoded = base64.b64encode(script.encode('utf-16-le')).decode()
        return [powershell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        return worker
    sudo = shutil.which('sudo')
    if sudo:
        try:
            if subprocess.run([sudo, '-n', 'true'], capture_output=True, timeout=3, check=False).returncode == 0:
                return [sudo, '-n', '--', *worker]
        except (OSError, subprocess.TimeoutExpired):
            pass
    if system == 'darwin':
        # AppleScript quoting is independent of shell quoting.
        command = shlex.join(worker).replace('\\', '\\\\').replace('"', '\\"')
        return ['/usr/bin/osascript', '-e', 'with timeout of 1940 seconds\n'
                f'do shell script "{command}" with administrator privileges\nend timeout']
    pkexec = shutil.which('pkexec')
    if pkexec:
        return [pkexec, '--disable-internal-agent', *worker]
    raise ToolError('Administrator access needs passwordless sudo or a desktop polkit agent (pkexec) on Linux. '
                    'No password is accepted through chat. Run the command in your own terminal if neither is available.')


async def run_admin(command: str, cwd: Path, timeout: int, ctx: ToolContext, max_output: int = 30_000) -> Output:  # noqa: ASYNC109 - bound in privileged worker too
    token = secrets.token_hex(32)
    loop = asyncio.get_running_loop()
    connected = loop.create_future()
    clients: set[asyncio.StreamWriter] = set()

    async def accept(reader, writer):
        clients.add(writer)
        handed_off = False
        try:
            hello = json.loads(await asyncio.wait_for(reader.readline(), timeout=3))
            if isinstance(hello, dict) and secrets.compare_digest(hello.get('token', ''), token) and not connected.done():
                connected.set_result((reader, writer))
                handed_off = True
        except (ValueError, TypeError, TimeoutError, OSError):
            pass
        finally:
            if not handed_off:
                writer.close()
                clients.discard(writer)

    server = await asyncio.start_server(accept, '127.0.0.1', 0, limit=262144)
    proc = None
    writer = None
    chunks = []
    size = 0
    try:
        port = server.sockets[0].getsockname()[1]
        argv = await asyncio.to_thread(launch_argv, worker_argv(port, token))
        await ctx.emit('Requesting administrator access through the operating system…\n', 'stderr')
        proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
                                                   stdout=asyncio.subprocess.DEVNULL,
                                                   stderr=asyncio.subprocess.DEVNULL)
        # Do not hang for two minutes when a native prompt is declined.
        async def wait_connection():
            while not connected.done():
                if proc.returncode is not None:
                    raise ToolError(f'Administrator access was declined or unavailable (launcher exit {proc.returncode}).')
                await asyncio.sleep(.1)
            return connected.result()

        try:
            reader, writer = await asyncio.wait_for(wait_connection(), timeout=120)
        except TimeoutError as exc:
            raise ToolError('Administrator authentication timed out. No command was sent.') from exc
        server.close()
        writer.write((json.dumps({'token': token, 'command': command, 'cwd': str(cwd), 'timeout': timeout}) + '\n').encode())
        await writer.drain()
        async with asyncio.timeout(timeout + 20):
            while True:
                line = await reader.readline()
                if not line:
                    raise ToolError('Administrator worker disconnected before reporting a result; check the command state.')
                event = json.loads(line)
                if 'error' in event:
                    raise ToolError(f'Administrator command failed: {event["error"]}')
                if 'text' in event:
                    text = event['text']
                    await ctx.emit(text, event['stream'])
                    if size < max_output * 2:
                        chunks.append(text)
                        size += len(text)
                    continue
                if 'exit_code' in event:
                    body, truncated = truncate(''.join(chunks), max_output, keep='both')
                    code, timed_out = event['exit_code'], event['timed_out']
                    content = (f'Administrator command timed out after {timeout}s and was stopped.\n\n{body}' if timed_out
                               else (body or '(no output)') if code == 0 else f'Exit code {code}\n\n{body}')
                    return Output(content=content, display={'exit_code': code, 'timed_out': timed_out,
                                  'command': command, 'cwd': str(cwd), 'elevated': True}, truncated=truncated)
    except TimeoutError as exc:
        raise ToolError('Administrator worker stopped responding. Cancellation was requested; verify the command state.') from exc
    finally:
        # EOF is a cancellation signal to the privileged supervisor, which
        # can stop children an unprivileged parent cannot signal directly.
        server.close()
        for client in clients:
            client.close()
        await server.wait_closed()
        if writer:
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        if proc and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    proc.terminate()
