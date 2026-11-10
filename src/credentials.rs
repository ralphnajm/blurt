//! The OpenAI API key, kept in the GNOME keyring.

use std::collections::HashMap;

use gtk::gio;
use libsecret::{Schema, SchemaAttributeType, SchemaFlags};

fn schema() -> Schema {
    Schema::new("dev.blurt.Blurt", SchemaFlags::NONE, HashMap::from([("service", SchemaAttributeType::String)]))
}

fn attributes() -> HashMap<&'static str, &'static str> {
    HashMap::from([("service", "openai")])
}

/// The saved key, or else `$OPENAI_API_KEY`.
pub fn api_key() -> Option<String> {
    libsecret::password_lookup_sync(Some(&schema()), attributes(), gio::Cancellable::NONE)
        .ok()
        .flatten()
        .map(String::from)
        .or_else(|| std::env::var("OPENAI_API_KEY").ok())
        .filter(|key| !key.is_empty())
}

pub fn set_api_key(key: &str) -> anyhow::Result<()> {
    libsecret::password_store_sync(
        Some(&schema()),
        attributes(),
        Some("default"),
        "Blurt · OpenAI API key",
        key,
        gio::Cancellable::NONE,
    )?;
    Ok(())
}
