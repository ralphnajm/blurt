#!/usr/bin/env bash
set -euo pipefail

dest="${1:?usage: packaging/install-data.sh DESTDIR}"
root="$(cd "$(dirname "$0")/.." && pwd)"

install -Dm644 "$root/packaging/blurt.service" "$dest/usr/lib/systemd/user/blurt.service"
install -Dm644 "$root/packaging/dev.blurt.Blurt.desktop" \
    "$dest/usr/share/applications/dev.blurt.Blurt.desktop"
for icon in "$root"/packaging/icons/hicolor/*/apps/dev.blurt.Blurt.png; do
    size="$(basename "$(dirname "$(dirname "$icon")")")"
    install -Dm644 "$icon" "$dest/usr/share/icons/hicolor/$size/apps/dev.blurt.Blurt.png"
done
extension="$dest/usr/share/gnome-shell/extensions/blurt@dev.blurt"
install -d "$extension/icons"
for file in extension.js metadata.json stylesheet.css; do
    install -m644 "$root/blurt/data/gnome-shell/blurt@dev.blurt/$file" "$extension/$file"
done
install -m644 "$root"/blurt/data/gnome-shell/blurt@dev.blurt/icons/*.svg "$extension/icons/"
