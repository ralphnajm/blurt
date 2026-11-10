# Changelog

All notable changes to Blurt are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Each version's section becomes the
notes of its GitHub release.

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
