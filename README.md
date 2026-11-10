<p align="center"><img src="data/icons/hicolor/128x128/apps/dev.blurt.Blurt.png" width="96" alt=""></p>

# Blurt

Talk anywhere you can type. Hold Ctrl, say it, let go, and Blurt types clean, filler-free text into whatever app has focus on your GNOME desktop.

Blurt records while you hold the key, sends the audio to OpenAI for transcription, removes the ums, false starts and repeated words with a quick `gpt-6-luna` pass, and hands the result to GNOME's input method, the same path the on-screen keyboard uses. Text lands in one piece in terminals, browsers and GTK apps, accents and emoji included. Apps without input-method support get the text typed as key presses instead.

It is built for Fedora Workstation and Ubuntu 24.04 or later with GNOME on Wayland and needs an [OpenAI API key](https://platform.openai.com/api-keys). A typical day of dictation costs a few cents.

## Install

Every release has a `.rpm` for Fedora and a `.deb` for Ubuntu attached. This downloads the right one for your system and installs it with dnf or apt:

```sh
curl -fsSL https://raw.githubusercontent.com/ralphnajm/blurt/main/scripts/install.sh | sh
```

Then open Blurt from the app grid, click **Set up** in the banner, paste your API key and log out and back in once so GNOME Shell loads the top bar menu. From then on, opening Blurt puts it in the top bar without opening a window, and it starts by itself when you log in; **Settings…** in its menu opens the settings and **Quit Blurt** stops it until you open it again. To update, use **Check for Updates** in the settings window's menu, or run the line above again. The service restarts on its own; the top bar menu picks up changes at your next login.

To switch Blurt off for your account run `blurt teardown`; `sudo dnf remove blurt` or `sudo apt remove blurt` removes it from the system.

## Use

Hold **Ctrl** for a second until you hear the start sound, speak, and release it. Super+D starts and stops dictation without holding anything and Super+Shift+D cancels. The speech bubble in the top bar turns red while recording, shows dots while transcribing and turns yellow when something went wrong; its menu explains what, and offers start, history, settings and quit. **Show history** lists everything you've dictated, newest first and searchable; click an entry to copy it.

The settings window picks the hold key and delay, the transcription model, the spoken language, words Blurt should spell correctly, how hard the cleanup pass thinks, and a monthly budget that either warns you or stops dictation. The Usage tab shows estimated spend per day and per model, computed from audio length and token counts at OpenAI's list prices; your OpenAI invoice remains the source of truth.

Holding Ctrl only counts when no other modifier or mouse button is down, but Blurt can't see letter keys, so holding Ctrl for more than the delay while pressing shortcuts will start a recording. Raise the delay or pick Alt or Shift if that gets in your way. Line breaks are turned into spaces so a dictated sentence can never run a command in a terminal.

Your audio and transcripts go to OpenAI. Settings live in `~/.config/blurt/config.toml`, the API key in the GNOME keyring, usage and a text history in `~/.local/state/blurt`, and recordings whose transcription failed in `~/.local/state/blurt/failed`.

## Develop

Blurt is a Rust program (GTK 4 and libadwaita) plus a GNOME Shell extension for the top bar menu and typing, in `data/gnome-shell/`. CI builds, checks and packages every push, so you don't need a local toolchain to release. To build locally, install the dependencies first:

```sh
sudo dnf install gtk4-devel libadwaita-devel libsecret-devel alsa-lib-devel          # Fedora
sudo apt install libgtk-4-dev libadwaita-1-dev libsecret-1-dev libasound2-dev        # Ubuntu
```

```sh
systemctl --user stop blurt.service
cargo run -- daemon        # in one terminal
cargo run -- settings      # in another
make check                 # format, lint, tests, data files, release notes
```

`blurt transcribe FILE` runs a recording through transcription and cleanup without typing it. GNOME Shell only reloads the extension at login. `make install PREFIX=~/.local` installs a build for your account without a package.

## Release

Every push to `main` is checked and packaged by CI. When the version in `Cargo.toml` has no GitHub release yet, CI creates one with the packages attached and the version's `CHANGELOG.md` section as its notes. See `CLAUDE.md` for the steps.
