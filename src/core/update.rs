//! Updates: [`check`] asks GitHub for the latest release, and [`run`] downloads
//! `scripts/update.sh` from the repository's main branch and runs it. The
//! script finds out how the running binary was installed, updates it the same
//! way, and starts the settings window again once this one has quit.

use std::process::{Command, Output, Stdio};

use anyhow::{Context, Result, bail};

pub const REPO: &str = "ralphnajm/blurt";
/// The script's exit status when the running version is already the latest.
const UP_TO_DATE: i32 = 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    /// Updated: the app must quit so the new version can start.
    Updated,
    UpToDate,
}

/// Runs curl and returns what it printed, or why it failed.
fn curl(url: &str) -> Result<Vec<u8>> {
    let output = Command::new("curl")
        .args(["-fsSL", "-H", "Accept: application/vnd.github+json", url])
        .stdin(Stdio::null())
        .output()
        .context("Could not run curl. Is it installed?")?;
    if !output.status.success() {
        bail!("Could not reach GitHub: {}", last_line(&output.stderr).unwrap_or("curl failed"));
    }
    Ok(output.stdout)
}

/// The latest release when it is newer than `current`, else `None`.
pub fn check(current: &str) -> Result<Option<String>> {
    let latest = release_version(&curl(&format!("https://api.github.com/repos/{REPO}/releases/latest"))?)
        .context("Could not find the latest release")?;
    Ok(is_newer(&latest, current).then_some(latest))
}

/// The version of the release GitHub's API describes in `json`.
fn release_version(json: &[u8]) -> Option<String> {
    let json: serde_json::Value = serde_json::from_slice(json).ok()?;
    let tag = json.get("tag_name")?.as_str()?;
    Some(tag.strip_prefix('v').unwrap_or(tag).to_owned())
}

/// Whether version `a` comes after `b`, comparing their numbers in turn. A
/// pre-release (`1.0.0-alpha.1`) comes before its release.
pub fn is_newer(a: &str, b: &str) -> bool {
    let parse = |v: &str| {
        let (core, pre) = v.split_once('-').map_or((v, None), |(core, pre)| (core, Some(pre)));
        let numbers: Vec<u64> = core.split('.').map(|n| n.parse().unwrap_or(0)).collect();
        // Pre-release identifiers compare as numbers when they are numbers.
        let pre: Option<Vec<(u64, String)>> =
            pre.map(|p| p.split('.').map(|id| (id.parse().unwrap_or(u64::MAX), id.to_owned())).collect());
        (numbers, pre)
    };
    let ((a_core, a_pre), (b_core, b_pre)) = (parse(a), parse(b));
    match a_core.cmp(&b_core) {
        std::cmp::Ordering::Equal => match (a_pre, b_pre) {
            (None, Some(_)) => true,
            (Some(a), Some(b)) => a > b,
            _ => false,
        },
        order => order.is_gt(),
    }
}

/// Where the update script is downloaded from.
pub fn script_url() -> String {
    format!("https://raw.githubusercontent.com/{REPO}/main/scripts/update.sh")
}

/// Downloads and runs the update script to install `target`, as [`check`]
/// found it. Blocks until it is done, which can take minutes when the update
/// is built from source.
pub fn run(current: &str, target: &str) -> Result<Outcome> {
    let exe = std::env::current_exe().context("Could not find the running binary")?;
    let script = String::from_utf8(curl(&script_url())?).context("The update script is not text")?;
    let output = Command::new("sh")
        .arg("-c")
        .arg(script)
        .arg("update.sh")
        .arg(current)
        .arg(exe)
        .arg(std::process::id().to_string())
        .arg(target)
        .stdin(Stdio::null())
        .output()
        .context("Could not run the update script")?;
    outcome(&output)
}

fn outcome(output: &Output) -> Result<Outcome> {
    match output.status.code() {
        Some(0) => Ok(Outcome::Updated),
        Some(UP_TO_DATE) => Ok(Outcome::UpToDate),
        _ => bail!("{}", last_line(&output.stderr).unwrap_or("the update script failed")),
    }
}

/// The last non-blank line of a command's output, which explains a failure.
fn last_line(output: &[u8]) -> Option<&str> {
    std::str::from_utf8(output).ok()?.lines().map(str::trim).rfind(|line| !line.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run_sh(script: &str) -> Output {
        Command::new("sh").args(["-c", script]).output().unwrap()
    }

    #[test]
    fn script_comes_from_the_main_branch() {
        assert_eq!(script_url(), "https://raw.githubusercontent.com/ralphnajm/blurt/main/scripts/update.sh");
    }

    #[test]
    fn reads_the_release_version() {
        assert_eq!(release_version(br#"{"tag_name": "v0.10.0", "name": "Blurt 0.10.0"}"#).as_deref(), Some("0.10.0"));
        assert_eq!(release_version(b"{}"), None);
    }

    #[test]
    fn compares_versions() {
        assert!(is_newer("0.10.0", "0.9.0"));
        assert!(is_newer("1.0.0", "0.99.1"));
        assert!(!is_newer("0.9.0", "0.9.0"));
        assert!(!is_newer("0.8.2", "0.9.0"));
        assert!(is_newer("1.0.0", "1.0.0-alpha.1"));
        assert!(!is_newer("1.0.0-alpha.1", "1.0.0"));
        assert!(is_newer("1.0.0-alpha.10", "1.0.0-alpha.9"));
    }

    #[test]
    fn exit_status_tells_the_outcome() {
        assert_eq!(outcome(&run_sh("exit 0")).unwrap(), Outcome::Updated);
        assert_eq!(outcome(&run_sh("exit 3")).unwrap(), Outcome::UpToDate);
        let err = outcome(&run_sh("echo 'Downloading…' >&2; echo 'Update cancelled' >&2; exit 1")).unwrap_err();
        assert_eq!(err.to_string(), "Update cancelled");
    }

    #[test]
    fn script_refuses_development_builds_and_older_targets() {
        let source = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/scripts/update.sh")).unwrap();
        let script =
            |args: &[&str]| Command::new("sh").arg("-c").arg(&source).arg("update.sh").args(args).output().unwrap();
        let dev = script(&["0.7.0", "/x/target/release/blurt", "1", "0.8.0"]);
        assert!(last_line(&dev.stderr).unwrap().contains("development build"));
        assert_eq!(script(&["0.8.0", "/usr/bin/blurt", "1", "0.7.0"]).status.code(), Some(UP_TO_DATE));
        assert_eq!(script(&["0.8.0", "/usr/bin/blurt", "1", "0.8.0"]).status.code(), Some(UP_TO_DATE));
    }
}
