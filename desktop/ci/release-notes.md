## Omote 0.10.0

### In-app update fixes

- Fixed the Tauri 2 desktop bridge: Install now reaches the native installer.
- Linux downloads match Debian/RPM/AppImage installations. Debian and RPM updates
  use the system authentication prompt and package manager.
- A detached, checksum-verifying worker waits for the old app to exit, installs,
  then reopens the installed path. AppImage updates replace the original file.
- Installer/download failures remain visible, including after restarting.
- macOS opens the disk image for the existing Finder replacement step.
- **Older affected installs need this installer once to bootstrap the fix.**
  Subsequent updates use Settings → Updates → Download → Install.

### Creative and personal workflows

- Stateful, on-demand access to all 13 installed ArtCraft engines and configured
  creative MCP bridges; native projects, delivery exports and export verification.
- Agent ComfyUI workflow authoring, atomic edits, live schema validation, versioned
  execution and API-to-native editor loading.
- Job searches by role/city, persistent application tracking, truthful resume
  variants, DOCX/PDF/HTML/TXT exports, DOCX reading and reviewed browser uploads.
- Simple job/travel starters, stronger flight/hotel comparisons, booking references
  and confirmation checks. Final applications and purchases remain reviewed.

### Verification and setup

The preceding full suite passed 2,756 tests; focused updater checks passed 74 tests.
Real Linux checks covered 13 native creative engine connections, image/vector/video/
motion exports, editable DOCX reopening, and ComfyUI image generation on an RTX 5060 Ti.
The release pipeline verifies installers and native update IPC on Linux, Windows
and both Mac architectures before publication. Live employer submissions, purchases,
Adobe bridges and native Android phone services are not claimed as verified.

Editors, weights, ComfyUI, provider accounts and optional browser dependencies are
separate installations. PDF generation and browser workflows need Chromium.

See [Workspace setup](https://github.com/notquiteog/Omote/blob/v0.10.0/docs/WORKSPACE.md),
[Creative workspace](https://github.com/notquiteog/Omote/blob/v0.10.0/docs/CREATIVE.md),
and [Personal assistant capabilities](https://github.com/notquiteog/Omote/blob/v0.10.0/docs/PERSONAL-ASSISTANT.md).

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
