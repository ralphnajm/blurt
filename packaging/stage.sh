#!/usr/bin/env bash
set -euo pipefail

dest="${1:?usage: packaging/stage.sh DESTDIR}"
root="$(cd "$(dirname "$0")/.." && pwd)"
python="${PYTHON:-/usr/bin/python3}"
libdir=/usr/lib/blurt
site="$dest$libdir/site"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

pip() {
    "$python" -m pip --disable-pip-version-check --no-cache-dir "$@"
}

"$python" "$root/packaging/lock_requirements.py" "$root/poetry.lock" > "$work/requirements.txt"
pip install --quiet --no-warn-script-location --no-deps --only-binary=:all: \
    --target "$site" --requirement "$work/requirements.txt"
rm -rf "$site/bin"
pip wheel --quiet --no-deps --wheel-dir "$work/wheel" "$root"
pip install --quiet --no-warn-script-location --no-deps --target "$site" "$work"/wheel/blurt-*.whl
rm -rf "$site/bin"
"$python" -m compileall -q -j 0 --invalidation-mode unchecked-hash -d "$libdir/site" "$site" >/dev/null

install -d "$dest/usr/bin"
cat > "$dest/usr/bin/blurt" <<EOF
#!$python -sP
import sys

sys.path.insert(0, "$libdir/site")

from blurt.cli import main

sys.exit(main())
EOF
chmod 755 "$dest/usr/bin/blurt"

install -Dm644 "$root/packaging/blurt.service" "$dest/usr/lib/systemd/user/blurt.service"
install -Dm644 "$root/packaging/dev.blurt.Blurt.desktop" \
    "$dest/usr/share/applications/dev.blurt.Blurt.desktop"
for icon in "$root"/packaging/icons/hicolor/*/apps/dev.blurt.Blurt.png; do
    size="$(basename "$(dirname "$(dirname "$icon")")")"
    install -Dm644 "$icon" "$dest/usr/share/icons/hicolor/$size/apps/dev.blurt.Blurt.png"
done
extension="$root/blurt/data/gnome-shell/blurt@dev.blurt"
install -d "$dest/usr/share/gnome-shell/extensions"
cp -r "$extension" "$dest/usr/share/gnome-shell/extensions/"
find "$dest/usr/share/gnome-shell/extensions" -type d -exec chmod 755 {} +
find "$dest/usr/share/gnome-shell/extensions" -type f -exec chmod 644 {} +
