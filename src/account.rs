//! Switches Blurt on and off for the current account: the user service, the
//! top bar extension and the start/stop and cancel shortcuts.

use std::process::{Command, Output};

use crate::daemon::{BUS_NAME, INTERFACE, OBJECT_PATH};

pub const SERVICE: &str = "blurt.service";
pub const EXTENSION_UUID: &str = "blurt@dev.blurt";
const SHELL_SCHEMA: &str = "org.gnome.shell";
const KEYBINDINGS_SCHEMA: &str = "org.gnome.settings-daemon.plugins.media-keys";
const KEYBINDING_SCHEMA: &str = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding";
const KEYBINDING_ROOT: &str = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings";

/// The shortcuts: their key, name, the daemon method they call and default binding.
pub const SHORTCUTS: &[(&str, &str, &str, &str)] =
    &[("toggle", "Blurt: start/stop", "Toggle", "<Super>d"), ("cancel", "Blurt: cancel", "Cancel", "<Super><Shift>d")];

#[derive(Debug, Clone)]
pub struct Status {
    pub service_enabled: bool,
    pub service_active: bool,
    pub extension_enabled: bool,
    pub extension_loaded: bool,
    /// Each shortcut's binding, empty when it isn't set.
    pub shortcuts: Vec<(String, String)>,
}

impl Status {
    pub fn complete(&self) -> bool {
        self.service_enabled && self.extension_enabled && self.shortcuts.iter().all(|(_, binding)| !binding.is_empty())
    }

    pub fn shortcut(&self, name: &str) -> &str {
        self.shortcuts.iter().find(|(n, _)| n == name).map_or("", |(_, binding)| binding)
    }
}

fn run(program: &str, args: &[&str]) -> Option<Output> {
    Command::new(program).args(args).output().ok()
}

fn stdout(program: &str, args: &[&str]) -> String {
    run(program, args).map(|out| String::from_utf8_lossy(&out.stdout).trim().to_owned()).unwrap_or_default()
}

fn gsettings_get(schema: &str, key: &str) -> String {
    stdout("gsettings", &["get", schema, key])
}

fn gsettings_set(schema: &str, key: &str, value: &str) {
    run("gsettings", &["set", schema, key, value]);
}

/// Parses a GVariant string array as gsettings prints it: `['a', 'b']` or `@as []`.
pub fn parse_strv(raw: &str) -> Vec<String> {
    let raw = raw.trim().trim_start_matches("@as").trim();
    let inner = raw.trim_start_matches('[').trim_end_matches(']');
    let mut items = Vec::new();
    let mut chars = inner.chars();
    while let Some(c) = chars.next() {
        if c != '\'' && c != '"' {
            continue;
        }
        let mut item = String::new();
        while let Some(d) = chars.next() {
            match d {
                '\\' => item.extend(chars.next()),
                d if d == c => break,
                d => item.push(d),
            }
        }
        items.push(item);
    }
    items
}

/// Formats a string array as a GVariant for gsettings.
pub fn format_strv(items: &[String]) -> String {
    let quoted: Vec<String> =
        items.iter().map(|item| format!("'{}'", item.replace('\\', "\\\\").replace('\'', "\\'"))).collect();
    format!("[{}]", quoted.join(", "))
}

fn keybinding_paths() -> Vec<String> {
    parse_strv(&gsettings_get(KEYBINDINGS_SCHEMA, "custom-keybindings"))
}

fn keybinding_path(name: &str) -> String {
    format!("{KEYBINDING_ROOT}/blurt-{name}/")
}

/// The binding of a shortcut, empty when it isn't set.
pub fn shortcut(name: &str) -> String {
    let path = keybinding_path(name);
    if !keybinding_paths().contains(&path) {
        return String::new();
    }
    gsettings_get(&format!("{KEYBINDING_SCHEMA}:{path}"), "binding").trim_matches('\'').to_owned()
}

fn set_shortcut(name: &str, title: &str, method: &str, binding: &str) {
    let path = keybinding_path(name);
    let schema = format!("{KEYBINDING_SCHEMA}:{path}");
    let command =
        format!("gdbus call --session --dest {BUS_NAME} --object-path {OBJECT_PATH} --method {INTERFACE}.{method}");
    gsettings_set(&schema, "name", &format!("'{title}'"));
    gsettings_set(&schema, "command", &format!("'{command}'"));
    gsettings_set(&schema, "binding", &format!("'{binding}'"));
    let mut paths = keybinding_paths();
    if !paths.contains(&path) {
        paths.push(path);
        gsettings_set(KEYBINDINGS_SCHEMA, "custom-keybindings", &format_strv(&paths));
    }
}

fn remove_shortcuts() {
    let ours: Vec<String> = SHORTCUTS.iter().map(|(name, ..)| keybinding_path(name)).collect();
    for path in &ours {
        run("gsettings", &["reset-recursively", &format!("{KEYBINDING_SCHEMA}:{path}")]);
    }
    let rest: Vec<String> = keybinding_paths().into_iter().filter(|p| !ours.contains(p)).collect();
    gsettings_set(KEYBINDINGS_SCHEMA, "custom-keybindings", &format_strv(&rest));
}

fn shell_list(key: &str) -> Vec<String> {
    parse_strv(&gsettings_get(SHELL_SCHEMA, key))
}

fn extension_state() -> (bool, bool) {
    let enabled = shell_list("enabled-extensions").iter().any(|u| u == EXTENSION_UUID)
        && !shell_list("disabled-extensions").iter().any(|u| u == EXTENSION_UUID)
        && gsettings_get(SHELL_SCHEMA, "disable-user-extensions") == "false";
    let info = stdout("gnome-extensions", &["info", EXTENSION_UUID]);
    let loaded = info.contains("State: ACTIVE") || info.contains("State: ENABLED");
    (enabled, loaded)
}

fn enable_extension() {
    gsettings_set(SHELL_SCHEMA, "disable-user-extensions", "false");
    if run("gnome-extensions", &["enable", EXTENSION_UUID]).is_some_and(|out| out.status.success()) {
        return;
    }
    // GNOME Shell doesn't know about the extension until the next login: enable it by hand.
    let mut enabled = shell_list("enabled-extensions");
    if !enabled.iter().any(|u| u == EXTENSION_UUID) {
        enabled.push(EXTENSION_UUID.into());
        gsettings_set(SHELL_SCHEMA, "enabled-extensions", &format_strv(&enabled));
    }
    let disabled = shell_list("disabled-extensions");
    if disabled.iter().any(|u| u == EXTENSION_UUID) {
        let rest: Vec<String> = disabled.into_iter().filter(|u| u != EXTENSION_UUID).collect();
        gsettings_set(SHELL_SCHEMA, "disabled-extensions", &format_strv(&rest));
    }
}

fn disable_extension() {
    run("gnome-extensions", &["disable", EXTENSION_UUID]);
    let enabled = shell_list("enabled-extensions");
    if enabled.iter().any(|u| u == EXTENSION_UUID) {
        let rest: Vec<String> = enabled.into_iter().filter(|u| u != EXTENSION_UUID).collect();
        gsettings_set(SHELL_SCHEMA, "enabled-extensions", &format_strv(&rest));
    }
}

pub fn status() -> Status {
    let (extension_enabled, extension_loaded) = extension_state();
    Status {
        service_enabled: stdout("systemctl", &["--user", "is-enabled", SERVICE]) == "enabled",
        service_active: stdout("systemctl", &["--user", "is-active", SERVICE]) == "active",
        extension_enabled,
        extension_loaded,
        shortcuts: SHORTCUTS.iter().map(|(name, ..)| ((*name).to_owned(), shortcut(name))).collect(),
    }
}

/// Sets Blurt up, keeping shortcuts already set unless `bindings` names others.
pub fn set_up(bindings: &[(&str, Option<&str>)]) -> Status {
    for (name, title, method, default) in SHORTCUTS {
        let given = bindings.iter().find(|(n, _)| n == name).and_then(|(_, b)| *b).map(str::to_owned);
        let current = shortcut(name);
        let binding = given.unwrap_or(if current.is_empty() { (*default).to_owned() } else { current });
        set_shortcut(name, title, method, &binding);
    }
    run("systemctl", &["--user", "daemon-reload"]);
    run("systemctl", &["--user", "enable", "--now", SERVICE]);
    enable_extension();
    status()
}

pub fn set_service(on: bool) {
    let action = if on { "enable" } else { "disable" };
    run("systemctl", &["--user", action, "--now", SERVICE]);
}

pub fn tear_down() {
    set_service(false);
    disable_extension();
    remove_shortcuts();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_and_formats_string_arrays() {
        assert_eq!(parse_strv("@as []"), Vec::<String>::new());
        assert_eq!(parse_strv("['a@b', 'it\\'s']"), vec!["a@b".to_owned(), "it's".to_owned()]);
        let items = vec!["/x/".to_owned(), "it's".to_owned()];
        assert_eq!(parse_strv(&format_strv(&items)), items);
    }
}
