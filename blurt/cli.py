import argparse
import json
import sys
import time

from faster_whisper import decode_audio
from gi.repository import Gio, GLib

from blurt import __version__, account, gui, openai_api
from blurt.cleanup import clean
from blurt.config import CONFIG_PATH, load_config
from blurt.credentials import get_api_key
from blurt.daemon import BUS_NAME, HISTORY_PATH, INTERFACE, OBJECT_PATH, Daemon
from blurt.recorder import STREAM_RATE, WHISPER_RATE
from blurt.transcriber import LocalTranscriber


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
    parser.add_argument("--version", action="version", version=f"blurt {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("settings", help="open the settings and usage window")
    setup = sub.add_parser("setup", help="set up shortcuts, service and top bar for this account")
    setup.add_argument("--shortcut", help="start/stop shortcut, e.g. <Super>d")
    setup.add_argument("--cancel-shortcut", help="cancel shortcut, e.g. <Super><Shift>d")
    sub.add_parser("teardown", help="switch Blurt off for this account")
    for name in ("toggle", "start", "stop", "cancel", "status"):
        sub.add_parser(name, help=f"{name} dictation" if name != "status" else "show daemon state")
    sub.add_parser("authorize", help="grant keyboard access through the portal dialog")
    sub.add_parser("daemon", help="run the background service")
    transcribe = sub.add_parser("transcribe", help="transcribe and clean an audio file")
    transcribe.add_argument("file")
    transcribe.add_argument("--engine", choices=["local", "openai", "stream"], default="local")
    transcribe.add_argument("--cleanup", choices=["openai", "codex", "basic", "off"])
    transcribe.add_argument("--speed", type=float, default=1.0, help="stream faster than real time")
    history = sub.add_parser("history", help="show recent dictations")
    history.add_argument("-n", type=int, default=10)
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
        case "authorize":
            print("Allow remote interaction in the dialog that opens…")
            call("Authorize", timeout_ms=310_000)
            print("Keyboard access granted and remembered.")
        case "transcribe":
            cmd_transcribe(args)
        case "history":
            cmd_history(args)
        case command:
            call(command.capitalize())
