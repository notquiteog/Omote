## Omote 0.11.11

### Windows chat, voice and conversation permissions

Talk now uses the conversation it was started from, sharing its model, history, working folder, tools and permission mode. Required approvals and questions appear on screen in both Talk and Live, and answers stay synchronized between voice and the screen. Permission changes save immediately and survive reopening the conversation. Ending a call leaves its parent conversation open and does not cancel unrelated typed work.

Windows microphone capture and headset playback now start together through a shared audio context. Talk rejects brief background noise before interrupting replies, drops explicit non-speech annotations, and reads each new agent answer instead of replaying the first one. Invalid speech model settings produce an actionable error before microphone capture begins.

This release also fixes Windows hook execution and process cleanup, desktop imports and stale-lock checks. Shell tools explicitly describe Windows cmd.exe syntax. Chat retries transient upstream gateway failures before streaming starts, without replaying tool execution.

Regression coverage now includes Windows, Linux and macOS core tests, plus browser tests for microphone capture, rendered audio, call approvals, questions and saved permissions. Native installers are built and smoke-tested on each platform before publication.

Each installer includes the app and its own Python runtime. Existing data remains in `~/.omote`. Restart or update separately managed daemons as well as the desktop app.

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
