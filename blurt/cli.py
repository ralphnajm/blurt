import argparse
import sys
import time
from pathlib import Path

from gi.repository import Gio, GLib

from blurt import __version__, account, gui, history, openai_api
from blurt.cleanup import clean
from blurt.config import CONFIG_PATH, load_config
from blurt.credentials import get_api_key
from blurt.daemon import BUS_NAME, INTERFACE, OBJECT_PATH, Daemon


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
                "Blurt is not running. Run `blurt setup` or start it from the settings window."
            )
        sys.exit(exc.message)


def cmd_setup(args: argparse.Namespace) -> None:
    load_config()
    status = account.set_up({"toggle": args.shortcut, "cancel": args.cancel_shortcut})
    print(f"Config: {CONFIG_PATH}")
    print(
        f"Shortcuts: {status.shortcuts['toggle']} start/stop, {status.shortcuts['cancel']} cancel"
    )
    print(f"Service: {'running' if status.service_active else 'not running'}")
    if not status.extension_enabled:
        print(
            f"Top bar extension could not be enabled: gnome-extensions enable {account.EXTENSION_UUID}"
        )
    elif not status.extension_loaded:
        print("Top bar extension enabled. Log out and back in once so GNOME Shell loads it.")
    print("Next: open Blurt from the app grid, add your OpenAI key and grant keyboard access.")


def cmd_teardown(_args: argparse.Namespace) -> None:
    account.tear_down()
    print("Blurt is switched off for this account. Config, API key and usage history were kept.")


def cmd_transcribe(args: argparse.Namespace) -> None:
    config = load_config()
    if args.cleanup:
        config.cleanup = args.cleanup
    key = get_api_key()
    if not key:
        sys.exit("No OpenAI API key saved. Add one with `blurt settings`.")
    started = time.monotonic()
    file = Path(args.file)
    raw = openai_api.transcribe(key, config, file.read_bytes(), file.name)
    print(
        f"transcribe {time.monotonic() - started:.2f}s ({config.transcribe_model})",
        file=sys.stderr,
    )
    started = time.monotonic()
    cleaned = clean(raw, config, key)
    print(f"cleanup    {time.monotonic() - started:.2f}s ({config.cleanup})", file=sys.stderr)
    if cleaned.warning:
        print(cleaned.warning, file=sys.stderr)
    print(f"raw:   {raw}\nclean: {cleaned.text}")


def cmd_history(args: argparse.Namespace) -> None:
    if args.window:
        sys.exit(gui.run("history"))
    entries, _offset, _reset = history.read()
    for entry in entries[-args.n :]:
        print(f"[{entry.at.isoformat()}] {entry.text}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="blurt", description="Talk anywhere you can type.")
    parser.add_argument("--version", action="version", version=f"blurt {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("settings", help="open the settings and usage window")
    setup = sub.add_parser("setup", help="set up shortcuts, service and top bar for this account")
    setup.add_argument("--shortcut", help="start/stop shortcut, e.g. <Super>d")
    setup.add_argument("--cancel-shortcut", help="cancel shortcut, e.g. <Super><Shift>d")
    sub.add_parser("teardown", help="switch Blurt off for this account")
    for name in ("toggle", "start", "stop", "cancel", "status"):
        sub.add_parser(name, help=f"{name} dictation" if name != "status" else "show daemon state")
    sub.add_parser("daemon", help="run the background service")
    transcribe = sub.add_parser("transcribe", help="transcribe and clean an audio file with OpenAI")
    transcribe.add_argument("file")
    transcribe.add_argument("--cleanup", choices=["openai", "basic", "off"])
    history_cmd = sub.add_parser("history", help="show recent dictations")
    history_cmd.add_argument("-n", type=int, default=10)
    history_cmd.add_argument("--window", action="store_true", help="open the history window")
    args = parser.parse_args()

    match args.command:
        case "settings":
            sys.exit(gui.run())
        case "setup":
            cmd_setup(args)
        case "teardown":
            cmd_teardown(args)
        case "daemon":
            print(f"Blurt {__version__}", flush=True)
            Daemon(load_config()).run()
        case "status":
            reply = call("Status")
            print(reply.unpack()[0] if reply else "unknown")
        case "transcribe":
            cmd_transcribe(args)
        case "history":
            cmd_history(args)
        case command:
            call(command.capitalize())
