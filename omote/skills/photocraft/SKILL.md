---
name: photocraft
description: Edit layered images with open-source PhotoCraft, discover engine commands, inspect previews, or drive its authenticated desktop MCP bridge.
argument-hint: "[document and requested edits]"
---

Use the `photocraft` tool for headless editing. `status` reports whether the
installed CLI is available. PhotoCraft is a separate open-source application;
Omote does not bundle it or pretend to have installed it.

1. Inspect the source with `action: inspect`. Record dimensions and layers, and
   look at the returned image. If creating a document, use `new` with dimensions,
   color mode, depth and background appropriate to the requested output.
2. Discover actual command IDs and parameters with `action: commands` and a
   narrow filter. Never invent a command or assume an Adobe script runs here.
3. Use `action: edit`, `source` (or `new`), ordered `commands` with `id` and
   `params`, and a separate `output`. Keep layered PSD/.pcraft working files;
   export a different PNG/JPEG for delivery. Reopen the working file to continue.
   The tool closes its engine after each call and never preserves hidden state.
4. Inspect the returned document report and PNG preview. Surface import/export
   warnings, especially flattened or unsupported features. If a command fails,
   the tool stops before its save; report the failed step and retry deliberately.
   A successfully saved file remains saved if a later preview fails.
5. For a series of images, validate one first, then repeat the same verified
   commands with distinct outputs. Use installed PhotoCraft batch tooling only
   after reading its local help and applying the same approval and path rules.

For interactive PhotoCraft sessions, use an operator-configured MCP server whose
command is `photocraft-cli mcp --bridge 127.0.0.1:7878 --control-token-file
/private/path/photocraft-control.token`. The desktop app must already run with
`--control`, that token file, and deliberately chosen automation read/write
roots. Use its discovered `ui_inspect`, `ui_screenshot`, `command_list` and
`command_run` schemas. Never echo the token or disable authentication. Desktop
screenshots and pointer tools are a fallback when a bridge is unavailable.

Reference: https://github.com/storytold/photocraft/blob/main/docs/mcp.md
