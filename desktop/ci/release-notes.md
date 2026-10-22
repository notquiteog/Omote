## Omote 0.11.8

### Shop, saved browser logins and controlled eBay replies

Open **Shop** in the sidebar to save supplier quotes and listing drafts, review costs and estimated profit, and continue the work in chat. Website shortcuts cover Facebook Marketplace, eBay, Etsy, Depop, Alibaba, Made-in-China and DHgate. Browser assistance works without marketplace API credentials; sending messages, publishing and purchases use the existing approval policy.

Compatible chats now share the browser's saved account login while keeping separate tabs, navigation and element references. Closing one chat leaves the others open. A new chat starts on a blank page, and a busy or incompatible profile produces a clear message instead of silently opening an unsigned temporary profile. Expired website sessions can still require sign-in.

**Shop → Replies** adds the first unattended inbox connector through eBay's official Message API. Advanced setup requires an eBay user access token. You explicitly enable an exact question-and-answer rule for an item, with a daily limit and expiry. Account checks and a persistent outgoing ledger prevent blind retries after uncertain sends. Expired tokens pause replies and require reconnecting; automatic token refresh is not included yet.

Other marketplaces use assisted browser workflows. This release does not add unattended replies to Facebook Marketplace, Etsy or Depop. Supplier records and readiness checks help prepare work; they do not guarantee product eligibility, margins or successful transactions.

Studio's separate character, source-video and speech inputs, **Tasks → Results**, and interrupted-chat recovery from previous releases remain included.

### What to try

1. Open **Shop**, choose a website and select **Open with Omote**. Send the prepared chat request to begin.
2. Sign in through Omote's browser when asked, then try another chat using the same account.
3. Save a supplier quote or listing draft, select **Review**, and use **Continue with Omote** to work on it in chat.
4. If you have an eBay API token, open **Replies**, connect the account, and enable a narrowly defined answer rule. Pause it from the same screen.

Automated tests cover storage boundaries, reply limits, duplicate prevention, uncertain sends, token redaction, desktop/mobile Shop flows, and persistent browser login with separate tabs. Marketplace tests use simulated services and accounts; no real seller was contacted and no live listing was published. See [the Shop guide](https://github.com/notquiteog/Omote/blob/v0.11.8/docs/COMMERCE.md) for setup and current limitations.

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
