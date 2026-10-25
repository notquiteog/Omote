"""Shell commands for tests that run on both Windows and POSIX."""
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path


def python_command(directory: Path, source: str) -> str:
    script = directory / f'hook-{uuid.uuid4().hex}.py'
    script.write_text(source, encoding='utf-8')
    argv = [sys.executable, str(script)]
    return subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)
