"""ArtCraft's native engines and explicitly configured creative MCP bridges.

No model selects an executable. Installed command paths come from the operator,
PATH, or the app's managed data directory. Capability schemas come from the real
server. Some suite engines do not enforce folder scopes: this is reported rather
than claiming the working directory is an OS sandbox.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CraftApp:
    id: str
    label: str
    purpose: str
    scoped: bool = False
    flags: tuple[str, ...] = ()

    @property
    def documentation(self):
        name = 'agents.md' if self.id == 'filmcraft' else 'mcp.md'
        return f'https://github.com/storytold/{self.id}/blob/main/docs/{name}'


APPS = {app.id: app for app in (
    CraftApp('photocraft', 'PhotoCraft', 'Layered images, masks, retouching and raster exports', True),
    CraftApp('vectorcraft', 'VectorCraft', 'Vector artwork, illustration, SVG/PDF and raster exports', True, ('--headless',)),
    CraftApp('filmcraft', 'FilmCraft', 'Video timelines, color, audio, captions, video and interchange exports'),
    CraftApp('effectcraft', 'EffectCraft', 'Motion graphics, compositing, VFX and render queues', True),
    CraftApp('lightcraft', 'LightCraft', 'RAW development, photo library and image exports'),
    CraftApp('pdfcraft', 'PdfCraft', 'PDF editing, pages, rendering and exports', flags=('--root', '{workspace}')),
    CraftApp('designcraft', 'DesignCraft', 'Page layout, publishing and document exports'),
    CraftApp('figcraft', 'FigCraft', 'Interface design, components, prototypes and exports'),
    CraftApp('wordcraft', 'WordCraft', 'Document composition, editing and exports'),
    CraftApp('soundcraft', 'SoundCraft', 'Recording, audio timelines, mixing and exports'),
    CraftApp('deckcraft', 'DeckCraft', 'Presentation editing, rendering and exports'),
    CraftApp('cadcraft', 'CADCraft', 'Drafting, geometry and CAD exports'),
    CraftApp('gridcraft', 'GridCraft', 'Spreadsheets, charts, analysis and exports'),
)}


def executable(app: CraftApp):
    from omote.config import config

    variable = f'OMOTE_{app.id.upper()}_BIN'
    override = os.environ.get(variable)
    if override:
        return shutil.which(override)
    command = f'{app.id}-cli'
    found = shutil.which(command)
    if found:
        return found
    suffix = '.exe' if os.name == 'nt' else ''
    candidates = [config.data_dir / 'creative-apps' / app.id / 'bin' / (command + suffix)]
    if os.name == 'nt':
        for key in ('ProgramFiles', 'LOCALAPPDATA'):
            if os.environ.get(key):
                candidates.append(Path(os.environ[key]) / app.label / (command + suffix))
    if sys.platform == 'darwin':
        candidates.append(Path('/Applications') / f'{app.label}.app' / 'Contents' / 'MacOS' / command)
    return next((str(path.resolve()) for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def catalog():
    from omote.mcp.manager import manager

    rows = []
    for app in APPS.values():
        command = executable(app)
        rows.append({'id': app.id, 'label': app.label, 'purpose': app.purpose,
                     'available': bool(command), 'kind': 'local', 'executable': command,
                     'filesystem_scope': 'engine-enforced folders' if app.scoped else 'process account permissions',
                     'documentation': app.documentation,
                     'setup': f'Install {app.id}-cli or set OMOTE_{app.id.upper()}_BIN. Native tools and export formats are discovered on connection.'})
    for name, cfg in manager.configs.items():
        if not cfg.creative or not cfg.enabled:
            continue
        server = manager.servers.get(name)
        rows.append({'id': f'mcp:{name}', 'label': name, 'purpose': 'Operator-configured creative app bridge',
                     'available': bool(server and server.running), 'kind': 'connector',
                     'filesystem_scope': 'connector permissions', 'documentation': '',
                     'setup': 'Configure the app’s official/supported MCP bridge with creative: true in .mcp.json. Adobe apps require an installed app and an available bridge.'})
    return {'apps': rows, 'model_support': 'Any configured chat model with tool calling; visual review also needs image input.',
            'note': 'Availability means an engine or connector was found, not that every command, codec or app version has been tested.'}
