//! Settings in `~/.config/blurt/config.toml`, and where Blurt keeps its state.

use std::fmt::Write as _;
use std::fs;
use std::path::PathBuf;

use anyhow::Result;
use serde::{Deserialize, Serialize};

pub const CLEANUP_MODES: &[&str] = &["openai", "basic", "off"];
pub const REASONING_LEVELS: &[&str] = &["none", "low"];
pub const BUDGET_ACTIONS: &[&str] = &["warn", "block"];
pub const HOLD_KEYS: &[&str] = &["off", "ctrl", "alt", "shift"];

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub transcribe_model: String,
    pub language: String,
    pub vocabulary: Vec<String>,
    pub cleanup: String,
    pub cleanup_reasoning: String,
    pub cleanup_instructions: String,
    pub cleanup_timeout: u64,
    pub trailing_space: bool,
    pub monthly_budget: f64,
    pub budget_action: String,
    pub sounds: bool,
    pub max_seconds: u32,
    pub hold_key: String,
    pub hold_delay: f64,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            transcribe_model: "gpt-transcribe".into(),
            language: "en".into(),
            vocabulary: Vec::new(),
            cleanup: "openai".into(),
            cleanup_reasoning: "none".into(),
            cleanup_instructions: String::new(),
            cleanup_timeout: 25,
            trailing_space: true,
            monthly_budget: 10.0,
            budget_action: "warn".into(),
            sounds: true,
            max_seconds: 300,
            hold_key: "ctrl".into(),
            hold_delay: 1.0,
        }
    }
}

fn xdg_dir(variable: &str, fallback: &str) -> PathBuf {
    std::env::var_os(variable)
        .filter(|value| !value.is_empty())
        .map_or_else(|| home().join(fallback), PathBuf::from)
        .join("blurt")
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map_or_else(|| PathBuf::from("/"), PathBuf::from)
}

pub fn config_dir() -> PathBuf {
    xdg_dir("XDG_CONFIG_HOME", ".config")
}

pub fn state_dir() -> PathBuf {
    xdg_dir("XDG_STATE_HOME", ".local/state")
}

pub fn config_path() -> PathBuf {
    config_dir().join("config.toml")
}

impl Config {
    /// Reads the config, writing the defaults first if there is none. Values out
    /// of range fall back to their defaults.
    pub fn load() -> Result<Self> {
        let path = config_path();
        if !path.exists() {
            Self::default().save()?;
        }
        Ok(Self::parse(&fs::read_to_string(path)?))
    }

    pub fn parse(text: &str) -> Self {
        // Lenient: a field of the wrong type falls back to its default instead of failing the whole file.
        let raw: toml::Table = text.parse().unwrap_or_default();
        let defaults = Self::default();
        let mut table = toml::Table::try_from(&defaults).expect("config serializes");
        for (key, value) in raw {
            if let Some(slot) = table.get_mut(&key) {
                let value = match (&*slot, value) {
                    (toml::Value::Float(_), toml::Value::Integer(n)) => toml::Value::Float(n as f64),
                    (_, value) => value,
                };
                if std::mem::discriminant(slot) == std::mem::discriminant(&value) {
                    *slot = value;
                }
            }
        }
        let mut config: Self = table.try_into().unwrap_or(defaults);
        let fix = |value: &mut String, allowed: &[&str], default: &str| {
            if !allowed.contains(&value.as_str()) {
                *value = default.into();
            }
        };
        let defaults = Self::default();
        fix(&mut config.cleanup, CLEANUP_MODES, &defaults.cleanup);
        fix(&mut config.cleanup_reasoning, REASONING_LEVELS, &defaults.cleanup_reasoning);
        fix(&mut config.budget_action, BUDGET_ACTIONS, &defaults.budget_action);
        fix(&mut config.hold_key, HOLD_KEYS, &defaults.hold_key);
        config
    }

    pub fn to_toml(&self) -> String {
        let table = toml::Table::try_from(self).expect("config serializes");
        let mut text = String::from("# Managed by `blurt settings`; edits here are picked up on restart.\n");
        // Keep the field order of the struct, which toml::Table (sorted) loses.
        for key in Self::FIELDS {
            if let Some(value) = table.get(*key) {
                let _ = writeln!(text, "{key} = {value}");
            }
        }
        text
    }

    const FIELDS: &[&str] = &[
        "transcribe_model",
        "language",
        "vocabulary",
        "cleanup",
        "cleanup_reasoning",
        "cleanup_instructions",
        "cleanup_timeout",
        "trailing_space",
        "monthly_budget",
        "budget_action",
        "sounds",
        "max_seconds",
        "hold_key",
        "hold_delay",
    ];

    pub fn save(&self) -> Result<()> {
        let dir = config_dir();
        fs::create_dir_all(&dir)?;
        let tmp = dir.join("config.tmp");
        fs::write(&tmp, self.to_toml())?;
        fs::rename(tmp, config_path())?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips() {
        let config = Config { vocabulary: vec!["Blurt".into(), "say \"hi\"".into()], ..Config::default() };
        assert_eq!(Config::parse(&config.to_toml()), config);
    }

    #[test]
    fn falls_back_on_bad_values() {
        let config = Config::parse("cleanup = \"magic\"\nhold_delay = 2\nsounds = \"yes\"\nlanguage = \"de\"\n");
        assert_eq!(config.cleanup, "openai");
        assert!((config.hold_delay - 2.0).abs() < f64::EPSILON);
        assert!(config.sounds);
        assert_eq!(config.language, "de");
    }

    #[test]
    fn reads_the_python_config() {
        let text = "# Managed by `blurt settings`; edits here are picked up on restart.\n\
            transcribe_model = \"gpt-transcribe\"\nlanguage = \"en\"\nvocabulary = []\ncleanup = \"openai\"\n\
            cleanup_reasoning = \"none\"\ncleanup_instructions = \"\"\ncleanup_timeout = 25\ntrailing_space = true\n\
            monthly_budget = 10.0\nbudget_action = \"warn\"\nsounds = true\nmax_seconds = 300\nhold_key = \"alt\"\n\
            hold_delay = 0.3\n";
        let config = Config::parse(text);
        assert_eq!(config.hold_key, "alt");
        assert!((config.hold_delay - 0.3).abs() < f64::EPSILON);
    }
}
