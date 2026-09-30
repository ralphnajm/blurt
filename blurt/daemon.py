import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from gi.repository import Gio, GLib

from blurt import openai_api, usage
from blurt.cleanup import clean
from blurt.config import STATE_DIR, Config, load_config, update_in_place
from blurt.credentials import get_api_key
from blurt.feedback import Feedback
from blurt.injector import Injector
from blurt.openai_api import StreamSession
from blurt.pricing import audio_cost
from blurt.recorder import Recorder, seconds

APP_ID = "dev.blurt.Blurt"
BUS_NAME = "dev.blurt.Daemon"
OBJECT_PATH = "/dev/blurt/Daemon"
INTERFACE = "dev.blurt.Daemon"
HISTORY_PATH = STATE_DIR / "history.jsonl"
FAILED_DIR = STATE_DIR / "failed"
KEEP_FAILED = 20
INTROSPECTION = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Toggle"/>
    <method name="Start"/>
    <method name="Stop"/>
    <method name="Cancel"/>
    <method name="Authorize"/>
    <method name="Reload"/>
    <method name="Quit"/>
    <method name="Status"><arg type="s" direction="out"/></method>
    <signal name="StateChanged"><arg type="s" name="state"/></signal>
  </interface>
</node>
"""
MIN_SECONDS = 0.3
PREVIEW_INTERVAL = 1.0

type State = Literal["idle", "recording", "processing"]
type Route = Literal["stream", "transcribe"]


@dataclass
class Plan:
    route: Route
    key: str
    notes: list[str] = field(default_factory=list)
    session: StreamSession | None = None


class Daemon:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._state: State = "idle"
        self._recorder = Recorder()
        self._feedback = Feedback(config)
        self._loop = GLib.MainLoop()
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._injector = Injector(config, self._bus)
        self._cancelled = threading.Event()
        self._timeout_id = 0
        self._plan: Plan | None = None
        self._last_preview = 0.0

    def run(self) -> None:
        self._register_app()
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        self._bus.register_object(OBJECT_PATH, node.interfaces[0], self._on_call, None, None)
        Gio.bus_own_name_on_connection(
            self._bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, self._on_name_lost
        )
        self._loop.run()

    def _register_app(self) -> None:
        try:
            self._bus.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.host.portal.Registry",
                "Register",
                GLib.Variant("(sa{sv})", (APP_ID, {})),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
        except GLib.Error as exc:
            print(f"Portal app registration failed: {exc.message}", flush=True)

    def _on_name_lost(self, *_args: object) -> None:
        print(f"Could not own {BUS_NAME}; is another daemon running?", flush=True)
        self._loop.quit()

    def _on_call(
        self,
        _conn: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _iface: str,
        method: str,
        _params: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        match method:
            case "Toggle":
                self.toggle()
            case "Start":
                self.start()
            case "Stop":
                self.stop()
            case "Cancel":
                self.cancel()
            case "Reload":
                self.reload()
            case "Quit":
                invocation.return_value(None)
                GLib.idle_add(self.quit)
                return
            case "Authorize":
                threading.Thread(target=self._authorize, args=(invocation,), daemon=True).start()
                return
            case "Status":
                invocation.return_value(GLib.Variant("(s)", (self._state,)))
                return
        invocation.return_value(None)

    def toggle(self) -> None:
        if self._state == "recording":
            self.stop()
        elif self._state == "idle":
            self.start()
        else:
            self._feedback.notify("Still processing", "Wait for the last dictation to finish", 2000)

    def reload(self) -> None:
        update_in_place(self._config, load_config())

    def start(self) -> None:
        if self._state != "idle":
            return
        try:
            plan = self._make_plan()
        except RuntimeError as exc:
            self._fail("Can't start dictation", str(exc))
            return
        if plan.route == "stream":
            plan.session = StreamSession(plan.key, self._config, self._on_partial)
            plan.session.start()
        try:
            self._recorder.start(
                self._config.max_seconds, plan.session.feed if plan.session else None
            )
        except Exception as exc:
            if plan.session:
                plan.session.abort()
            self._fail("Microphone unavailable", str(exc))
            return
        self._plan = plan
        self._cancelled.clear()
        self._last_preview = 0.0
        self._change_state("recording")
        self._feedback.sound("start")
        body = " · ".join(["Press the shortcut again to finish", *plan.notes])
        self._feedback.notify("Listening…", body)
        self._timeout_id = GLib.timeout_add_seconds(self._config.max_seconds, self._on_max_duration)

    def _make_plan(self) -> Plan:
        key = get_api_key()
        if not key:
            raise RuntimeError("No OpenAI API key. Add one in Blurt settings.")
        notes = []
        budget = self._config.monthly_budget
        if budget > 0 and usage.cost_since(usage.month_start()) >= budget:
            if self._config.budget_action == "block":
                raise RuntimeError(
                    f"The ${budget:.2f} monthly budget is used up. Raise it in Blurt settings."
                )
            notes.append(f"${budget:.2f} monthly budget reached")
        return Plan("stream" if self._config.streaming else "transcribe", key, notes)

    def _on_partial(self, text: str) -> None:
        now = time.monotonic()
        if now - self._last_preview < PREVIEW_INTERVAL or self._state != "recording":
            return
        self._last_preview = now
        tail = text[-160:]
        GLib.idle_add(self._feedback.notify, "Listening…", ("…" if len(text) > 160 else "") + tail)

    def stop(self) -> None:
        if self._state != "recording" or self._plan is None:
            return
        self._clear_timeout()
        audio = self._recorder.stop()
        self._feedback.sound("stop")
        self._change_state("processing")
        self._feedback.notify("Transcribing…", f"{seconds(audio):.1f}s of audio")
        plan, self._plan = self._plan, None
        threading.Thread(target=self._process, args=(audio, plan), daemon=True).start()

    def cancel(self) -> None:
        if self._state == "recording":
            self._clear_timeout()
            audio = self._recorder.stop()
            if self._plan and self._plan.session:
                self._plan.session.abort()
                self._record_stream(self._plan, seconds(audio))
            self._plan = None
            self._change_state("idle")
            self._feedback.notify("Dictation cancelled", "", 1500)
        elif self._state == "processing":
            self._cancelled.set()
            self._feedback.notify("Dictation cancelled", "", 1500)

    def _process(self, audio: bytes, plan: Plan) -> None:
        timings: dict[str, float] = {}
        try:
            if seconds(audio) < MIN_SECONDS:
                if plan.session:
                    plan.session.abort()
                GLib.idle_add(self._finish_empty)
                return
            started = time.monotonic()
            try:
                raw = self._transcribe(audio, plan)
            except Exception as exc:
                saved = self._save_failed(audio)
                GLib.idle_add(
                    self._fail, "Transcription failed", f"{exc}\nRecording saved to {saved}"
                )
                return
            timings["transcribe"] = time.monotonic() - started
            if not raw:
                GLib.idle_add(self._finish_empty)
                return
            if self._config.cleanup in ("openai", "codex"):
                GLib.idle_add(self._feedback.notify, "Cleaning up…", raw)
            started = time.monotonic()
            cleaned = clean(raw, self._config, plan.key)
            timings["cleanup"] = time.monotonic() - started
            if cleaned.warning:
                plan.notes.append(cleaned.warning)
            text = cleaned.text
            if self._config.trailing_space and text and not text.endswith((" ", "\n")):
                text += " "
            if self._cancelled.is_set():
                self._record_history(raw, text, None, timings)
                return
            started = time.monotonic()
            try:
                mode = self._injector.inject(text)
            except Exception as exc:
                self._record_history(raw, text, None, timings)
                self._injector.copy(text)
                GLib.idle_add(self._fail, "Couldn't type, copied to clipboard", f"{exc}")
                return
            timings["inject"] = time.monotonic() - started
            self._record_history(raw, text, mode, timings)
            GLib.idle_add(self._finish_ok, text, " · ".join(plan.notes) or None)
        except Exception as exc:
            GLib.idle_add(self._fail, "Dictation failed", f"{exc}")
        finally:
            GLib.idle_add(self._set_idle)

    def _transcribe(self, audio: bytes, plan: Plan) -> str:
        duration = seconds(audio)
        if plan.session:
            try:
                return plan.session.finish()
            except Exception as exc:
                plan.notes.append(f"Streaming failed ({exc}), uploaded instead")
            finally:
                self._record_stream(plan, duration)
        text = openai_api.transcribe(plan.key, self._config, openai_api.wav_bytes(audio))
        model = self._config.transcribe_model
        usage.record("transcribe", model, duration, cost=audio_cost(model, duration))
        return text

    def _record_stream(self, plan: Plan, duration: float) -> None:
        if plan.session is None or not plan.session.connected:
            return
        model = self._config.stream_model
        usage.record("stream", model, duration, cost=audio_cost(model, duration))

    def _save_failed(self, audio: bytes) -> Path:
        FAILED_DIR.mkdir(parents=True, exist_ok=True)
        target = FAILED_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
        target.write_bytes(openai_api.wav_bytes(audio))
        for old in sorted(FAILED_DIR.glob("*.wav"))[:-KEEP_FAILED]:
            old.unlink()
        return target

    def _finish_ok(self, text: str, warning: str | None) -> bool:
        self._feedback.notify(warning or "Dictated", text.strip(), 3000)
        return False

    def _finish_empty(self) -> bool:
        self._feedback.notify("No speech detected", "", 2000)
        return False

    def _fail(self, summary: str, body: str) -> bool:
        print(f"{summary}: {body}", flush=True)
        self._feedback.sound("error")
        self._feedback.notify(summary, body, 6000, urgent=True)
        if self._state != "processing":
            self._change_state("idle")
        return False

    def _set_idle(self) -> bool:
        self._change_state("idle")
        return False

    def _change_state(self, state: State) -> None:
        if state == self._state:
            return
        self._state = state
        self._bus.emit_signal(
            None, OBJECT_PATH, INTERFACE, "StateChanged", GLib.Variant("(s)", (state,))
        )

    def quit(self) -> bool:
        if self._state == "recording":
            self.cancel()
        self._loop.quit()
        return False

    def _authorize(self, invocation: Gio.DBusMethodInvocation) -> None:
        try:
            self._injector.authorize()
            invocation.return_value(None)
        except Exception as exc:
            invocation.return_dbus_error(f"{INTERFACE}.Error", str(exc))

    def _on_max_duration(self) -> bool:
        self._timeout_id = 0
        self.stop()
        return False

    def _clear_timeout(self) -> None:
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0

    def _record_history(
        self, raw: str, text: str, mode: str | None, timings: dict[str, float]
    ) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "raw": raw,
            "text": text,
            "inject": mode,
            "timings": {k: round(v, 2) for k, v in timings.items()},
        }
        with HISTORY_PATH.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
