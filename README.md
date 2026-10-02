<p align="center"><img src="packaging/icons/hicolor/128x128/apps/dev.blurt.Blurt.png" width="96" alt=""></p>

# Blurt

Talk anywhere you can type. Hold Ctrl, say it, let go, and Blurt types clean, filler-free text into whatever app has focus on your GNOME desktop.

Blurt records while you hold the key, sends the audio to OpenAI for transcription, removes the ums, false starts and repeated words with a quick `gpt-6-luna` pass, and hands the result to GNOME's input method, the same path the on-screen keyboard uses. Text lands in one piece in terminals, browsers and GTK apps, accents and emoji included. Apps without input-method support get the text typed as key presses instead.

It is built for Fedora Workstation and Ubuntu 24.04 or later with GNOME on Wayland and needs an [OpenAI API key](https://platform.openai.com/api-keys). A typical day of dictation costs a few cents.

## Install

Build a package from the latest release tag and install it. On Fedora:

```sh
make build-deps   # once: rpm-build and friends
make rpm install
```

On Ubuntu:

```sh
make build-deps   # once: pip and desktop-file-utils
make deb install  # same as sudo apt install ./dist/blurt_X.Y.Z-1_all.deb
```

apt pulls in the dependencies from the Ubuntu archive. The `.deb` bundles `sounddevice`, the one Python library Ubuntu doesn't package.

Then open Blurt from the app grid, click **Set up** in the banner, paste your API key and log out and back in once so GNOME Shell loads the top bar menu. Later releases install the same way; the service restarts on its own, the top bar menu picks up changes at your next login.

To switch Blurt off for your account run `blurt teardown`; `sudo dnf remove blurt` or `sudo apt remove blurt` removes it from the system.

## Use

Hold **Ctrl** for a second until you hear the start sound, speak, and release it. Super+D starts and stops dictation without holding anything and Super+Shift+D cancels. The speech bubble in the top bar turns red while recording, shows dots while transcribing and turns yellow when something went wrong; its menu explains what, and offers start, settings and quit.

The settings window picks the hold key and delay, the transcription model, the spoken language, words Blurt should spell correctly, how hard the cleanup pass thinks, and a monthly budget that either warns you or stops dictation. The Usage tab shows estimated spend per day and per model, computed from audio length and token counts at OpenAI's list prices; your OpenAI invoice remains the source of truth.

Holding Ctrl only counts when no other modifier or mouse button is down, but Blurt can't see letter keys, so holding Ctrl for more than the delay while pressing shortcuts will start a recording. Raise the delay or pick Alt or Shift if that gets in your way. Line breaks are turned into spaces so a dictated sentence can never run a command in a terminal.

Your audio and transcripts go to OpenAI. Settings live in `~/.config/blurt/config.toml`, the API key in the GNOME keyring, usage and a text history in `~/.local/state/blurt`, and recordings whose transcription failed in `~/.local/state/blurt/failed`.

## Develop

```sh
poetry install
systemctl --user stop blurt.service
poetry run blurt daemon      # in one terminal
poetry run blurt settings    # in another
make check                   # format, lint, types
```

`blurt transcribe FILE` runs a recording through transcription and cleanup without typing it. The top bar extension lives in `blurt/data/gnome-shell/`; GNOME Shell only reloads it at login.

## Release

Versions come from `pyproject.toml` and every release is a `vX.Y.Z` tag. Packages are only ever built from a tag through a clean `git archive`, so local changes never reach an installed build.

```sh
make release VERSION=0.5.0 NOTES="What changed"
git push --follow-tags
make rpm install   # or make deb install
```
