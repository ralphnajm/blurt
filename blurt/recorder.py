import threading
from collections.abc import Callable

import sounddevice as sd

RATE = 24000
SAMPLE_BYTES = 2


def seconds(pcm: bytes) -> float:
    return len(pcm) / (RATE * SAMPLE_BYTES)


class Recorder:
    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._size = 0
        self._max_size = 0
        self._lock = threading.Lock()
        self._stream: sd.RawInputStream | None = None
        self._on_chunk: Callable[[bytes], None] | None = None

    def start(self, max_seconds: int, on_chunk: Callable[[bytes], None] | None = None) -> None:
        self._chunks = []
        self._size = 0
        self._max_size = max_seconds * RATE * SAMPLE_BYTES
        self._on_chunk = on_chunk
        self._stream = sd.RawInputStream(
            samplerate=RATE,
            channels=1,
            dtype="int16",
            blocksize=RATE // 10,
            callback=self._on_audio,
        )
        self._stream.start()

    def stop(self) -> bytes:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self._on_chunk = None
        with self._lock:
            return b"".join(self._chunks)

    def _on_audio(self, data: object, _frames: int, _time: object, _status: object) -> None:
        chunk = bytes(data)  # type: ignore[call-overload]
        with self._lock:
            if self._size >= self._max_size:
                return
            self._chunks.append(chunk)
            self._size += len(chunk)
        if self._on_chunk is not None:
            self._on_chunk(chunk)
