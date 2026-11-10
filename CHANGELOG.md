# Changelog

All notable changes to Blurt are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Each version's section becomes the
notes of its GitHub release.

## [0.9.0] - 2026-11-10

### Changed

- Opening Blurt from the app grid starts it in the top bar instead of opening the settings window. The settings
  window opens only when something needs doing there: finishing setup, adding your API key, or logging out and back
  in once so the top bar menu loads. Open it any time with **Settings…** in the top bar menu.
- **Quit Blurt** in the top bar menu is how you stop Blurt: it stays off until you open it again or log in.
- The settings window says when GNOME needs you to log out and back in before Blurt shows in the top bar, instead of
  Blurt silently doing nothing.

### Removed

- The **Background service** switch in the settings window: open Blurt to start it, and quit it from the top bar menu.

## [0.8.0] - 2026-11-10

### Added

- **Check for Updates** in the settings window's menu: when a newer release is out, it offers to install it the way
  Blurt was installed, asking for your password, and reopens the settings window as the new version.

### Changed

- Installing or updating the package starts the background service right away for everyone who has Blurt set up,
  instead of only restarting it on upgrades: after a reinstall, dictation works without restarting it by hand.
- Install with one line: `curl -fsSL https://raw.githubusercontent.com/ralphnajm/blurt/main/scripts/install.sh | sh`.

## [0.7.0] - 2026-11-10

### Changed

- Blurt is now a single native program instead of a Python app, so distribution upgrades can no longer break it: the
  Fedora 45 move to Python 3.15 uninstalled the previous version. Your settings, API key, history and usage carry over.
- Releases are built by CI for every version pushed to `main`, as a `.rpm` for Fedora and a `.deb` for Ubuntu 24.04 and
  later, attached to a GitHub release. `scripts/install.sh` installs or updates to the latest one.

### Fixed

- The top bar menu loads on GNOME 51.

## [0.6.0] - 2026-10-02

### Added

- **Show history** lists everything you've dictated, newest first and searchable; click an entry to copy it.

## [0.5.0] - 2026-10-02

### Added

- A `.deb` package for Ubuntu 24.04 and later.
