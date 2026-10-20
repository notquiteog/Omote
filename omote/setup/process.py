"""Bounded logs and cancellable process trees, also in frozen desktop builds."""
import asyncio
import contextlib
import os
import re
import sys

from omote.agent.tools.shell import _kill_tree, _spawn_kwargs


class SetupError(RuntimeError):
    pass


class SetupHelp(SetupError):
    """A short, actionable message safe to show without technical details."""


def environment():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('UV_', 'PIP_', 'PYTHON', '_PYI')) and k != 'VIRTUAL_ENV'}
    # A frozen daemon's private libraries must not replace a child's own ones.
    for key in ('LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
        if key + '_ORIG' in env:
            env[key] = env.pop(key + '_ORIG')
        elif getattr(sys, 'frozen', False):
            env.pop(key, None)
    env.update(PYTHONUNBUFFERED='1', PYTHONNOUSERSITE='1')
    return env


async def spawn(args, *, cwd, env):
    options = _spawn_kwargs()
    if os.name == 'nt':
        import subprocess
        options['creationflags'] |= subprocess.CREATE_NO_WINDOW
    # PyInstaller's Windows DLL search override is inherited by child programs.
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            return await asyncio.create_subprocess_exec(
                *map(str, args), cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **options)
        finally:
            ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)
    return await asyncio.create_subprocess_exec(
        *map(str, args), cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **options)


def clean(text):
    # Package managers may echo proxy/signed URLs. Diagnostics never need them.
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    return re.sub(r'https?://[^\s<>"\']+', '[download source]', text)[-12000:]


async def terminate(process):
    if process and process.returncode is None:
        await _kill_tree(process)
        await process.wait()


async def run(args, *, cwd, env, log=None, timeout=1800):  # noqa: ASYNC109 - timeout bounds a subprocess tree
    process = await spawn(args, cwd=cwd, env=env)
    output = ''
    async def read():
        nonlocal output
        while chunk := await process.stdout.read(4096):
            output = (output + chunk.decode(errors='replace'))[-24000:]
            if log:
                log(clean(output))
        return await process.wait()
    reader = asyncio.create_task(read())
    try:
        async with asyncio.timeout(timeout):
            code = await asyncio.shield(reader)
        if code:
            raise SetupError(f'Setup step failed (code {code}). {clean(output)[-2000:]}')
        return output
    finally:
        await terminate(process)
        with contextlib.suppress(asyncio.CancelledError):
            await reader
