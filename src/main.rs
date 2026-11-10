//! Blurt: hold a key, say it, and Blurt types clean, filler-free text into
//! whatever app has focus on a GNOME desktop.

mod account;
mod core;
mod credentials;
mod daemon;
mod ui;

use std::process::ExitCode;
use std::time::Instant;

use anyhow::{Context, Result, bail};
use gtk::gio;
use gtk::glib;

use crate::core::cleanup::clean;
use crate::core::config::{CLEANUP_MODES, Config, config_path};
use crate::core::{history, openai};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

const USAGE: &str = "\
Usage: blurt <command>

Talk anywhere you can type.

Commands:
  settings                 open the settings and usage window
  history [-n N] [--window]
                           show recent dictations, or open the history window
  setup [--shortcut KEYS] [--cancel-shortcut KEYS]
                           set up shortcuts, service and top bar for this account
  teardown                 switch Blurt off for this account
  toggle | start | stop | cancel
                           control dictation
  status                   show the service's state
  transcribe FILE [--cleanup openai|basic|off]
                           transcribe and clean an audio file without typing it
  daemon                   run the background service

Options:
  -h, --help               show this help
  -V, --version            show the version";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(code) => code,
        Err(err) => {
            eprintln!("blurt: {err:#}");
            ExitCode::FAILURE
        }
    }
}

/// The value after `--name`, also accepted as `--name=value`.
fn option<'a>(args: &'a [String], names: &[&str]) -> Result<Option<&'a str>> {
    for (i, arg) in args.iter().enumerate() {
        for name in names {
            if arg == name {
                return args.get(i + 1).map(|v| Some(v.as_str())).with_context(|| format!("{name} needs a value"));
            }
            if let Some(value) = arg.strip_prefix(&format!("{name}=")) {
                return Ok(Some(value));
            }
        }
    }
    Ok(None)
}

fn run(args: &[String]) -> Result<ExitCode> {
    let Some(command) = args.first() else {
        eprintln!("{USAGE}");
        return Ok(ExitCode::from(2));
    };
    let rest = &args[1..];
    match command.as_str() {
        "-h" | "--help" | "help" => println!("{USAGE}"),
        "-V" | "--version" => println!("blurt {VERSION}"),
        "settings" => return Ok(ui::run(ui::Start::Settings)),
        "history" if rest.iter().any(|a| a == "--window") => return Ok(ui::run(ui::Start::History)),
        "history" => {
            let n: usize = option(rest, &["-n"])?.map_or(Ok(10), str::parse).context("-n needs a number")?;
            let entries = history::read(0).entries;
            for entry in &entries[entries.len().saturating_sub(n)..] {
                println!("[{}] {}", entry.at.format("%Y-%m-%dT%H:%M:%S"), entry.text);
            }
        }
        "setup" => setup(rest)?,
        "teardown" => {
            account::tear_down();
            println!("Blurt is switched off for this account. Config, API key and usage history were kept.");
        }
        "daemon" => {
            println!("Blurt {VERSION}");
            daemon::run()?;
        }
        "status" => {
            let reply = call("Status")?;
            println!("{}", reply.child_value(0).get::<String>().unwrap_or_else(|| "unknown".into()));
        }
        "toggle" | "start" | "stop" | "cancel" => {
            let mut method = command.clone();
            method[..1].make_ascii_uppercase();
            call(&method)?;
        }
        "transcribe" => transcribe(rest)?,
        other => {
            eprintln!("blurt: unknown command {other}\n\n{USAGE}");
            return Ok(ExitCode::from(2));
        }
    }
    Ok(ExitCode::SUCCESS)
}

fn call(method: &str) -> Result<glib::Variant> {
    let bus = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE)?;
    bus.call_sync(
        Some(daemon::BUS_NAME),
        daemon::OBJECT_PATH,
        daemon::INTERFACE,
        method,
        None,
        None,
        gio::DBusCallFlags::NONE,
        5000,
        gio::Cancellable::NONE,
    )
    .map_err(|err| {
        let message = err.message();
        if message.contains("ServiceUnknown") || message.contains("not provided") {
            anyhow::anyhow!("Blurt is not running. Run `blurt setup` or start it from the settings window.")
        } else {
            anyhow::anyhow!("{message}")
        }
    })
}

fn setup(args: &[String]) -> Result<()> {
    Config::load()?;
    let status = account::set_up(&[
        ("toggle", option(args, &["--shortcut"])?),
        ("cancel", option(args, &["--cancel-shortcut"])?),
    ]);
    println!("Config: {}", config_path().display());
    println!("Shortcuts: {} start/stop, {} cancel", status.shortcut("toggle"), status.shortcut("cancel"));
    println!("Service: {}", if status.service_active { "running" } else { "not running" });
    if !status.extension_enabled {
        println!("Top bar extension could not be enabled: gnome-extensions enable {}", account::EXTENSION_UUID);
    } else if !status.extension_loaded {
        println!("Top bar extension enabled. Log out and back in once so GNOME Shell loads it.");
    }
    println!("Next: open Blurt from the app grid and add your OpenAI key.");
    Ok(())
}

fn transcribe(args: &[String]) -> Result<()> {
    let mut config = Config::load()?;
    if let Some(mode) = option(args, &["--cleanup"])? {
        if !CLEANUP_MODES.contains(&mode) {
            bail!("--cleanup must be one of {}", CLEANUP_MODES.join(", "));
        }
        mode.clone_into(&mut config.cleanup);
    }
    let file = args
        .iter()
        .enumerate()
        .find(|(i, a)| !a.starts_with("--") && (*i == 0 || args[i - 1] != "--cleanup"))
        .map(|(_, a)| a)
        .context("Usage: blurt transcribe FILE [--cleanup openai|basic|off]")?;
    let key = credentials::api_key().context("No OpenAI API key saved. Add one with `blurt settings`.")?;
    let path = std::path::Path::new(file);
    let audio = std::fs::read(path).with_context(|| format!("Could not read {file}"))?;
    let name = path.file_name().map_or("audio.wav".into(), |n| n.to_string_lossy().into_owned());
    let started = Instant::now();
    let raw = openai::transcribe(&key, &config, &audio, &name)?;
    eprintln!("transcribe {:.2}s ({})", started.elapsed().as_secs_f64(), config.transcribe_model);
    let started = Instant::now();
    let cleaned = clean(&raw, &config, Some(&key));
    eprintln!("cleanup    {:.2}s ({})", started.elapsed().as_secs_f64(), config.cleanup);
    if let Some(warning) = cleaned.warning {
        eprintln!("{warning}");
    }
    println!("raw:   {raw}\nclean: {}", cleaned.text);
    Ok(())
}
