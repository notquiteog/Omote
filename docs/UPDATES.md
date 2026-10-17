# Updates

Omote checks GitHub for a newer release, downloads it when asked, and hands
installation to the desktop app and the operating system.

## What happens, in order

1. **Check.** One request to the GitHub releases API, at most once an hour,
   and never while a turn is running. The result is a dot on the Updates tab.
2. **Download.** You press the button. The installer is fetched to
   `data/updates/` and checked against the SHA256 published in the same
   release.
3. **Install.** A detached worker verifies the file again, waits for the old
   app to exit, runs the system installer, then reopens Omote. AppImage installs
   replace the original AppImage. On macOS, the disk image opens for the Finder
   replacement step.

Nothing else happens. No step runs on its own, and the two that change
anything need a person.

## Why not Tauri's own updater

`tauri-plugin-updater` wants a minisign key pair and refuses to install
anything it cannot verify against one. This project signs macOS builds ad-hoc
until there is an Apple Developer ID, so the official updater would refuse
every build it published. So this uses the `SHA256SUMS.txt` the release
workflow already writes.

**That is an integrity check, not a signature**, and the difference is worth
being exact about:

| it catches | it does not catch |
|---|---|
| a truncated or corrupted download | anybody who can publish a release |
| a proxy that substituted an error page | a release account that was compromised |
| a mirror serving last month's build | a wrong-but-consistent checksum |

The middle column is what a signature is for. When there is a signing key,
this is the place to add it — `Updates.download` already refuses a mismatch
and there is one place to change.

## Why the pieces are separate

* **Check does not download.** A version check is 4KB of JSON.
* **Download does not install.** The download is verified and written to a
  file. That file is the thing the OS acts on, and a person can look at it
  first.
* **The daemon does not run it.** Replacing a running binary is the OS's
  problem, not this process's: on Windows a running `.exe` cannot be
  overwritten at all, and on macOS an `.app` bundle cannot be moved while it
  is open. The desktop app's `apply_update` command starts a worker outside
  the installation directory. It quits only after that worker is ready;
  the worker waits for exit before replacing anything.

## What you get offered

The right artifact for the machine, or nothing. Matching is by extension and
architecture, with a preference order that encodes a real difference:

| platform | preferred | because |
|---|---|---|
| Linux package install | matching `.deb` or `.rpm` | preserves the package manager's installation |
| Linux AppImage | `.AppImage` | replaces the original AppImage file |
| Windows | `-setup.exe`, then `.msi` | NSIS does not want an administrator to uninstall |
| macOS | `.dmg` | there is nothing else |

A model this project does not recognise gets no installer rather than the
nearest one, and the reason is on the message: downloading 120MB of the wrong
architecture and then failing is worse than being told the release has nothing
for you.

## If installation fails

An installer failure is saved locally and shown after restart. The downloaded
file remains available. Press **Check now** to revalidate it against the selected
release's published checksum; a valid download offers **Install** again without
another asset download. A modified file, missing verification record, different
version or different published checksum requires a new download.

Debian and RPM installations use the OS authentication prompt. Canceling that
prompt leaves the current app installed. Do not enter an administrator password
in chat. The installer log and `install-result.json` are in the update staging
directory.

Versions before 0.10.0 affected by the native bridge bug need one manual update
from the release page. After that bootstrap, Settings supports the separate
Download and Install steps.

## Pre-releases

A release tagged `v0.2.0-rc.1` is **not offered** to an install on a stable
release, by default. Silently running code the author has not promised
anybody is not a thing an updater should do to somebody.

An install that is *already* on a prerelease can move forward within one —
`rc.1` to `rc.2` — because it got there by asking and holding it there is
not a safety property.

## Privacy

`OMOTE_LOCAL_ONLY=true` stops the check entirely. A version check is a
request to a third party, and somebody who asked for no remote traffic did not
ask for that either.

`OMOTE_UPDATE_CHECK=false` turns it off without that.

Tor is not used, and the reason is in `omote/update.py`: the update host
is a CDN-hosted release asset, and routing a version check through a proxy
buys nothing.

## Settings

| setting | default | does |
|---|---|---|
| `OMOTE_UPDATE_CHECK` | `true` | may ask GitHub at all |
| `OMOTE_UPDATE_CHECK_ON_START` | `false` | asks at start-up rather than waiting to be asked |
| `OMOTE_UPDATE_DIR` | `data/updates` | where a downloaded installer waits |

The start-up check is off by default on purpose. A process that makes a
network call nobody asked for, before anybody has seen the window, is the
behaviour people notice first and forgive last.

The staging directory is under the data directory and never inside a working
root, for the same reason checkpoints are: a 90MB disk image inside a project
gets read, grepped and eventually committed.

## Running from source

`pip install` and the desktop app are different things. The app is a bundle
that replaced itself; a source install is a `git pull`. The message says which
you have and what to do about it:

```
pip install --upgrade git+https://github.com/notquiteog/Omote@v0.2.0
```

## The routes

```
GET  /api/update/status           what is known, no network
GET  /api/update/check            a check somebody pressed a button for
POST /api/update/check            { force, prerelease }
POST /api/update/apply            fetch and verify; installs nothing
POST /api/update/check-on-start   the start-up check, behind a flag
POST /api/update/dismiss          clear the notice
```

`status` is what the interface polls, so it has to be free: no network, no
disk, and small enough to ask about every few seconds.

`check` refuses to run while a session is busy, and says so
(`{"deferred": ...}`) rather than waiting. A message that appears over work
you are doing is a message that gets dismissed without being read.
