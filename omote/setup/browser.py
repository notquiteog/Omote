"""Activate private Playwright packages without modifying the app environment."""
import importlib
import json
import os
import sys
from pathlib import Path


def activate(data_dir=None):
    if data_dir is None:
        from omote.config import config
        data_dir = config.data_dir
    root = Path(data_dir).resolve() / 'tools' / 'setup'
    try:
        manifest = json.loads((root / 'browser.json').read_text())
        if not isinstance(manifest, dict) or manifest.get('python') != f'{sys.version_info.major}.{sys.version_info.minor}':
            return
        packages = (root / manifest['packages']).resolve()
        if not packages.is_relative_to(root.resolve()) or not packages.is_dir():
            return
        # Append so an already installed/imported Playwright remains authoritative.
        # Setup installs the exact same version when it is present in the app.
        if str(packages) not in sys.path:
            sys.path.append(str(packages))
            importlib.invalidate_caches()
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(root / 'browsers'))
    except (OSError, ValueError, KeyError, TypeError):
        return
