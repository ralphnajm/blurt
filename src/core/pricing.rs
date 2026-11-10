//! OpenAI list prices, used to estimate what each request cost.

pub const PRICES_AS_OF: &str = "September 2026";

/// Transcription models and their price per minute of audio.
pub const TRANSCRIBE_PER_MINUTE: &[(&str, f64)] = &[
    ("gpt-transcribe", 0.0045),
    ("gpt-4o-transcribe", 0.006),
    ("gpt-4o-mini-transcribe", 0.003),
    ("whisper-1", 0.006),
];

pub const CLEANUP_MODEL: &str = "gpt-6-luna";
/// The cleanup model's price per million input and output tokens.
pub const CLEANUP_PER_MILLION: (f64, f64) = (0.10, 0.50);

pub fn transcribe_rate(model: &str) -> Option<f64> {
    TRANSCRIBE_PER_MINUTE.iter().find(|(name, _)| *name == model).map(|(_, rate)| *rate)
}

pub fn audio_cost(model: &str, seconds: f64) -> f64 {
    transcribe_rate(model).unwrap_or(0.0) * seconds / 60.0
}

pub fn text_cost(model: &str, input_tokens: u64, output_tokens: u64) -> f64 {
    let (input, output) = if model == CLEANUP_MODEL { CLEANUP_PER_MILLION } else { (0.0, 0.0) };
    (input_tokens as f64 * input + output_tokens as f64 * output) / 1_000_000.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn costs() {
        assert!((audio_cost("gpt-transcribe", 60.0) - 0.0045).abs() < 1e-12);
        assert!(audio_cost("unknown", 60.0).abs() < 1e-12);
        assert!((text_cost(CLEANUP_MODEL, 1_000_000, 1_000_000) - 0.6).abs() < 1e-12);
    }
}
