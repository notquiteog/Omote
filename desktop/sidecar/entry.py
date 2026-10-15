"""The daemon, as the desktop app carries it.

PyInstaller freezes this rather than `omote.main` directly because it needs
a script, not a module, to start from. It is the same `main()` that the
`omote` command runs, with one thing put right first.
"""

import os
import sys

if getattr(sys, 'frozen', False) and sys.platform.startswith('linux'):
    # PyInstaller points the dynamic linker at the libraries it unpacked, and
    # that reaches every program this process starts — which, for a daemon
    # whose agent runs your shell, is every command it runs. `git` loading
    # this build's libssl instead of the system's is an error about symbol
    # versions that says nothing about why. The linker read the variable when
    # this process started, so dropping it here changes only the children.
    # Measured: without this, a child saw LD_LIBRARY_PATH=/tmp/_MEI….
    original = os.environ.pop('LD_LIBRARY_PATH_ORIG', None)
    if original is None:
        os.environ.pop('LD_LIBRARY_PATH', None)
    else:
        os.environ['LD_LIBRARY_PATH'] = original

if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--elevated-worker':
        from omote.elevation import worker_main

        worker_main(sys.argv[2], sys.argv[3])
    else:
        from omote.main import main

        main()
