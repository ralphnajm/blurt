import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from gi.repository import Gio, GLib

from blurt import history, openai_api, usage
from blurt.cleanup import clean
from blurt.config import STATE_DIR, Config, load_config, update_in_place
from blurt.credentials import get_api_key
from blurt.feedback import Feedback
from blurt.injector import Injector
from blurt.pricing import audio_cost
from blurt.recorder import Recorder, seconds

APP_ID = "dev.blurt.Blurt"
BUS_NAME = "dev.blurt.Daemon"
OBJECT_PATH = "/dev/blurt/Daemon"
INTERFACE = "dev.blurt.Daemon"
FAILED_DIR = STATE_DIR / "failed"
KEEP_FAILED = 20
INTROSPECTION = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Toggle"/>
    <method name="Start"/>
    <method name="HoldStart"/>
    <method name="Stop"/>
    <method name="Cancel"/>
    <method name="Reload"/>
    <method name="Quit"/>
    <method name="Status"><arg type="s" direction="out"/></method>
    <method name="HoldSettings">
      <arg type="s" name="key" direction="out"/>
      <arg type="d" name="delay" direction="out"/>
    </method>
    <method name="Message"><arg type="s" direction="out"/></method>
    <signal name="StateChanged"><arg type="s" name="state"/></signal>
    <signal name="MessageChanged"><arg type="s" name="message"/></signal>
    <signal name="HoldSettingsChanged">
      <arg type="s" name="key"/>
      <arg type="d" name="delay"/>
    </signal>
  </interface>
</node>
"""
MIN_SECONDS = 0.3

type State = Literal["idle", "recording", "processing"]


@dataclass
class Plan:
    key: str
    notes: list[str] = field(default_factory=list)


class Daemon:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._state: State = "idle"
        self._recorder = Recorder()
        self._feedback = Feedback(config)
        self._loop = GLib.MainLoop()
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._injector = Injector(self._bus)
        self._cancelled = threading.Event()
        self._timeout_id = 0
        self._plan: Plan | None = None
        self._message = ""

    def run(self) -> None:
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        self._bus.register_object(OBJECT_PATH, node.interfaces[0], self._on_call, None, None)
        Gio.bus_own_name_on_connection(
            self._bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, self._on_name_lost
        )
        self._loop.run()

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
            case "HoldStart":
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
            case "Status":
                invocation.return_value(GLib.Variant("(s)", (self._state,)))
                return
            case "HoldSettings":
                invocation.return_value(self._hold_settings())
                return
            case "Message":
                invocation.return_value(GLib.Variant("(s)", (self._message,)))
                return
        invocation.return_value(None)

    def toggle(self) -> None:
        if self._state == "recording":
            self.stop()
        elif self._state == "idle":
            self.start()

    def reload(self) -> None:
        update_in_place(self._config, load_config())
        self._bus.emit_signal(
            None, OBJECT_PATH, INTERFACE, "HoldSettingsChanged", self._hold_settings()
        )

    def _hold_settings(self) -> GLib.Variant:
        return GLib.Variant("(sd)", (self._config.hold_key, float(self._config.hold_delay)))

    def start(self) -> None:
        if self._state != "idle":
            return
        try:
            plan = self._make_plan()
        except RuntimeError as exc:
            self._fail("Can't start dictation", str(exc))
            return
        try:
            self._recorder.start(self._config.max_seconds)
        except Exception as exc:
            self._fail("Microphone unavailable", str(exc))
            return
        self._plan = plan
        self._cancelled.clear()
        self._change_state("recording")
        self._feedback.sound("start")
        self._set_message(" · ".join(plan.notes))
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
        return Plan(key, notes)

    def stop(self) -> None:
        if self._state != "recording" or self._plan is None:
            return
        self._clear_timeout()
        audio = self._recorder.stop()
        self._feedback.sound("stop")
        self._change_state("processing")
        plan, self._plan = self._plan, None
        threading.Thread(target=self._process, args=(audio, plan), daemon=True).start()

    def cancel(self) -> None:
        if self._state == "recording":
            self._clear_timeout()
            self._recorder.stop()
            self._plan = None
            self._change_state("idle")
        elif self._state == "processing":
            self._cancelled.set()

    def _process(self, audio: bytes, plan: Plan) -> None:
        timings: dict[str, float] = {}
        try:
            if seconds(audio) < MIN_SECONDS:
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
            started = time.monotonic()
            cleaned = clean(raw, self._config, plan.key)
            timings["cleanup"] = time.monotonic() - started
            if cleaned.warning:
                plan.notes.append(cleaned.warning)
            text = cleaned.text
            if self._config.trailing_space and text and not text.endswith((" ", "\n")):
                text += " "
            if self._cancelled.is_set():
                history.record(raw, text, None, timings)
                return
            started = time.monotonic()
            try:
                mode = self._injector.inject(text)
            except Exception as exc:
                history.record(raw, text, None, timings)
                self._injector.copy(text)
                GLib.idle_add(self._fail, "Copied to clipboard instead of typing", f"{exc}")
                return
            timings["inject"] = time.monotonic() - started
            history.record(raw, text, mode, timings)
            GLib.idle_add(self._finish_ok, text, " · ".join(plan.notes) or None)
        except Exception as exc:
            GLib.idle_add(self._fail, "Dictation failed", f"{exc}")
        finally:
            GLib.idle_add(self._set_idle)

    def _transcribe(self, audio: bytes, plan: Plan) -> str:
        duration = seconds(audio)
        text = openai_api.transcribe(plan.key, self._config, openai_api.wav_bytes(audio))
        model = self._config.transcribe_model
        usage.record("transcribe", model, duration, cost=audio_cost(model, duration))
        return text

    def _save_failed(self, audio: bytes) -> Path:
        FAILED_DIR.mkdir(parents=True, exist_ok=True)
        target = FAILED_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
        target.write_bytes(openai_api.wav_bytes(audio))
        for old in sorted(FAILED_DIR.glob("*.wav"))[:-KEEP_FAILED]:
            old.unlink()
        return target

    def _finish_ok(self, text: str, warning: str | None) -> bool:
        self._set_message(warning or "")
        return False

    def _finish_empty(self) -> bool:
        self._set_message("No speech detected")
        return False

    def _fail(self, summary: str, body: str) -> bool:
        print(f"{summary}: {body}", flush=True)
        self._feedback.sound("error")
        self._set_message(f"{summary}: {body}")
        if self._state != "processing":
            self._change_state("idle")
        return False

    def _set_idle(self) -> bool:
        self._change_state("idle")
        return False

    def _set_message(self, message: str) -> None:
        if message == self._message:
            return
        self._message = message
        self._bus.emit_signal(
            None, OBJECT_PATH, INTERFACE, "MessageChanged", GLib.Variant("(s)", (message,))
        )

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

    def _on_max_duration(self) -> bool:
        self._timeout_id = 0
        self.stop()
        return False

    def _clear_timeout(self) -> None:
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0
