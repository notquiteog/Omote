## Omote 0.8.0

This release introduces the Omote name and folded O identity across the desktop,
web workspace, startup screen, installer icons and VS Code extension.

### Creative workspace

- Full connected ComfyUI editor, including Nodes 2.0 when supported by the installed
  frontend, plus an optional bridge for capturing and restoring complete graphs.
- Workflow versions, project labels, installed-node validation, API input inspection,
  run history and imports of finished outputs into the local asset library.
- Reusable character profiles, ordered identity references and continuity notes.
  Reference selection follows the chosen model's declared image inputs.
- Image/video assets and self-contained GLB, GLTF, OBJ and STL previews with orbit,
  wireframe, animation playback, provenance and library export.
- PhotoCraft integration for layered editing through its separately installed CLI.

### Agent and workspace improvements

- Browser-based employee portals, email, travel and personal-assistant workflows,
  multilingual action recognition, native dropdowns and clearer confirmations.
- Inspectable, editable and exportable user/project memory with separate embeddings.
- Codex-style workspace, accessible mobile navigation, nine companions and improved
  companion behavior. The selected companion no longer replaces the app identity.
- Configurable STT/LLM/TTS voice and the OpenAI Realtime API integration.
- More robust MCP handling for large image previews and malformed responses.

### Setup and upgrading

Back up your existing data folder and stop the previous app before changing paths.
The executable/package is now `omote`, the MCP command is `omote-mcp`, and settings
use `OMOTE_` / `omote.*`. Set `OMOTE_DATA_DIR` to the existing data folder to retain
sessions, media and memories. The desktop app's default state folder is `~/.omote`.
Review launch scripts and browser-local preferences after upgrading.

Model providers, ComfyUI/custom nodes, PhotoCraft and external accounts are separate
services. This installer does not bundle model weights or credentials. Native
Playwright browser tools currently require a separately installed `omote[browser]`
daemon and `playwright install chromium`; start that daemon before opening the
app so the app attaches to it. Voice needs microphone access; Live uses OpenAI API
access, not a ChatGPT subscription.

See [Workspace setup](https://github.com/notquiteog/Omote/blob/v0.8.0/docs/WORKSPACE.md),
[Creative workspace](https://github.com/notquiteog/Omote/blob/v0.8.0/docs/CREATIVE.md),
and [Business workflows](https://github.com/notquiteog/Omote/blob/v0.8.0/docs/BUSINESS.md).

Each installer contains the Omote app and its daemon, with its own Python runtime. Optional tools and services have the setup requirements above. It keeps its data, its logs and an optional `.env` in `~/.omote`. If a daemon is already running on port 8477, one you started yourself, the app uses that one and leaves it running when it quits.

## Windows

`…_x64-setup.exe` installs for you alone, with no administrator prompt; the `.msi` is an alternative Windows installer.

The installers are not code-signed yet, so SmartScreen will say it protected your PC. Choose **More info**, then **Run anyway**.

## macOS

`…_aarch64.dmg` is for Apple Silicon, `…_x64.dmg` for Intel. Open it and drag Omote into Applications.

Omote is not notarised yet — the project has no Apple Developer ID — so the first time you open it, macOS will refuse. Either go to **System Settings → Privacy & Security**, find the line about Omote near the bottom and choose **Open Anyway**, or run this once:

```
xattr -dr com.apple.quarantine /Applications/Omote.app
```

Nobody on the project owns a Mac. These builds are installed and started on GitHub's macOS machines before they are published here, which is the whole of their testing: reports from real Macs are especially welcome.

## Linux

- Debian, Ubuntu: `sudo apt install ./omote_…_amd64.deb`
- Fedora, openSUSE: `sudo dnf install ./omote-….x86_64.rpm`
- Anything else: the `.AppImage` — `chmod +x` it and run it. If it will not start, install `libfuse2`.

`SHA256SUMS.txt` has the checksum of every file here.
