import threading

import numpy as np
from faster_whisper import WhisperModel

from blurt.config import Config
from blurt.recorder import WHISPER_RATE, resample


class LocalTranscriber:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._model: WhisperModel | None = None
        self._loaded_name = ""
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._config.local_model

    def ensure_loaded(self) -> WhisperModel:
        with self._lock:
            if self._model is None or self._loaded_name != self._config.local_model:
                self._model = WhisperModel(
                    self._config.local_model,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=self._config.threads,
                )
                self._loaded_name = self._config.local_model
            return self._model

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._loaded_name = ""

    def transcribe(self, audio: np.ndarray, rate: int) -> str:
        audio = resample(audio, rate, WHISPER_RATE)
        if audio.size < WHISPER_RATE // 4:
            return ""
        model = self.ensure_loaded()
        language = None if self._config.language == "auto" else self._config.language
        prompt = ", ".join(self._config.vocabulary) or None
        segments, _info = model.transcribe(
            audio,
            language=language,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=prompt,
        )
        return " ".join(s.text.strip() for s in segments).strip()
