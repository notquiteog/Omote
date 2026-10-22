## Omote 0.11.6

### Find your saved work

Open **Tasks → Results** to search saved chat replies, scheduled reports and generated Studio files, including work saved before restarting Omote. Expand a reply, download linked project files, export a conversation or return to its original chat. Browsing and downloading work without a connected AI model.

Results load on demand, with pagination and no background polling while you read. Search and loading handle late responses, and the layout fits narrow screens.

### Review interrupted chats before continuing

Interrupted chats appear under **Needs you**. Choose **Review saved work**, then **Review saved conversation**, to read recent saved messages without starting an agent. **Continue in chat** opens the original conversation; work starts only when you send a message. Each chat keeps its own context.

### Download files from the original project

File downloads are limited to existing files explicitly linked in a saved reply and located inside that chat's project. The boundary is checked again when downloading. These downloads use the current file version, not an immutable historical copy. Conversation exports include the full saved transcript; inbox previews are bounded views of recent text.

The release also preserves daemon startup logs when a browser test fails, so intermittent platform startup problems can be diagnosed.

### What to try

1. Open **Tasks → Results**, search for a saved reply and download a linked file or conversation.
2. Restart Omote and check that saved results remain available.
3. Review an interrupted chat through **Needs you** before choosing to continue.

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
