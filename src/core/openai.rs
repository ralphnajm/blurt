//! The few OpenAI endpoints Blurt calls: transcriptions, chat completions and,
//! to check a key, the model list.

use std::sync::LazyLock;
use std::time::Duration;

use anyhow::{Result, bail};
use serde_json::{Value, json};

use super::config::Config;

const API_BASE: &str = "https://api.openai.com/v1";

static AGENT: LazyLock<ureq::Agent> = LazyLock::new(|| {
    ureq::Agent::config_builder()
        // Errors carry OpenAI's message, read from the body.
        .http_status_as_error(false)
        .max_idle_age(Duration::from_secs(300))
        .build()
        .into()
});

#[derive(Debug, Clone, PartialEq)]
pub struct TextResult {
    pub text: String,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

fn read(mut response: ureq::http::Response<ureq::Body>) -> Result<Value> {
    let status = response.status();
    let body = response.body_mut().read_to_string()?;
    if status.is_success() {
        return Ok(serde_json::from_str(&body)?);
    }
    let message = serde_json::from_str::<Value>(&body)
        .ok()
        .and_then(|value| value["error"]["message"].as_str().map(str::to_owned))
        .unwrap_or_else(|| body.chars().take(200).collect());
    bail!("OpenAI {}: {message}", status.as_u16())
}

pub fn check_key(key: &str) -> Result<()> {
    let response = AGENT
        .get(format!("{API_BASE}/models"))
        .header("Authorization", format!("Bearer {key}"))
        .config()
        .timeout_global(Some(Duration::from_secs(10)))
        .build()
        .call()?;
    read(response).map(drop)
}

/// A multipart/form-data body of text fields and one file.
fn multipart(boundary: &str, fields: &[(&str, &str)], filename: &str, file: &[u8]) -> Vec<u8> {
    let mut body = Vec::with_capacity(file.len() + 1024);
    for (name, value) in fields {
        body.extend_from_slice(
            format!("--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").as_bytes(),
        );
    }
    let filename = filename.replace(['"', '\r', '\n'], "_");
    body.extend_from_slice(
        format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n\
             Content-Type: application/octet-stream\r\n\r\n"
        )
        .as_bytes(),
    );
    body.extend_from_slice(file);
    body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
    body
}

pub fn transcribe(key: &str, config: &Config, audio: &[u8], filename: &str) -> Result<String> {
    let vocabulary = format!("Vocabulary: {}", config.vocabulary.join(", "));
    let mut fields = vec![("model", config.transcribe_model.as_str()), ("response_format", "json")];
    if config.language != "auto" && !config.language.is_empty() {
        fields.push(("language", &config.language));
    }
    if !config.vocabulary.is_empty() {
        fields.push(("prompt", &vocabulary));
    }
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
    let boundary = format!("blurt-{nanos:x}-{:x}", std::process::id());
    let body = multipart(&boundary, &fields, filename, audio);
    let response = AGENT
        .post(format!("{API_BASE}/audio/transcriptions"))
        .header("Authorization", format!("Bearer {key}"))
        .header("Content-Type", format!("multipart/form-data; boundary={boundary}"))
        .config()
        .timeout_global(Some(Duration::from_secs(60)))
        .build()
        .send(&body[..])?;
    let value = read(response)?;
    Ok(value["text"].as_str().unwrap_or_default().trim().to_owned())
}

pub fn complete(
    key: &str,
    model: &str,
    reasoning: &str,
    system: &str,
    user: &str,
    timeout_seconds: u64,
) -> Result<TextResult> {
    let body = json!({
        "model": model,
        "reasoning_effort": reasoning,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    });
    let response = AGENT
        .post(format!("{API_BASE}/chat/completions"))
        .header("Authorization", format!("Bearer {key}"))
        .config()
        .timeout_global(Some(Duration::from_secs(timeout_seconds)))
        .build()
        .send_json(&body)?;
    let payload = read(response)?;
    Ok(TextResult {
        text: payload["choices"][0]["message"]["content"].as_str().unwrap_or_default().trim().to_owned(),
        input_tokens: payload["usage"]["prompt_tokens"].as_u64().unwrap_or(0),
        output_tokens: payload["usage"]["completion_tokens"].as_u64().unwrap_or(0),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_multipart() {
        let body = multipart("b", &[("model", "m")], "a.wav", b"RIFF");
        let text = String::from_utf8(body).unwrap();
        assert!(text.starts_with("--b\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nm\r\n"));
        assert!(text.contains("filename=\"a.wav\""));
        assert!(text.ends_with("RIFF\r\n--b--\r\n"));
    }
}
