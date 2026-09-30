import base64
import io
import json
import queue
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import numpy as np
from websockets.sync.client import ClientConnection, connect

from blurt.config import Config
from blurt.recorder import STREAM_RATE, to_pcm16

API_BASE = "https://api.openai.com/v1"
REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
COMMIT = object()
ABORT = object()


class OpenAIError(RuntimeError):
    pass


@dataclass
class TextResult:
    text: str
    input_tokens: int
    output_tokens: int


def headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = response.text[:200]
    raise OpenAIError(f"OpenAI {response.status_code}: {message}")


def check_key(key: str) -> None:
    response = httpx.get(f"{API_BASE}/models", headers=headers(key), timeout=10)
    raise_for_status(response)


def wav_bytes(audio: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(to_pcm16(audio))
    return buffer.getvalue()


def transcribe(key: str, config: Config, audio: np.ndarray, rate: int) -> str:
    data = {"model": config.transcribe_model, "response_format": "json"}
    if config.language != "auto":
        data["language"] = config.language
    if config.vocabulary:
        data["prompt"] = "Vocabulary: " + ", ".join(config.vocabulary)
    response = httpx.post(
        f"{API_BASE}/audio/transcriptions",
        headers=headers(key),
        data=data,
        files={"file": ("dictation.wav", wav_bytes(audio, rate), "audio/wav")},
        timeout=60,
    )
    raise_for_status(response)
    return str(response.json().get("text", "")).strip()


def complete(key: str, model: str, system: str, user: str, timeout: float) -> TextResult:
    body: dict[str, object] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if model.startswith("gpt-5"):
        body["reasoning_effort"] = "minimal"
    response = httpx.post(
        f"{API_BASE}/chat/completions", headers=headers(key), json=body, timeout=timeout
    )
    raise_for_status(response)
    payload = response.json()
    usage = payload.get("usage") or {}
    return TextResult(
        text=str(payload["choices"][0]["message"]["content"] or "").strip(),
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
    )


class StreamSession:
    def __init__(self, key: str, config: Config, on_partial: Callable[[str], None]) -> None:
        self._key = key
        self._config = config
        self._on_partial = on_partial
        self._outbox: queue.Queue[object] = queue.Queue()
        self._order: list[str] = []
        self._text: dict[str, str] = {}
        self._pending: set[str] = set()
        self._error: Exception | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.connected = False

    @property
    def transcript(self) -> str:
        return " ".join(self._text[i].strip() for i in self._order if self._text.get(i)).strip()

    def start(self) -> None:
        self._thread.start()

    def feed(self, chunk: np.ndarray) -> None:
        self._outbox.put(to_pcm16(chunk))

    def finish(self, timeout: float = 15) -> str:
        self._outbox.put(COMMIT)
        self._thread.join(timeout)
        if self._thread.is_alive():
            self._outbox.put(ABORT)
            raise OpenAIError("Streaming transcription timed out")
        if self._error is not None:
            raise self._error
        return self.transcript

    def abort(self) -> None:
        self._outbox.put(ABORT)

    def _session_update(self) -> str:
        transcription: dict[str, object] = {"model": self._config.stream_model}
        if self._config.stream_delay:
            transcription["delay"] = self._config.stream_delay
        if self._config.language != "auto":
            transcription["languages"] = [self._config.language]
        if self._config.vocabulary:
            transcription["keywords"] = self._config.vocabulary
        return json.dumps(
            {
                "type": "session.update",
                "session": {
                    "type": "transcription",
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": STREAM_RATE},
                            "transcription": transcription,
                            "turn_detection": None,
                        }
                    },
                },
            }
        )

    def _run(self) -> None:
        try:
            with connect(
                REALTIME_URL, additional_headers=headers(self._key), open_timeout=10
            ) as ws:
                self.connected = True
                ws.send(self._session_update())
                self._loop(ws)
        except Exception as exc:
            self._error = exc if isinstance(exc, OpenAIError) else OpenAIError(str(exc))

    def _loop(self, ws: ClientConnection) -> None:
        committed_at: float | None = None
        commit_sent = False
        while True:
            while True:
                try:
                    item = self._outbox.get_nowait()
                except queue.Empty:
                    break
                if item is ABORT:
                    return
                if item is COMMIT:
                    ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    commit_sent = True
                    committed_at = time.monotonic()
                elif isinstance(item, bytes):
                    audio = base64.b64encode(item).decode()
                    ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": audio}))
            try:
                message = ws.recv(timeout=0.05)
            except TimeoutError:
                message = None
            if message is not None and self._handle(json.loads(message), commit_sent):
                return
            if committed_at is not None and time.monotonic() - committed_at > 12:
                raise OpenAIError("No final transcript after commit")

    def _handle(self, event: dict[str, object], commit_sent: bool) -> bool:
        kind = event.get("type")
        item_id = str(event.get("item_id", ""))
        if kind == "input_audio_buffer.committed":
            self._track(item_id)
            self._pending.add(item_id)
        elif kind == "conversation.item.input_audio_transcription.delta":
            self._track(item_id)
            self._text[item_id] = self._text.get(item_id, "") + str(event.get("delta", ""))
            self._on_partial(self.transcript)
        elif kind == "conversation.item.input_audio_transcription.completed":
            self._track(item_id)
            self._text[item_id] = str(event.get("transcript", ""))
            self._pending.discard(item_id)
            if commit_sent and not self._pending:
                return True
        elif kind == "conversation.item.input_audio_transcription.failed":
            raise OpenAIError(f"Transcription failed: {event.get('error')}")
        elif kind == "error":
            error = event.get("error") or {}
            code = str(error.get("code", "")) if isinstance(error, dict) else ""
            if commit_sent and "empty" in code:
                return True
            message = error.get("message") if isinstance(error, dict) else error
            raise OpenAIError(f"Realtime error: {message}")
        return False

    def _track(self, item_id: str) -> None:
        if item_id and item_id not in self._order:
            self._order.append(item_id)
