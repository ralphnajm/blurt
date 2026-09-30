import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import numpy as np
from gi.repository import Gio, GLib

from blurt import openai_api, usage
from blurt.cleanup import clean
from blurt.config import STATE_DIR, Config, load_config, update_in_place
from blurt.credentials import get_api_key
from blurt.feedback import Feedback
from blurt.injector import Injector
from blurt.openai_api import StreamSession
from blurt.pricing import audio_cost
from blurt.recorder import STREAM_RATE, WHISPER_RATE, Recorder
from blurt.transcriber import LocalTranscriber

APP_ID = "dev.blurt.Blurt"
BUS_NAME = "dev.blurt.Daemon"
OBJECT_PATH = "/dev/blurt/Daemon"
INTERFACE = "dev.blurt.Daemon"
HISTORY_PATH = STATE_DIR / "history.jsonl"
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
type Route = Literal["stream", "transcribe", "local"]


@dataclass
class Plan:
    route: Route
    key: str | None
    notes: list[str] = field(default_factory=list)
    session: StreamSession | None = None


class Daemon:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._state: State = "idle"
        self._recorder = Recorder()
        self._local = LocalTranscriber(config)
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
        if self._config.engine == "local":
            self._preload_local()
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

    def _preload_local(self) -> None:
        def load() -> None:
            started = time.monotonic()
            try:
                self._local.ensure_loaded()
                print(
                    f"Whisper {self._config.local_model} ready in {time.monotonic() - started:.1f}s",
                    flush=True,
                )
            except Exception as exc:
                print(f"Whisper failed to load: {exc}", flush=True)

        threading.Thread(target=load, daemon=True).start()

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
        if self._config.engine == "local":
            self._preload_local()
        elif not self._config.fallback_local:
            self._local.unload()

    def start(self) -> None:
        if self._state != "idle":
            return
        try:
            plan = self._make_plan()
        except RuntimeError as exc:
            self._fail("Can't start dictation", str(exc))
            return
        rate = STREAM_RATE if plan.route == "stream" else WHISPER_RATE
        if plan.route == "stream" and plan.key:
            plan.session = StreamSession(plan.key, self._config, self._on_partial)
            plan.session.start()
        elif plan.route == "local":
            self._preload_local()
        try:
            self._recorder.start(
                rate, self._config.max_seconds, plan.session.feed if plan.session else None
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
        route = {"stream": "streaming", "transcribe": "OpenAI", "local": "local Whisper"}[
            plan.route
        ]
        body = " · ".join([f"Press the shortcut again to finish ({route})", *plan.notes])
        self._feedback.notify("Listening…", body)
        self._timeout_id = GLib.timeout_add_seconds(self._config.max_seconds, self._on_max_duration)

    def _make_plan(self) -> Plan:
        if self._config.engine == "local":
            return Plan("local", None)
        key = get_api_key()
        if not key:
            if self._config.fallback_local:
                return Plan("local", None, ["No OpenAI API key, using local Whisper"])
            raise RuntimeError("No OpenAI API key. Add one in Blurt settings.")
        notes = []
        budget = self._config.monthly_budget
        if budget > 0 and usage.cost_since(usage.month_start()) >= budget:
            if self._config.budget_action == "local":
                return Plan(
                    "local", key, [f"${budget:.2f} monthly budget reached, using local Whisper"]
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
        rate = self._recorder.rate
        audio = self._recorder.stop()
        self._feedback.sound("stop")
        self._change_state("processing")
        self._feedback.notify("Transcribing…", f"{audio.size / rate:.1f}s of audio")
        plan, self._plan = self._plan, None
        threading.Thread(target=self._process, args=(audio, rate, plan), daemon=True).start()

    def cancel(self) -> None:
        if self._state == "recording":
            self._clear_timeout()
            rate = self._recorder.rate
            audio = self._recorder.stop()
            if self._plan and self._plan.session:
                self._plan.session.abort()
                self._record_stream(self._plan, rate, audio.size / rate)
            self._plan = None
            self._change_state("idle")
            self._feedback.notify("Dictation cancelled", "", 1500)
        elif self._state == "processing":
            self._cancelled.set()
            self._feedback.notify("Dictation cancelled", "", 1500)

    def _process(self, audio: np.ndarray, rate: int, plan: Plan) -> None:
        timings: dict[str, float] = {}
        try:
            seconds = audio.size / rate
            if seconds < MIN_SECONDS:
                if plan.session:
                    plan.session.abort()
                GLib.idle_add(self._finish_empty)
                return
            started = time.monotonic()
            raw = self._transcribe(audio, rate, plan)
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

    def _transcribe(self, audio: np.ndarray, rate: int, plan: Plan) -> str:
        seconds = audio.size / rate
        if plan.route == "stream" and plan.session:
            try:
                return plan.session.finish()
            except Exception as exc:
                plan.notes.append(f"Streaming failed ({exc})")
            finally:
                self._record_stream(plan, rate, seconds)
        if plan.route in ("stream", "transcribe") and plan.key:
            try:
                text = openai_api.transcribe(plan.key, self._config, audio, rate)
                model = self._config.transcribe_model
                usage.record("transcribe", model, seconds, cost=audio_cost(model, seconds))
                return text
            except Exception as exc:
                if not self._config.fallback_local:
                    raise
                plan.notes.append(f"OpenAI failed ({exc}), used local Whisper")
        text = self._local.transcribe(audio, rate)
        usage.record("local", self._config.local_model, seconds)
        return text

    def _record_stream(self, plan: Plan, rate: int, seconds: float) -> None:
        if plan.session is None or not plan.session.connected:
            return
        model = self._config.stream_model
        usage.record("stream", model, seconds, cost=audio_cost(model, seconds))

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
