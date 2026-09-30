import argparse
import ast
import json
import os
import subprocess
import sys
import shutil
import time
from pathlib import Path

from faster_whisper import decode_audio
from PIL import Image
from gi.repository import Gio, GLib

from blurt import gui, openai_api
from blurt.cleanup import clean
from blurt.config import CONFIG_PATH, load_config
from blurt.daemon import APP_ID, BUS_NAME, HISTORY_PATH, INTERFACE, OBJECT_PATH, Daemon
from blurt.credentials import get_api_key
from blurt.data import APP_ICON, EXTENSION_DIR, EXTENSION_UUID
from blurt.recorder import STREAM_RATE, WHISPER_RATE
from blurt.transcriber import LocalTranscriber

SERVICE = "blurt.service"
UNIT_PATH = Path.home() / ".config/systemd/user" / SERVICE
DESKTOP_PATH = Path.home() / ".local/share/applications" / f"{APP_ID}.desktop"
KEYBINDINGS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEYBINDING_SCHEMA = f"{KEYBINDINGS_SCHEMA}.custom-keybinding"
KEYBINDING_ROOT = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
ICON_ROOT = Path.home() / ".local/share/icons/hicolor"
ICON_SIZES = (512, 256, 128, 64, 48, 32, 16)
EXTENSIONS_DIR = Path.home() / ".local/share/gnome-shell/extensions"
SHORTCUTS = {"toggle": "Blurt: start/stop", "cancel": "Blurt: cancel"}


def executable() -> Path:
    return Path(sys.executable).parent / "blurt"


def call(method: str, timeout_ms: int = 5000) -> GLib.Variant | None:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        return bus.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            method,
            None,
            None,
            Gio.DBusCallFlags.NONE,
            timeout_ms,
            None,
        )
    except GLib.Error as exc:
        if "ServiceUnknown" in exc.message or "not provided" in exc.message:
            sys.exit(
                f"Blurt daemon is not running. Start it with: systemctl --user start {SERVICE}"
            )
        sys.exit(exc.message)


def gdbus_command(method: str) -> str:
    return (
        f"gdbus call --session --dest {BUS_NAME} --object-path {OBJECT_PATH} "
        f"--method {INTERFACE}.{method}"
    )


def gsettings(*args: str) -> str:
    return subprocess.run(
        ["gsettings", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def keybinding_paths() -> list[str]:
    raw = gsettings("get", KEYBINDINGS_SCHEMA, "custom-keybindings")
    return list(ast.literal_eval(raw.removeprefix("@as "))) if raw else []


def set_shortcut(name: str, method: str, binding: str) -> None:
    path = f"{KEYBINDING_ROOT}/blurt-{name}/"
    schema = f"{KEYBINDING_SCHEMA}:{path}"
    gsettings("set", schema, "name", SHORTCUTS[name])
    gsettings("set", schema, "command", gdbus_command(method))
    gsettings("set", schema, "binding", binding)
    paths = keybinding_paths()
    if path not in paths:
        gsettings("set", KEYBINDINGS_SCHEMA, "custom-keybindings", str([*paths, path]))


def remove_shortcuts() -> None:
    paths = keybinding_paths()
    ours = [p for p in paths if p.rstrip("/").split("/")[-1].startswith("blurt-")]
    for path in ours:
        subprocess.run(
            ["gsettings", "reset-recursively", f"{KEYBINDING_SCHEMA}:{path}"], check=False
        )
    gsettings(
        "set", KEYBINDINGS_SCHEMA, "custom-keybindings", str([p for p in paths if p not in ours])
    )


def install_icons() -> None:
    source = Image.open(APP_ICON)
    for size in ICON_SIZES:
        target = ICON_ROOT / f"{size}x{size}/apps/{APP_ID}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        source.resize((size, size), Image.Resampling.LANCZOS).save(target)
    subprocess.run(
        ["gtk-update-icon-cache", "-f", "-t", str(ICON_ROOT)], check=False, capture_output=True
    )


def remove_icons() -> None:
    for size in ICON_SIZES:
        (ICON_ROOT / f"{size}x{size}/apps/{APP_ID}.png").unlink(missing_ok=True)


def install_extension() -> bool:
    target = EXTENSIONS_DIR / EXTENSION_UUID
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)
    shutil.copytree(EXTENSION_DIR, target)
    result = subprocess.run(
        ["gnome-extensions", "enable", EXTENSION_UUID], capture_output=True, text=True
    )
    return result.returncode == 0


def remove_extension() -> None:
    subprocess.run(
        ["gnome-extensions", "disable", EXTENSION_UUID], check=False, capture_output=True
    )
    target = EXTENSIONS_DIR / EXTENSION_UUID
    if target.exists():
        shutil.rmtree(target)


def cmd_install(args: argparse.Namespace) -> None:
    load_config()
    exe = executable()
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(
        f"""[Unit]
Description=Blurt voice typing
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart={exe} daemon
Environment=PATH={os.environ.get("PATH", "/usr/bin")}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
"""
    )
    DESKTOP_PATH.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_PATH.write_text(
        f"""[Desktop Entry]
Type=Application
Name=Blurt
Comment=Talk anywhere you can type
Icon={APP_ID}
Exec={exe} settings
Categories=Utility;Accessibility;
Keywords=voice;dictation;speech;transcribe;dictate;typing;
"""
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", SERVICE], check=True)
    subprocess.run(["systemctl", "--user", "restart", SERVICE], check=True)
    set_shortcut("toggle", "Toggle", args.shortcut)
    set_shortcut("cancel", "Cancel", args.cancel_shortcut)
    install_icons()
    enabled = install_extension()
    print(f"Installed. Config: {CONFIG_PATH}")
    print(f"Shortcuts: {args.shortcut} start/stop, {args.cancel_shortcut} cancel")
    if enabled:
        print("Top bar extension installed. Log out and back in once so GNOME Shell loads it.")
    else:
        print(f"Top bar extension copied; enable it with: gnome-extensions enable {EXTENSION_UUID}")
    print(
        "Next: open Blurt from the app grid (or `blurt settings`), add your OpenAI key and grant keyboard access."
    )


def cmd_uninstall(_args: argparse.Namespace) -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE], check=False)
    UNIT_PATH.unlink(missing_ok=True)
    DESKTOP_PATH.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    remove_shortcuts()
    remove_icons()
    remove_extension()
    print("Uninstalled. Config, API key and usage history were kept.")


def cmd_transcribe(args: argparse.Namespace) -> None:
    config = load_config()
    if args.cleanup:
        config.cleanup = args.cleanup
    key = get_api_key()
    if args.engine != "local" and not key:
        sys.exit("No OpenAI API key saved. Add one with `blurt settings`.")
    rate = STREAM_RATE if args.engine == "stream" else WHISPER_RATE
    audio = decode_audio(args.file, sampling_rate=rate)
    seconds = audio.size / rate
    started = time.monotonic()
    if args.engine == "local":
        raw = LocalTranscriber(config).transcribe(audio, rate)
    elif args.engine == "openai":
        raw = openai_api.transcribe(key or "", config, audio, rate)
    else:
        session = openai_api.StreamSession(
            key or "", config, lambda text: print(f"  … {text}", file=sys.stderr)
        )
        session.start()
        step = rate // 10
        for i in range(0, audio.size, step):
            session.feed(audio[i : i + step])
            time.sleep(0.1 / args.speed)
        raw = session.finish()
    print(
        f"transcribe {time.monotonic() - started:.2f}s ({seconds:.1f}s audio, {args.engine})",
        file=sys.stderr,
    )
    started = time.monotonic()
    cleaned = clean(raw, config, key)
    print(f"cleanup    {time.monotonic() - started:.2f}s ({config.cleanup})", file=sys.stderr)
    if cleaned.warning:
        print(cleaned.warning, file=sys.stderr)
    print(f"raw:   {raw}\nclean: {cleaned.text}")


def cmd_history(args: argparse.Namespace) -> None:
    if not HISTORY_PATH.exists():
        return
    for line in HISTORY_PATH.read_text().splitlines()[-args.n :]:
        entry = json.loads(line)
        print(f"[{entry['at']}] {entry['text'].strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="blurt", description="Talk anywhere you can type.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("daemon", help="run the background service")
    for name in ("toggle", "start", "stop", "cancel", "status"):
        sub.add_parser(name, help=f"{name} dictation" if name != "status" else "show daemon state")
    sub.add_parser("authorize", help="grant keyboard access through the portal dialog")
    install = sub.add_parser("install", help="install the service and GNOME shortcuts")
    install.add_argument("--shortcut", default="<Super>d")
    install.add_argument("--cancel-shortcut", default="<Super><Shift>d")
    sub.add_parser("uninstall", help="remove the service and shortcuts")
    transcribe = sub.add_parser("transcribe", help="transcribe and clean an audio file")
    transcribe.add_argument("file")
    transcribe.add_argument("--engine", choices=["local", "openai", "stream"], default="local")
    transcribe.add_argument("--cleanup", choices=["openai", "codex", "basic", "off"])
    transcribe.add_argument("--speed", type=float, default=1.0, help="stream faster than real time")
    sub.add_parser("settings", help="open the settings and usage window")
    history = sub.add_parser("history", help="show recent dictations")
    history.add_argument("-n", type=int, default=10)
    args = parser.parse_args()

    match args.command:
        case "daemon":
            Daemon(load_config()).run()
        case "status":
            reply = call("Status")
            print(reply.unpack()[0] if reply else "unknown")
        case "authorize":
            print("Allow remote interaction in the dialog that opens…")
            call("Authorize", timeout_ms=310_000)
            print("Keyboard access granted and remembered.")
        case "install":
            cmd_install(args)
        case "uninstall":
            cmd_uninstall(args)
        case "transcribe":
            cmd_transcribe(args)
        case "history":
            cmd_history(args)
        case "settings":
            sys.exit(gui.run())
        case command:
            call(command.capitalize())
