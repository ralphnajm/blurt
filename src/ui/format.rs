//! How amounts, durations and dates read in the UI.

use chrono::{Datelike, Local, NaiveDateTime};

pub fn money(value: f64) -> String {
    if value == 0.0 {
        "$0.00".into()
    } else if value < 0.01 {
        format!("${value:.4}")
    } else {
        format!("${value:.2}")
    }
}

/// A per-unit price without trailing zeros: $0.0045, $0.1.
pub fn rate(value: f64) -> String {
    let text = format!("{value:.4}");
    format!("${}", text.trim_end_matches('0').trim_end_matches('.'))
}

pub fn minutes(seconds: f64) -> String {
    format!("{:.1} min", seconds / 60.0)
}

pub fn tokens(count: u64) -> String {
    if count >= 1000 { format!("{:.1}k tokens", count as f64 / 1000.0) } else { format!("{count} tokens") }
}

pub fn when(at: NaiveDateTime) -> String {
    let today = Local::now().date_naive();
    match (today - at.date()).num_days() {
        0 => format!("Today, {}", at.format("%H:%M")),
        1 => format!("Yesterday, {}", at.format("%H:%M")),
        _ if at.year() == today.year() => at.format("%b %-d, %H:%M").to_string(),
        _ => at.format("%b %-d %Y, %H:%M").to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats() {
        assert_eq!(money(0.0), "$0.00");
        assert_eq!(money(0.004), "$0.0040");
        assert_eq!(money(1.5), "$1.50");
        assert_eq!(rate(0.0045), "$0.0045");
        assert_eq!(rate(0.1), "$0.1");
        assert_eq!(tokens(999), "999 tokens");
        assert_eq!(tokens(1500), "1.5k tokens");
    }
}
