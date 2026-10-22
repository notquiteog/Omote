"""Run local Linux generation experiments inside a hard memory budget.

Use this around the managed ComfyUI server, not just its HTTP client. The
server and its children share the cap. Refuse unsupported hosts rather than
silently starting an unbounded job. No swap files or system settings change.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

import psutil

GIB = 1024**3
MIB = 1024**2


def memory_budget(total: int, available: int, requested: float | None = None) -> int:
    reserve = max(6 * GIB, total // 5)
    maximum = min(total * 3 // 5, available - reserve) // MIB * MIB
    chosen = maximum if requested is None else int(requested * GIB) // MIB * MIB
    if chosen < 64 * MIB or chosen > maximum:
        raise ValueError('Not enough available memory for this budget while keeping the desktop responsive.')
    return chosen


def command(program: list[str], limit: int, systemd: str) -> list[str]:
    return [systemd, '--user', '--scope', '--quiet', '--collect', f'--property=MemoryMax={limit}',
            '--property=MemorySwapMax=0', '--', *program]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory-gib', type=float)
    parser.add_argument('program', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    program = args.program[1:] if args.program[:1] == ['--'] else args.program
    if not program:
        parser.error('Provide the managed server command after --.')
    systemd = shutil.which('systemd-run')
    if sys.platform != 'linux' or not systemd:
        parser.error('This validation launcher needs Linux with a systemd user session. Nothing was started.')
    memory = psutil.virtual_memory()
    try:
        limit = memory_budget(memory.total, memory.available, args.memory_gib)
    except (ValueError, OverflowError) as error:
        parser.error(str(error))
    print(f'Generation memory limit: {limit / GIB:.1f} GiB; swap disabled for this job.', flush=True)
    code = subprocess.run(command(program, limit, systemd)).returncode
    return code if code >= 0 else 128 - code


if __name__ == '__main__':
    raise SystemExit(main())
