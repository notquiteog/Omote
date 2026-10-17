## Omote 0.10.1

### Update reliability

- Canceled installs recover their verified download after restart and a release
  check, so Install can be retried without downloading the installer again.
- Reused downloads are rehashed against the selected release's published
  checksum. Missing, modified or mismatched downloads fall back to Download.
- Replacing an unverified download clears any older verification record.
- Linux release tests now exercise native update handoff, old-app exit,
  replacement and relaunch with an isolated executable payload.

### Chat reliability on every desktop

- Provider errors sent inside an HTTP-success stream are now visible failures.
- A model that returns no answer is retried once, including on the first
  message, then reports a clear error instead of silently ending the turn.
- Packaged-daemon checks on Linux, Windows and both Mac architectures now send
  a chat message, stream a reply, execute a file tool with UTF-8 content, and
  verify that upstream errors and repeated empty replies reach the client.

Real OpenRouter free-model checks use synthetic prompts and temporary files.
Free endpoints may be rate limited; errors are reported without claiming the
request succeeded. macOS updates still open the disk image for Finder replacement.
Older installs affected by the pre-0.10.0 native bridge bug need one manual update.

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
