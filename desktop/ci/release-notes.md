## Omote 0.9.0

### Managed local models

- Download verified GGUF chat models with persistent cancel/resume, SHA-256 checks,
  disk/RAM/VRAM preflight, and explicit GPU or CPU mode.
- Install a pinned llama.cpp runtime, start or stop an owned local server, and use
  a downloaded model directly in chat. Private runtime credentials, process recovery
  and actual context-window limits keep local sessions predictable.
- Studio’s Models & workflows library manages ComfyUI safetensors and JSON workflow
  downloads, live node/model compatibility checks, native workflow exports and
  Workbench imports. An SD Turbo starter recipe preserves its sampling defaults.
- ComfyUI needs an existing installation and the one-time managed model-path setup
  shown in Studio. Downloads use the network; inference stays on the selected backend.

### Workspace improvements

- Lazy tool discovery loads optional schemas only when needed, with a bounded cache
  that preserves toolset restrictions and approval rules.
- A simpler Brain interface for viewing, creating, editing, deleting and exporting
  memories, with improved automatic capture, filtering and duplicate handling.
- Live CPU, RAM, disk and supported GPU monitoring, plus per-command root/admin
  access through native Linux, macOS and Windows authentication paths.
- More space for the native ComfyUI editor, a collapsible workbench, focus mode,
  responsive Studio controls, and connection, voice and provider fixes.

### Verification and setup

The local regression suite passed 2,716 tests with five skipped, followed by
focused checks for final workflow defaults and exports. Real Linux RTX 5060 Ti
checks covered Qwen chat, interrupted/resumed model downloads and 512 × 512 image
creation through Studio and ComfyUI. Native macOS/Windows GPU inference and admin
prompts still need verification on user hardware; installer smoke tests run in CI.

Model weights, ComfyUI/custom nodes, PhotoCraft and external provider accounts are
separate downloads or services. SD Turbo is labeled for non-commercial research;
review each model publisher’s license. Automatic multi-file diffusion dependency
installation is not included. Optional native browser automation needs a separately
installed `omote[browser]` daemon and `playwright install chromium`.

See [Workspace setup](https://github.com/notquiteog/Omote/blob/v0.9.0/docs/WORKSPACE.md),
[Creative workspace](https://github.com/notquiteog/Omote/blob/v0.9.0/docs/CREATIVE.md),
and [Capability audit](https://github.com/notquiteog/Omote/blob/v0.9.0/docs/CAPABILITIES.md).

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
