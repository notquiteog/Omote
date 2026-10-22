## Omote 0.11.1

### Reliable model switching

Chat finishes saving a reply before enabling model changes. Switching immediately after a reply now uses the selected model for the next message, and a completed conversation stays marked complete after restarting. Release checks also exercise real video encoding on Linux.

### Local setup, no terminal

**Studio → Tools** installs and configures ComfyUI or Playwright with no shell, pip, administrator account or Python install. ComfyUI gets a private runtime, detects the device, connects Studio and managed model folders, and restarts with Omote. Playwright installs and tests a version-matched browser. Start is asynchronous; poll status until ready or failed. Cancel keeps reusable downloads, and stop only stops Omote-owned ComfyUI while refusing while creations are queued or running.

### Video export

**Studio → Make final video** assembles shots into an H.264/AAC MP4 at 30 fps through local FFmpeg, with burned-in captions, voiceover replacing original audio, and mixed aspect ratios fitted without cropping. When FFmpeg is missing, **Set up video export** downloads a checksum-pinned native binary for Linux x64/ARM64, macOS Intel/Apple Silicon, or Windows x64, tests H.264 encoding and libass captions, and installs into Omote's data directory — no shell, system Python or administrator access needed. Existing FFmpeg on PATH is preferred.

### Guided Studio projects

**Studio → Create a project** offers six starting points — cinematic story, remix a video, stage in 3D, product campaign, visual effects, narrated short — with shared character and image references, per-shot direction, plans stored per configured user with `base_revision` conflict protection, and exports that survive daemon restarts. Generated takes and exports keep their provenance; a stale revision is rejected before work starts and an interrupted job is shown explicitly, never automatically resubmitted.

### Creative apps

**Studio → Creative apps** shows detected engines and configured creative MCP connectors. The `creative_apps` tool supports all 13 native ArtCraft engines (PhotoCraft, VectorCraft, FilmCraft, EffectCraft, LightCraft, PdfCraft, DesignCraft, FigCraft, WordCraft, SoundCraft, DeckCraft, CADCraft, GridCraft): `status`, `tools` with actual schemas and pagination, and `call` invoking a discovered tool with its original arguments. Keep the returned session ID across edits; up to four editor sessions can be open. Adobe and other apps can use an operator-installed MCP bridge with `"creative": true` in the server's `.mcp.json`.

### Reference media

Uploads now accept video and audio references as well as images, up to 32 MB each. Video references are validated before storage, and image/video/audio generator parameters carry `multiple` and `max_images` so a model receives one reference or several exactly as its schema allows.

### Local models

The device mode gained `auto` alongside `gpu` and `cpu`, and runtime operations gained `prepare` (warm a model without starting generation) and `cancel_setup`.

### Memory

Automatic chat context now searches personal memories plus the current project's memories, filtered before ranking so unrelated projects cannot crowd out relevant results. Explicit recall can still search other subjects, and a manually entered project label must match the project root to take part in that project's automatic recall.

### System

The system tool now reports the machine's architecture and any foreign architectures the package manager serves (Debian multiarch, e.g. `i386` for Steam and Wine), so a library installed for the native architecture alone is not mistaken for one that serves 32-bit programs. It also reports what is already installed — by package manager and by app-menu entry, including programs unpacked by hand — before installing anything twice. A new `setup_local` agent tool offers the same ComfyUI/Playwright setup from the agent. Telegram joins the install recipes (flatpak on Linux, winget on Windows, cask on macOS), with a note about where its flatpak keeps its data.

### Provider and reasoning controls

Reasoning limits load immediately after system boot, and controls show the actual reasoning levels for models with fixed minimums. Model and effort choices persist across restarts, survive a failed switch, and apply when reopening a stored conversation.

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
