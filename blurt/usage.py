import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from blurt.config import STATE_DIR

DB_PATH = STATE_DIR / "usage.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    model TEXT NOT NULL,
    audio_seconds REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_at ON usage (at);
"""


@dataclass
class ModelUsage:
    kind: str
    model: str
    requests: int
    audio_seconds: float
    input_tokens: int
    output_tokens: int
    cost: float


@dataclass
class Request:
    at: datetime
    kind: str
    model: str
    audio_seconds: float
    input_tokens: int
    output_tokens: int
    cost: float


def connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.executescript(SCHEMA)
    return conn


def record(
    kind: str,
    model: str,
    audio_seconds: float = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: float = 0,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO usage (at, kind, model, audio_seconds, input_tokens, output_tokens, cost)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                kind,
                model,
                audio_seconds,
                input_tokens,
                output_tokens,
                cost,
            ),
        )


def month_start(today: date | None = None) -> datetime:
    today = today or date.today()
    return datetime(today.year, today.month, 1)


def cost_since(since: datetime) -> float:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost), 0) FROM usage WHERE at >= ?", (since.isoformat(),)
        ).fetchone()
    return float(row[0])


def audio_seconds_since(since: datetime) -> float:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(audio_seconds), 0) FROM usage"
            " WHERE at >= ? AND kind IN ('transcribe', 'stream')",
            (since.isoformat(),),
        ).fetchone()
    return float(row[0])


def daily_costs(days: int = 30) -> list[tuple[date, float]]:
    first = date.today() - timedelta(days=days - 1)
    with connect() as conn:
        rows = conn.execute(
            "SELECT substr(at, 1, 10), SUM(cost) FROM usage WHERE at >= ? GROUP BY 1",
            (first.isoformat(),),
        ).fetchall()
    totals = {date.fromisoformat(day): float(cost) for day, cost in rows}
    return [
        (first + timedelta(days=i), totals.get(first + timedelta(days=i), 0.0)) for i in range(days)
    ]


def by_model(since: datetime) -> list[ModelUsage]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT kind, model, COUNT(*), SUM(audio_seconds), SUM(input_tokens),"
            " SUM(output_tokens), SUM(cost) FROM usage WHERE at >= ?"
            " GROUP BY kind, model ORDER BY SUM(cost) DESC, COUNT(*) DESC",
            (since.isoformat(),),
        ).fetchall()
    return [ModelUsage(*row) for row in rows]


def recent(limit: int = 15) -> list[Request]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT at, kind, model, audio_seconds, input_tokens, output_tokens, cost"
            " FROM usage ORDER BY at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [Request(datetime.fromisoformat(row[0]), *row[1:]) for row in rows]
