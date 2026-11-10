//! Everything dictated, one JSON object per line in `~/.local/state/blurt/history.jsonl`.

use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::PathBuf;

use anyhow::Result;
use chrono::{Local, NaiveDateTime};
use serde_json::{Map, Value, json};

use super::config::state_dir;

#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub at: NaiveDateTime,
    pub text: String,
    /// False when the text was copied or dropped instead of typed.
    pub typed: bool,
}

pub fn path() -> PathBuf {
    state_dir().join("history.jsonl")
}

/// Appends a dictation: the transcript, the text it became, how it was typed
/// (`None` when it wasn't) and how long each step took, in seconds.
pub fn record(raw: &str, text: &str, inject: Option<&str>, timings: &[(&str, f64)]) -> Result<()> {
    fs::create_dir_all(state_dir())?;
    let timings: Map<String, Value> =
        timings.iter().map(|(step, seconds)| ((*step).to_owned(), json!((seconds * 100.0).round() / 100.0))).collect();
    let entry = json!({
        "at": Local::now().naive_local().format("%Y-%m-%dT%H:%M:%S").to_string(),
        "raw": raw,
        "text": text,
        "inject": inject,
        "timings": timings,
    });
    let mut file = OpenOptions::new().create(true).append(true).open(path())?;
    writeln!(file, "{entry}")?;
    Ok(())
}

/// What `read` found after an offset.
#[derive(Debug, Default)]
pub struct Chunk {
    /// New entries, oldest first.
    pub entries: Vec<Entry>,
    /// The offset to read from next time.
    pub offset: u64,
    /// The file was replaced: the entries start over from the beginning.
    pub reset: bool,
}

pub fn read(offset: u64) -> Chunk {
    read_from(&path(), offset)
}

fn read_from(path: &std::path::Path, offset: u64) -> Chunk {
    let Ok(mut file) = fs::File::open(path) else {
        return Chunk { reset: offset > 0, ..Chunk::default() };
    };
    let size = file.seek(SeekFrom::End(0)).unwrap_or(0);
    let reset = size < offset;
    let offset = if reset { 0 } else { offset };
    let mut data = Vec::new();
    if file.seek(SeekFrom::Start(offset)).is_err() || file.read_to_end(&mut data).is_err() {
        return Chunk { offset, reset, ..Chunk::default() };
    }
    // A line still being written has no newline yet; pick it up next time.
    let complete = data.iter().rposition(|b| *b == b'\n').map_or(0, |i| i + 1);
    let entries = String::from_utf8_lossy(&data[..complete]).lines().filter_map(parse).collect();
    Chunk { entries, offset: offset + complete as u64, reset }
}

fn parse(line: &str) -> Option<Entry> {
    let item: Value = serde_json::from_str(line).ok()?;
    let at = item["at"].as_str()?;
    let at = NaiveDateTime::parse_from_str(at, "%Y-%m-%dT%H:%M:%S")
        .or_else(|_| NaiveDateTime::parse_from_str(at, "%Y-%m-%dT%H:%M:%S%.f"))
        .ok()?;
    let text = item["text"].as_str()?.trim().to_owned();
    if text.is_empty() {
        return None;
    }
    // Entries from before `inject` was recorded were typed.
    let typed = item.get("inject").is_none_or(|inject| !inject.is_null());
    Some(Entry { at, text, typed })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_incrementally() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("history.jsonl");
        fs::write(
            &path,
            "{\"at\": \"2026-11-04T17:10:00\", \"raw\": \"x\", \"text\": \"Hello \", \"inject\": \"commit\"}\n\
             {\"at\": \"2026-11-04T17:11:00\", \"text\": \"Not typed\", \"inject\": null}\n\
             {\"at\": \"2026-11-04T17:12:00\", \"text\": \"   \"}\n{\"at\": \"2026-11",
        )
        .unwrap();
        let first = read_from(&path, 0);
        assert_eq!(first.entries.len(), 2);
        assert_eq!(first.entries[0].text, "Hello");
        assert!(first.entries[0].typed);
        assert!(!first.entries[1].typed);
        assert!(!first.reset);
        let mut file = OpenOptions::new().append(true).open(&path).unwrap();
        writeln!(file, "-04T17:13:00\", \"text\": \"Later\"}}").unwrap();
        let second = read_from(&path, first.offset);
        assert_eq!(second.entries.len(), 1);
        assert_eq!(second.entries[0].text, "Later");
        fs::write(&path, "").unwrap();
        assert!(read_from(&path, second.offset).reset);
    }
}
