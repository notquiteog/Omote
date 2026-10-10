"""Produce every Omote branding size from the unchanged generated master.

Run with Python + Pillow. This only resizes and encodes the supplied artwork.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / 'assets/brand/omote-master.png'


def build():
    with Image.open(MASTER) as original:
        master = original.convert('RGBA')

    def png(relative, size):
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        master.resize((size, size), Image.Resampling.LANCZOS).save(path, optimize=True)

    for size in (16, 32, 64, 128, 180, 192, 256, 512):
        png(f'omote/static/brand/omote-{size}.png', size)
    for name, size in {
        '32x32.png': 32, '128x128.png': 128, '128x128@2x.png': 256,
        '256x256.png': 256, '512x512.png': 512, 'icon.png': 1024,
    }.items():
        png(f'desktop/src-tauri/icons/{name}', size)
    png('desktop/ui/omote.png', 256)
    png('ide/vscode/media/omote.png', 256)
    icons = ROOT / 'desktop/src-tauri/icons'
    master.save(icons / 'icon.ico', sizes=[(n, n) for n in (16, 20, 24, 32, 48, 64, 128, 256)])
    master.resize((1024, 1024), Image.Resampling.LANCZOS).save(icons / 'icon.icns')
    print('Built Omote web, desktop, startup, and VS Code icons.')


if __name__ == '__main__':
    build()
