//! Estimated spend per request, in `~/.local/state/blurt/usage.db`.

use chrono::{Datelike, Duration, Local, NaiveDate, NaiveDateTime};
use rusqlite::{Connection, params};

use super::config::state_dir;
use anyhow::Result;

const SCHEMA: &str = "
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
";

/// One request to record.
#[derive(Debug, Default)]
pub struct Entry<'a> {
    pub kind: &'a str,
    pub model: &'a str,
    pub audio_seconds: f64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cost: f64,
}

#[derive(Debug, Clone)]
pub struct Totals {
    pub kind: String,
    pub model: String,
    pub requests: u64,
    pub audio_seconds: f64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cost: f64,
}

#[derive(Debug, Clone)]
pub struct Request {
    pub at: NaiveDateTime,
    pub kind: String,
    pub model: String,
    pub audio_seconds: f64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cost: f64,
}

/// Timestamps are stored as local ISO 8601 text, which sorts by time.
fn iso(at: NaiveDateTime) -> String {
    at.format("%Y-%m-%dT%H:%M:%S").to_string()
}

fn connect() -> Result<Connection> {
    let dir = state_dir();
    std::fs::create_dir_all(&dir)?;
    let conn = Connection::open(dir.join("usage.db"))?;
    conn.busy_timeout(std::time::Duration::from_secs(5))?;
    conn.execute_batch(SCHEMA)?;
    Ok(conn)
}

pub fn record(entry: &Entry) -> Result<()> {
    connect()?.execute(
        "INSERT INTO usage (at, kind, model, audio_seconds, input_tokens, output_tokens, cost)
         VALUES (?, ?, ?, ?, ?, ?, ?)",
        params![
            iso(Local::now().naive_local()),
            entry.kind,
            entry.model,
            entry.audio_seconds,
            entry.input_tokens as i64,
            entry.output_tokens as i64,
            entry.cost
        ],
    )?;
    Ok(())
}

pub fn today_start() -> NaiveDateTime {
    Local::now().date_naive().and_hms_opt(0, 0, 0).unwrap()
}

pub fn month_start() -> NaiveDateTime {
    let today = Local::now().date_naive();
    NaiveDate::from_ymd_opt(today.year(), today.month(), 1).unwrap().and_hms_opt(0, 0, 0).unwrap()
}

pub fn cost_since(since: NaiveDateTime) -> Result<f64> {
    Ok(connect()?
        .query_row("SELECT COALESCE(SUM(cost), 0) FROM usage WHERE at >= ?", [iso(since)], |row| row.get(0))?)
}

pub fn audio_seconds_since(since: NaiveDateTime) -> Result<f64> {
    Ok(connect()?.query_row(
        "SELECT COALESCE(SUM(audio_seconds), 0) FROM usage WHERE at >= ? AND kind IN ('transcribe', 'stream')",
        [iso(since)],
        |row| row.get(0),
    )?)
}

/// The cost of each of the last `days` days, oldest first.
pub fn daily_costs(days: i64) -> Result<Vec<(NaiveDate, f64)>> {
    let first = Local::now().date_naive() - Duration::days(days - 1);
    let conn = connect()?;
    let mut statement = conn.prepare("SELECT substr(at, 1, 10), SUM(cost) FROM usage WHERE at >= ? GROUP BY 1")?;
    let totals: Vec<(String, f64)> = statement
        .query_map([first.format("%Y-%m-%d").to_string()], |row| Ok((row.get(0)?, row.get(1)?)))?
        .collect::<rusqlite::Result<_>>()?;
    Ok((0..days)
        .map(|offset| {
            let day = first + Duration::days(offset);
            let key = day.format("%Y-%m-%d").to_string();
            (day, totals.iter().find(|(d, _)| *d == key).map_or(0.0, |(_, cost)| *cost))
        })
        .collect())
}

pub fn by_model(since: NaiveDateTime) -> Result<Vec<Totals>> {
    let conn = connect()?;
    let mut statement = conn.prepare(
        "SELECT kind, model, COUNT(*), SUM(audio_seconds), SUM(input_tokens), SUM(output_tokens), SUM(cost)
         FROM usage WHERE at >= ? GROUP BY kind, model ORDER BY SUM(cost) DESC, COUNT(*) DESC",
    )?;
    let rows = statement.query_map([iso(since)], |row| {
        Ok(Totals {
            kind: row.get(0)?,
            model: row.get(1)?,
            requests: row.get::<_, i64>(2)? as u64,
            audio_seconds: row.get(3)?,
            input_tokens: row.get::<_, i64>(4)? as u64,
            output_tokens: row.get::<_, i64>(5)? as u64,
            cost: row.get(6)?,
        })
    })?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

pub fn recent(limit: u32) -> Result<Vec<Request>> {
    let conn = connect()?;
    let mut statement = conn.prepare(
        "SELECT at, kind, model, audio_seconds, input_tokens, output_tokens, cost
         FROM usage ORDER BY at DESC, id DESC LIMIT ?",
    )?;
    let rows = statement.query_map([limit], |row| {
        let at: String = row.get(0)?;
        Ok(Request {
            at: NaiveDateTime::parse_from_str(&at, "%Y-%m-%dT%H:%M:%S").unwrap_or_default(),
            kind: row.get(1)?,
            model: row.get(2)?,
            audio_seconds: row.get(3)?,
            input_tokens: row.get::<_, i64>(4)? as u64,
            output_tokens: row.get::<_, i64>(5)? as u64,
            cost: row.get(6)?,
        })
    })?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}
