PRICES_AS_OF = "September 2026"

TRANSCRIBE_PER_MINUTE = {
    "gpt-transcribe": 0.0045,
    "gpt-4o-transcribe": 0.006,
    "gpt-4o-mini-transcribe": 0.003,
    "whisper-1": 0.006,
}
STREAM_PER_MINUTE = {
    "gpt-live-transcribe": 0.017,
}
TEXT_PER_MILLION = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
}
LOCAL_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
STREAM_DELAYS = ["minimal", "low", "medium", "high", "xhigh"]


def audio_cost(model: str, seconds: float) -> float:
    rate = TRANSCRIBE_PER_MINUTE.get(model) or STREAM_PER_MINUTE.get(model) or 0.0
    return rate * seconds / 60


def text_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    input_rate, output_rate = TEXT_PER_MILLION.get(model, (0.0, 0.0))
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
