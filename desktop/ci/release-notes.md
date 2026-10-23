## Omote 0.11.10

### October 2026 security update

Update existing daemon and desktop installations to refresh the HTTP stack, certificate trust bundle, image/PDF parsers and browser tools. This release requires patched aiohttp, multidict, certifi, Pillow and pypdf versions and updates the desktop Tokio lockfile to 1.53.3.

Browser Setup now installs Playwright 1.64.1, whose bundled Node 24.22.1 includes the October runtime security fixes. Older private browser environments are refused after an app update. If Browser Setup reports an interrupted or changed installation, choose **Retry setup** to install the matching browser tools. Project Node tooling and extension CI use Node 26.12.1.

The broad Python regression run passed 2,974 checks with 86 skips before the matching-browser download; the missing-browser case and setup regressions then passed in a 51-check follow-up. Extension tests passed. The release workflow validates native installers on Linux, Windows and macOS before publication. Existing upstream GLib 0.18.5 safety and native dependency maintenance warnings remain; this release does not resolve those upstream issues.

Each installer includes the app and its own Python runtime. Existing data remains in `~/.omote`. Restart or update separately managed daemons as well as the desktop app. Existing Shop, saved browser login and controlled eBay reply features remain included; see [the Shop guide](https://github.com/notquiteog/Omote/blob/v0.11.10/docs/COMMERCE.md).

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
