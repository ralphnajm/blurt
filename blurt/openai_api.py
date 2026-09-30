import io
import wave
from dataclasses import dataclass

import httpx

from blurt.config import Config
from blurt.recorder import RATE, SAMPLE_BYTES

API_BASE = "https://api.openai.com/v1"


_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(limits=httpx.Limits(keepalive_expiry=300))
    return _client


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
    response = client().get(f"{API_BASE}/models", headers=headers(key), timeout=10)
    raise_for_status(response)


def wav_bytes(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(SAMPLE_BYTES)
        out.setframerate(RATE)
        out.writeframes(pcm)
    return buffer.getvalue()


def transcribe(key: str, config: Config, audio: bytes, filename: str = "dictation.wav") -> str:
    data = {"model": config.transcribe_model, "response_format": "json"}
    if config.language != "auto":
        data["language"] = config.language
    if config.vocabulary:
        data["prompt"] = "Vocabulary: " + ", ".join(config.vocabulary)
    response = client().post(
        f"{API_BASE}/audio/transcriptions",
        headers=headers(key),
        data=data,
        files={"file": (filename, audio)},
        timeout=60,
    )
    raise_for_status(response)
    return str(response.json().get("text", "")).strip()


def complete(
    key: str, model: str, reasoning: str, system: str, user: str, timeout: float
) -> TextResult:
    body: dict[str, object] = {
        "model": model,
        "reasoning_effort": reasoning,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    response = client().post(
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
