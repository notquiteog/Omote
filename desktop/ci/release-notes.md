## Omote 0.11.7

### Separate characters, source video and speech in Studio

Imported ComfyUI workflows now expose native image, mask, video and audio loaders as individual file controls. Multiple character references stay separate, and files reach ComfyUI with their actual formats. Project references fill compatible image controls in order; models with too few reference slots show a clear message before rendering. Project-managed controls clearly identify where their files come from.

ComfyUI's source-video previews are no longer mistaken for generated results. Missing or mismatched input files fail before the workflow is queued.

This release also includes **Tasks → Results** and read-only interrupted-chat recovery from 0.11.6, so you can search saved replies, download linked project files, export conversations and review work after restarting.

### What to try

1. Import a working ComfyUI API workflow with separate character, source-video and audio inputs.
2. Prepare a Remix project with two reference images and a source clip, then add speech in Generate when the workflow exposes audio input.
3. Check each reference preview before generating. Only generated outputs should appear as new takes.
4. Open **Tasks → Results** to find saved work from earlier sessions.

Three independent local ComfyUI tests preserved two distinct images, all 24 frames of a test clip and an audible track. Backend and browser regression tests cover binding, uploads and saved-result recovery. These validate the media pipeline; they do not certify full-length identity replacement, lip sync or every model/provider. A compatible generation workflow and its model weights are still required.

Each installer includes the app and its own Python runtime. Existing data remains in `~/.omote`. If you run a separate daemon on port 8477, the desktop app uses it; update or restart that daemon separately to test the new server code.

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
