import json
from dataclasses import dataclass
from datetime import datetime

from blurt.config import STATE_DIR

HISTORY_PATH = STATE_DIR / "history.jsonl"


@dataclass
class Entry:
    at: datetime
    text: str
    typed: bool


def record(raw: str, text: str, mode: str | None, timings: dict[str, float]) -> None:
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


def read(offset: int = 0) -> tuple[list[Entry], int, bool]:
    """Entries written after byte `offset`, oldest first, the offset to read from next, and
    whether the file was replaced so the entries start over from the beginning."""
    try:
        with HISTORY_PATH.open("rb") as f:
            reset = f.seek(0, 2) < offset
            if reset:
                offset = 0
            f.seek(offset)
            data = f.read()
    except FileNotFoundError:
        return [], 0, offset > 0
    # A line still being written has no newline yet; pick it up next time.
    complete = data[: data.rfind(b"\n") + 1]
    entries = []
    for line in complete.decode().splitlines():
        try:
            item = json.loads(line)
            entries.append(
                Entry(
                    datetime.fromisoformat(item["at"]),
                    item["text"].strip(),
                    item.get("inject", "keys") is not None,
                )
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return [e for e in entries if e.text], offset + len(complete), reset
