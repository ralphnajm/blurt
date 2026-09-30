PRICES_AS_OF = "September 2026"

TRANSCRIBE_PER_MINUTE = {
    "gpt-transcribe": 0.0045,
    "gpt-4o-transcribe": 0.006,
    "gpt-4o-mini-transcribe": 0.003,
    "whisper-1": 0.006,
}
CLEANUP_MODEL = "gpt-6-luna"
TEXT_PER_MILLION = {
    CLEANUP_MODEL: (0.10, 0.50),
}


def audio_cost(model: str, seconds: float) -> float:
    rate = TRANSCRIBE_PER_MINUTE.get(model, 0.0)
    return rate * seconds / 60


def text_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    input_rate, output_rate = TEXT_PER_MILLION.get(model, (0.0, 0.0))
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
