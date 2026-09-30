import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd

WHISPER_RATE = 16000
STREAM_RATE = 24000


class Recorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._max_frames = 0
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._on_chunk: Callable[[np.ndarray], None] | None = None
        self.rate = WHISPER_RATE

    def start(
        self, rate: int, max_seconds: int, on_chunk: Callable[[np.ndarray], None] | None = None
    ) -> None:
        self._chunks = []
        self._frames = 0
        self._max_frames = max_seconds * rate
        self._on_chunk = on_chunk
        self.rate = rate
        self._stream = sd.InputStream(
            samplerate=rate,
            channels=1,
            dtype="float32",
            blocksize=rate // 10,
            callback=self._on_audio,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self._on_chunk = None
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._chunks)

    def _on_audio(self, data: np.ndarray, frames: int, _time: object, _status: object) -> None:
        chunk = data[:, 0].copy()
        with self._lock:
            if self._frames >= self._max_frames:
                return
            self._chunks.append(chunk)
            self._frames += frames
        if self._on_chunk is not None:
            self._on_chunk(chunk)


def resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or audio.size == 0:
        return audio
    duration = audio.size / source_rate
    target = np.linspace(0, duration, int(duration * target_rate), endpoint=False)
    source = np.arange(audio.size) / source_rate
    return np.interp(target, source, audio).astype(np.float32)


def to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
