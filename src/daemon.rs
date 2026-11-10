//! The background service: owns `dev.blurt.Daemon` on the session bus, records
//! while asked to, then transcribes, cleans up and types the text through the
//! top bar extension.

use std::cell::RefCell;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::rc::Rc;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use gtk::gio;
use gtk::glib;
use gtk::prelude::*;

use crate::core::cleanup::clean;
use crate::core::config::{Config, state_dir};
use crate::core::recorder::{self, Recording};
use crate::core::{history, openai, pricing, usage};
use crate::credentials;

pub const BUS_NAME: &str = "dev.blurt.Daemon";
pub const OBJECT_PATH: &str = "/dev/blurt/Daemon";
pub const INTERFACE: &str = "dev.blurt.Daemon";
const SHELL_BUS_NAME: &str = "dev.blurt.Shell";
const SHELL_OBJECT_PATH: &str = "/dev/blurt/Shell";
const SHELL_INTERFACE: &str = "dev.blurt.Shell";
const KEEP_FAILED: usize = 20;
const MIN_SECONDS: f64 = 0.3;

const INTROSPECTION: &str = r#"
<node>
  <interface name="dev.blurt.Daemon">
    <method name="Toggle"/>
    <method name="Start"/>
    <method name="HoldStart"/>
    <method name="Stop"/>
    <method name="Cancel"/>
    <method name="Reload"/>
    <method name="Quit"/>
    <method name="Status"><arg type="s" direction="out"/></method>
    <method name="HoldSettings">
      <arg type="s" name="key" direction="out"/>
      <arg type="d" name="delay" direction="out"/>
    </method>
    <method name="Message"><arg type="s" direction="out"/></method>
    <signal name="StateChanged"><arg type="s" name="state"/></signal>
    <signal name="MessageChanged"><arg type="s" name="message"/></signal>
    <signal name="HoldSettingsChanged">
      <arg type="s" name="key"/>
      <arg type="d" name="delay"/>
    </signal>
  </interface>
</node>
"#;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    Idle,
    Recording,
    Processing,
}

impl State {
    fn name(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Recording => "recording",
            Self::Processing => "processing",
        }
    }
}

/// What a dictation needs once recording stops.
struct Plan {
    key: String,
    notes: Vec<String>,
}

/// What the processing thread reports back to the main loop.
enum Event {
    Done(Option<String>),
    Empty,
    Failed(String, String),
    Idle,
}

struct Daemon {
    config: Config,
    state: State,
    message: String,
    bus: gio::DBusConnection,
    recording: Option<Recording>,
    plan: Option<Plan>,
    timeout: Option<glib::SourceId>,
    cancelled: Arc<AtomicBool>,
    events: async_channel::Sender<Event>,
    main_loop: glib::MainLoop,
}

fn sound(config: &Config, name: &str) {
    if !config.sounds {
        return;
    }
    let id = match name {
        "start" => "device-added",
        "stop" => "device-removed",
        _ => "dialog-error",
    };
    let child = Command::new("canberra-gtk-play").args(["-i", id]).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
    // Reap it in the background so no zombie stays behind.
    if let Ok(mut child) = child {
        std::thread::spawn(move || child.wait());
    }
}

impl Daemon {
    fn emit(&self, signal: &str, value: &glib::Variant) {
        if let Err(err) = self.bus.emit_signal(None, OBJECT_PATH, INTERFACE, signal, Some(value)) {
            eprintln!("Could not emit {signal}: {err}");
        }
    }

    fn hold_settings(&self) -> glib::Variant {
        (self.config.hold_key.as_str(), self.config.hold_delay).to_variant()
    }

    fn set_state(&mut self, state: State) {
        if state != self.state {
            self.state = state;
            self.emit("StateChanged", &(state.name(),).to_variant());
        }
    }

    fn set_message(&mut self, message: &str) {
        if message != self.message {
            message.clone_into(&mut self.message);
            self.emit("MessageChanged", &(message,).to_variant());
        }
    }

    fn fail(&mut self, summary: &str, body: &str) {
        println!("{summary}: {body}");
        sound(&self.config, "error");
        self.set_message(&format!("{summary}: {body}"));
        if self.state != State::Processing {
            self.set_state(State::Idle);
        }
    }

    fn reload(&mut self) {
        match Config::load() {
            Ok(config) => self.config = config,
            Err(err) => eprintln!("Could not reload the config: {err:#}"),
        }
        self.emit("HoldSettingsChanged", &self.hold_settings());
    }

    fn plan(&self) -> Result<Plan> {
        let key = credentials::api_key().context("No OpenAI API key. Add one in Blurt settings.")?;
        let mut notes = Vec::new();
        let budget = self.config.monthly_budget;
        if budget > 0.0 && usage::cost_since(usage::month_start()).unwrap_or(0.0) >= budget {
            anyhow::ensure!(
                self.config.budget_action != "block",
                "The ${budget:.2} monthly budget is used up. Raise it in Blurt settings."
            );
            notes.push(format!("${budget:.2} monthly budget reached"));
        }
        Ok(Plan { key, notes })
    }

    fn clear_timeout(&mut self) {
        if let Some(id) = self.timeout.take() {
            id.remove();
        }
    }
}

type Shared = Rc<RefCell<Daemon>>;

fn start(daemon: &Shared) {
    let mut d = daemon.borrow_mut();
    if d.state != State::Idle {
        return;
    }
    let plan = match d.plan() {
        Ok(plan) => plan,
        Err(err) => return d.fail("Can't start dictation", &format!("{err:#}")),
    };
    let recording = match Recording::start(d.config.max_seconds) {
        Ok(recording) => recording,
        Err(err) => return d.fail("Microphone unavailable", &format!("{err:#}")),
    };
    let message = plan.notes.join(" · ");
    d.recording = Some(recording);
    d.plan = Some(plan);
    d.cancelled.store(false, Ordering::SeqCst);
    d.set_state(State::Recording);
    sound(&d.config, "start");
    d.set_message(&message);
    let weak = Rc::downgrade(daemon);
    d.timeout = Some(glib::timeout_add_seconds_local_once(d.config.max_seconds, move || {
        if let Some(daemon) = weak.upgrade() {
            daemon.borrow_mut().timeout = None;
            stop(&daemon);
        }
    }));
}

fn stop(daemon: &Shared) {
    let mut d = daemon.borrow_mut();
    if d.state != State::Recording {
        return;
    }
    d.clear_timeout();
    let (Some(recording), Some(plan)) = (d.recording.take(), d.plan.take()) else { return };
    let audio = recording.stop();
    sound(&d.config, "stop");
    d.set_state(State::Processing);
    let job =
        Job { config: d.config.clone(), bus: d.bus.clone(), cancelled: d.cancelled.clone(), events: d.events.clone() };
    std::thread::spawn(move || job.run(&audio, plan));
}

fn cancel(daemon: &Shared) {
    let mut d = daemon.borrow_mut();
    match d.state {
        State::Recording => {
            d.clear_timeout();
            if let Some(recording) = d.recording.take() {
                recording.stop();
            }
            d.plan = None;
            d.set_state(State::Idle);
        }
        State::Processing => d.cancelled.store(true, Ordering::SeqCst),
        State::Idle => {}
    }
}

fn toggle(daemon: &Shared) {
    let state = daemon.borrow().state;
    match state {
        State::Recording => stop(daemon),
        State::Idle => start(daemon),
        State::Processing => {}
    }
}

fn quit(daemon: &Shared) {
    if daemon.borrow().state == State::Recording {
        cancel(daemon);
    }
    daemon.borrow().main_loop.quit();
}

fn handle(daemon: &Shared, event: Event) {
    let mut d = daemon.borrow_mut();
    match event {
        Event::Done(warning) => d.set_message(warning.as_deref().unwrap_or("")),
        Event::Empty => d.set_message("No speech detected"),
        Event::Failed(summary, body) => d.fail(&summary, &body),
        Event::Idle => d.set_state(State::Idle),
    }
}

/// Transcribes, cleans up and types one recording, off the main loop.
struct Job {
    config: Config,
    bus: gio::DBusConnection,
    cancelled: Arc<AtomicBool>,
    events: async_channel::Sender<Event>,
}

impl Job {
    fn send(&self, event: Event) {
        let _ = self.events.send_blocking(event);
    }

    fn run(&self, audio: &[i16], plan: Plan) {
        if let Some(event) = self.process(audio, plan) {
            self.send(event);
        }
        self.send(Event::Idle);
    }

    fn process(&self, audio: &[i16], mut plan: Plan) -> Option<Event> {
        let mut timings = Vec::new();
        let duration = recorder::seconds(audio);
        if duration < MIN_SECONDS {
            return Some(Event::Empty);
        }
        let wav = recorder::wav(audio);
        let started = Instant::now();
        let raw = match openai::transcribe(&plan.key, &self.config, &wav, "dictation.wav") {
            Ok(raw) => raw,
            Err(err) => {
                let saved = save_failed(&wav).map_or_else(|e| format!("not saved: {e:#}"), |p| p.display().to_string());
                return Some(Event::Failed(
                    "Transcription failed".into(),
                    format!("{err:#}\nRecording saved to {saved}"),
                ));
            }
        };
        let model = &self.config.transcribe_model;
        let _ = usage::record(&usage::Entry {
            kind: "transcribe",
            model,
            audio_seconds: duration,
            cost: pricing::audio_cost(model, duration),
            ..usage::Entry::default()
        });
        timings.push(("transcribe", started.elapsed().as_secs_f64()));
        if raw.is_empty() {
            return Some(Event::Empty);
        }
        let started = Instant::now();
        let cleaned = clean(&raw, &self.config, Some(&plan.key));
        timings.push(("cleanup", started.elapsed().as_secs_f64()));
        plan.notes.extend(cleaned.warning);
        let mut text = cleaned.text;
        if self.config.trailing_space && !text.is_empty() && !text.ends_with([' ', '\n']) {
            text.push(' ');
        }
        if self.cancelled.load(Ordering::SeqCst) {
            let _ = history::record(&raw, &text, None, &timings);
            return None;
        }
        let started = Instant::now();
        match self.insert(&text) {
            Ok(mode) => {
                timings.push(("inject", started.elapsed().as_secs_f64()));
                let _ = history::record(&raw, &text, Some(&mode), &timings);
                Some(Event::Done(Some(plan.notes.join(" · ")).filter(|n| !n.is_empty())))
            }
            Err(err) => {
                let _ = history::record(&raw, &text, None, &timings);
                copy(&text);
                Some(Event::Failed("Copied to clipboard instead of typing".into(), format!("{err:#}")))
            }
        }
    }

    /// Hands the text to the top bar extension, which types it; returns how.
    fn insert(&self, text: &str) -> Result<String> {
        let reply = self
            .bus
            .call_sync(
                Some(SHELL_BUS_NAME),
                SHELL_OBJECT_PATH,
                SHELL_INTERFACE,
                "Insert",
                Some(&(single_line(text),).to_variant()),
                Some(glib::VariantTy::new("(s)").unwrap()),
                gio::DBusCallFlags::NONE,
                5000,
                gio::Cancellable::NONE,
            )
            .map_err(|err| {
                let message = err.message();
                if message.contains("ServiceUnknown") || message.contains("not provided") {
                    anyhow::anyhow!("The Blurt top bar extension isn't running. Log out and back in once.")
                } else {
                    anyhow::anyhow!("{message}")
                }
            })?;
        Ok(reply.child_value(0).get::<String>().unwrap_or_default())
    }
}

/// Line breaks become spaces, so a dictated sentence can never run a command in a terminal.
pub fn single_line(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut pending_break = false;
    for c in text.chars() {
        if c == '\r' || c == '\n' {
            pending_break = true;
            continue;
        }
        if pending_break {
            if c.is_whitespace() {
                continue;
            }
            let trimmed = out.trim_end().len();
            out.truncate(trimmed);
            out.push(' ');
            pending_break = false;
        }
        out.push(c);
    }
    if pending_break {
        let trimmed = out.trim_end().len();
        out.truncate(trimmed);
        out.push(' ');
    }
    out
}

fn copy(text: &str) {
    let _ = Command::new("wl-copy").arg("--").arg(text).stdout(Stdio::null()).stderr(Stdio::null()).status();
}

fn save_failed(wav: &[u8]) -> Result<PathBuf> {
    let dir = state_dir().join("failed");
    std::fs::create_dir_all(&dir)?;
    let target = dir.join(format!("{}.wav", chrono::Local::now().format("%Y%m%d-%H%M%S")));
    std::fs::write(&target, wav)?;
    let mut old: Vec<PathBuf> = std::fs::read_dir(&dir)?
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|path| path.extension().is_some_and(|ext| ext == "wav"))
        .collect();
    old.sort();
    let excess = old.len().saturating_sub(KEEP_FAILED);
    for path in &old[..excess] {
        let _ = std::fs::remove_file(path);
    }
    Ok(target)
}

pub fn run() -> Result<()> {
    let config = Config::load()?;
    let bus = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE)?;
    let main_loop = glib::MainLoop::new(None, false);
    let (events, receiver) = async_channel::unbounded();
    let daemon: Shared = Rc::new(RefCell::new(Daemon {
        config,
        state: State::Idle,
        message: String::new(),
        bus: bus.clone(),
        recording: None,
        plan: None,
        timeout: None,
        cancelled: Arc::new(AtomicBool::new(false)),
        events,
        main_loop: main_loop.clone(),
    }));

    let weak = Rc::downgrade(&daemon);
    glib::spawn_future_local(async move {
        while let Ok(event) = receiver.recv().await {
            if let Some(daemon) = weak.upgrade() {
                handle(&daemon, event);
            }
        }
    });

    let node = gio::DBusNodeInfo::for_xml(INTROSPECTION)?;
    let interface = node.lookup_interface(INTERFACE).context("interface missing")?;
    let shared = daemon.clone();
    let _registration = bus
        .register_object(OBJECT_PATH, &interface)
        .method_call(move |_conn, _sender, _path, _interface, method, _params, invocation| {
            let reply = match method {
                "Toggle" => {
                    toggle(&shared);
                    None
                }
                "Start" | "HoldStart" => {
                    start(&shared);
                    None
                }
                "Stop" => {
                    stop(&shared);
                    None
                }
                "Cancel" => {
                    cancel(&shared);
                    None
                }
                "Reload" => {
                    shared.borrow_mut().reload();
                    None
                }
                "Quit" => {
                    let daemon = shared.clone();
                    glib::idle_add_local_once(move || quit(&daemon));
                    None
                }
                "Status" => Some((shared.borrow().state.name(),).to_variant()),
                "HoldSettings" => Some(shared.borrow().hold_settings()),
                "Message" => Some((shared.borrow().message.as_str(),).to_variant()),
                _ => {
                    invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", "Unknown method");
                    return;
                }
            };
            invocation.return_value(reply.as_ref());
        })
        .build()?;

    let lost = main_loop.clone();
    let _owner = gio::bus_own_name_on_connection(
        &bus,
        BUS_NAME,
        gio::BusNameOwnerFlags::NONE,
        |_, _| {},
        move |_, _| {
            println!("Could not own {BUS_NAME}; is another daemon running?");
            lost.quit();
        },
    );
    main_loop.run();
    // Let the last replies and signals go out before exiting.
    let _ = bus.flush_sync(gio::Cancellable::NONE);
    std::thread::sleep(Duration::from_millis(50));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn joins_lines() {
        assert_eq!(single_line("one\ntwo"), "one two");
        assert_eq!(single_line("one  \r\n\n  two\n"), "one two ");
        assert_eq!(single_line("no breaks"), "no breaks");
    }
}
