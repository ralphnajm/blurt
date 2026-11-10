# Blurt

Voice typing for GNOME (Rust, GTK 4 / libadwaita): a user service records while a key is held, transcribes with OpenAI, cleans the text up and types it through a GNOME Shell extension. See README.md.

## Layout

- `src/core/`: config, OpenAI client, cleanup, recorder, usage and history. No GTK or session bus, so it stays unit-testable.
- `src/daemon.rs`: the `dev.blurt.Daemon` D-Bus service (`blurt daemon`, run by `data/blurt.service`).
- `src/ui/`: the settings/usage window and the history window.
- `data/gnome-shell/blurt@dev.blurt/`: the top bar extension. It polls the hold key, calls the daemon, and exports `dev.blurt.Shell.Insert`, which types text through the input method. Keep its D-Bus calls in step with `src/daemon.rs`, and add each new GNOME Shell major version to `metadata.json`'s `shell-version`.

## Commands

- `make check`: fmt check, clippy `-D warnings` (pedantic), tests, desktop/AppStream validation, release notes. Run before finishing any change.
- `cargo run -- daemon` (stop `blurt.service` first) and `cargo run -- settings`.
- If the build dependencies live in a toolbox, run commands through `toolbox run -c <toolbox> …`.

## Changelog and releases

Describe every user-facing change in `CHANGELOG.md` under `## [Unreleased]` (`Added` / `Changed` / `Fixed` / `Removed`), written for users. Release with every change pushed to `main`: CI turns each new `Cargo.toml` version into a GitHub release, and a push without a version bump publishes nothing:

1. Bump `version` in `Cargo.toml` (semver: patch for fixes, minor for features, major for breaking changes) and run `cargo build` to update `Cargo.lock`.
2. Rename `## [Unreleased]` to `## [x.y.z] - YYYY-MM-DD`. It becomes the release notes.
3. Add `<release version="x.y.z" date="YYYY-MM-DD"/>` at the top of `<releases>` in `data/dev.blurt.Blurt.metainfo.xml`.
4. `make check` verifies steps 2 and 3.

A version with a pre-release suffix (`1.0.0-alpha.1`) is published as a GitHub pre-release, which the install script skips; give its AppStream `<release>` `type="development"`.

## Conventions

- User data stays compatible: `~/.config/blurt/config.toml`, the keyring entry (schema `dev.blurt.Blurt`, `service=openai`), `~/.local/state/blurt/usage.db` and `history.jsonl`.
- Target GTK 4.14 / libadwaita 1.5 (Ubuntu 24.04); don't enable newer feature flags.
- No runtime dependency on a language runtime the distribution can upgrade from under us: that is what broke the Python version.
