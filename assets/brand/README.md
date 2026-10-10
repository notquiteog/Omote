# Omote identity

A folded ivory O on a charcoal tile: one surface turning to reveal another.
The transparent master was generated with the built-in image-generation tool.
The exact prompt is in [generation-prompt.txt](generation-prompt.txt).
The master is kept unchanged; all app assets are size/format derivatives.

Run `python desktop/ci/build_icons.py` with Pillow installed to reproduce the
web, desktop, Windows ICO, macOS ICNS, and VS Code variants. Committed derivatives
let app builds work without Pillow. Do not generate variants independently:
they must all use the same master.

The app logo is a stable identity. Companions remain selectable in their own
sidebar control and conversation surfaces; selecting one does not change the logo
or favicon. The charcoal tile supplies contrast on both light and dark themes.
