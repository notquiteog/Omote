## Omote 0.10.2

- Choose a connected provider and model from the dropdown beside the chat composer.
- Adjust reasoning with an accessible slider: Auto, Off, Low, Medium, High,
  Extra high and Max. Adapters translate the level to what each model supports.
- Set model and effort before the first message; switch an idle conversation's
  model while preserving its history and reasoning setting.
- Remember model/effort choices, show the active selection after reload, and
  preserve the draft and current model when a switch fails.
- Keep controls usable on narrow screens. Model catalogs load in the background.
- Require real first-message, model-control and update UI tests on Windows,
  macOS WebKit and Linux before publishing future releases.

Includes 0.10.1's update recovery and visible chat failures. Real free-model
checks covered streaming, file tools, lazy discovery, system telemetry and the
new composer controls. Local in-app installation from 0.10.0 to 0.10.1 was verified.

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
