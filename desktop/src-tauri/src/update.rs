//! A detached copy of the sidecar outlives the old app and its install folder.

use std::fs::{self, File};
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub fn prepare(installer: &Path, launcher: &Path) -> Result<String, String> {
    let current = std::env::current_exe().map_err(|e| e.to_string())?;
    let parent = current.parent().ok_or("Could not find the installed app directory")?;
    let sidecar = parent.join(if cfg!(windows) { "omote-daemon.exe" } else { "omote-daemon" });
    if !sidecar.is_file() {
        return Err("This build has no bundled installer worker. Open the downloaded installer manually.".into());
    }
    let staging = installer.parent().ok_or("No update directory")?;
    let nonce = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|e| e.to_string())?.as_nanos();
    let stem = format!("install-{}-{nonce}", std::process::id());
    let worker = staging.join(format!("{stem}{}", if cfg!(windows) { ".exe" } else { "" }));
    fs::copy(&sidecar, &worker).map_err(|e| format!("Could not prepare the installer worker: {e}"))?;
    let request = staging.join(format!("{stem}.json"));
    fs::write(&request, serde_json::to_vec(&serde_json::json!({
        "installer": installer, "launcher": launcher, "parent": std::process::id()
    })).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    let log = File::create(staging.join("install.log")).map_err(|e| e.to_string())?;
    let mut command = Command::new(&worker);
    command.arg("--install-update").arg(&request)
        .env_remove("OMOTE_EXIT_WITH_STDIN").stdin(Stdio::null())
        .stderr(log.try_clone().map_err(|e| e.to_string())?).stdout(log);
    crate::daemon::outside_appimage(&mut command);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let mut child = command.spawn().map_err(|e| format!("Could not start the installer worker: {e}"))?;
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        if request.with_extension("ready").is_file() {
            return Ok("The app will close, the system installer may ask for permission, then Omote will reopen. Installation details are saved in the update directory.".into());
        }
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            let detail = fs::read_to_string(staging.join("install.log")).unwrap_or_default();
            return Err(format!("The installer worker stopped ({status}). {}", detail.chars().take(1500).collect::<String>()));
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
    Err("The installer worker did not become ready. Your current app is still running; try again or open the downloaded installer manually.".into())
}
