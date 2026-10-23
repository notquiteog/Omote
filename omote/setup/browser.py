"""Activate private Playwright packages without modifying the app environment."""
import importlib
import json
import os
import re
import sys
from pathlib import Path


def version_supported(version):
    from omote.setup.catalog import PLAYWRIGHT_VERSION
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        return False
    return tuple(map(int, version.split('.'))) >= tuple(map(int, PLAYWRIGHT_VERSION.split('.')))


def activate(data_dir=None):
    if data_dir is None:
        from omote.config import config
        data_dir = config.data_dir
    root = Path(data_dir).resolve() / 'tools' / 'setup'
    try:
        manifest = json.loads((root / 'browser.json').read_text())
        if not isinstance(manifest, dict) or manifest.get('python') != f'{sys.version_info.major}.{sys.version_info.minor}':
            return
        # Private environments survive app updates. Do not reactivate a driver
        # whose bundled Node predates the October 2026 security fixes.
        if not version_supported(manifest.get('version')):
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
