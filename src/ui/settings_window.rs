//! The settings window, with a Usage tab of estimated spend.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use adw::prelude::*;
use gtk::{gio, glib};

use super::chart::DailyChart;
use super::format::{minutes, money, rate, tokens};
use super::{APP_ID, REPO_URL};
use crate::account;
use crate::core::config::Config;
use crate::core::pricing::{CLEANUP_MODEL, CLEANUP_PER_MILLION, PRICES_AS_OF, TRANSCRIBE_PER_MINUTE, transcribe_rate};
use crate::core::{openai, update, usage};
use crate::credentials;
use crate::daemon::{BUS_NAME, INTERFACE, OBJECT_PATH};

const KEYS_URL: &str = "https://platform.openai.com/api-keys";
const USAGE_URL: &str = "https://platform.openai.com/usage";
const REFRESH_SECONDS: u32 = 5;

fn shortcut_label(binding: &str) -> String {
    if binding.is_empty() {
        return "not set".into();
    }
    gtk::accelerator_parse(binding)
        .map_or_else(|| binding.to_owned(), |(key, mods)| gtk::accelerator_get_label(key, mods).into())
}

fn shortcuts_summary(status: &account::Status) -> String {
    format!(
        "{} start/stop · {} cancel",
        shortcut_label(status.shortcut("toggle")),
        shortcut_label(status.shortcut("cancel"))
    )
}

/// A caption over a large number, in a card.
fn stat(title: &str) -> (gtk::Box, gtk::Label) {
    let caption = gtk::Label::builder().label(title).xalign(0.0).css_classes(["caption", "dim-label"]).build();
    let value = gtk::Label::builder().label("–").xalign(0.0).css_classes(["title-3", "numeric"]).build();
    let inner = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(4)
        .margin_top(12)
        .margin_bottom(12)
        .margin_start(14)
        .margin_end(14)
        .build();
    inner.append(&caption);
    inner.append(&value);
    let card = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["card"]).hexpand(true).build();
    card.append(&inner);
    (card, value)
}

fn amount(cost: f64) -> gtk::Label {
    gtk::Label::builder().label(money(cost)).css_classes(["numeric"]).build()
}

struct Usage {
    today: gtk::Label,
    month: gtk::Label,
    audio: gtk::Label,
    budget: gtk::Label,
    budget_bar: gtk::LevelBar,
    chart: DailyChart,
    models: adw::PreferencesGroup,
    recent: adw::PreferencesGroup,
    rows: RefCell<Vec<(adw::PreferencesGroup, adw::ActionRow)>>,
}

struct Settings {
    window: adw::ApplicationWindow,
    config: RefCell<Config>,
    save: Cell<Option<glib::SourceId>>,
    toasts: adw::ToastOverlay,
    stack: adw::ViewStack,
    banner: adw::Banner,
    shortcut_row: adw::ActionRow,
    hold_delay_row: RefCell<Option<adw::SpinRow>>,
    reasoning_row: RefCell<Option<adw::ComboRow>>,
    usage: RefCell<Option<Usage>>,
}

impl Settings {
    fn toast(&self, message: &str) {
        self.toasts.add_toast(adw::Toast::builder().title(message).timeout(3).build());
    }

    /// Changes a setting and saves it shortly after, once typing has paused.
    fn set(self: &Rc<Self>, change: impl FnOnce(&mut Config)) {
        change(&mut self.config.borrow_mut());
        self.sync_sensitivity();
        if let Some(id) = self.save.take() {
            id.remove();
        }
        let weak = Rc::downgrade(self);
        self.save.set(Some(glib::timeout_add_local_once(std::time::Duration::from_millis(400), move || {
            if let Some(this) = weak.upgrade() {
                this.save.set(None);
                if let Err(err) = this.config.borrow().save() {
                    this.toast(&format!("Could not save settings: {err:#}"));
                }
                call_daemon("Reload");
            }
        })));
    }

    fn sync_sensitivity(&self) {
        let config = self.config.borrow();
        if let Some(row) = &*self.reasoning_row.borrow() {
            row.set_sensitive(config.cleanup == "openai");
        }
        if let Some(row) = &*self.hold_delay_row.borrow() {
            row.set_sensitive(config.hold_key != "off");
        }
    }

    fn apply_status(&self, status: &account::Status) {
        if !status.complete() {
            self.banner.set_title("Blurt isn't set up for your account yet");
            self.banner.set_button_label(Some("Set up"));
            self.banner.set_revealed(true);
        } else if !status.extension_loaded {
            // GNOME Shell only finds new extensions when you log in.
            self.banner.set_title("Log out and back in once to show Blurt in the top bar");
            self.banner.set_button_label(None);
            self.banner.set_revealed(true);
        } else {
            self.banner.set_revealed(false);
        }
        self.shortcut_row.set_subtitle(&shortcuts_summary(status));
    }

    fn refresh_usage(&self) {
        if self.stack.visible_child_name().as_deref() != Some("usage") {
            return;
        }
        let usage_ref = self.usage.borrow();
        let Some(u) = usage_ref.as_ref() else { return };
        let month = usage::month_start();
        let month_cost = usage::cost_since(month).unwrap_or(0.0);
        u.today.set_label(&money(usage::cost_since(usage::today_start()).unwrap_or(0.0)));
        u.month.set_label(&money(month_cost));
        u.audio.set_label(&minutes(usage::audio_seconds_since(month).unwrap_or(0.0)));
        let budget = self.config.borrow().monthly_budget;
        u.budget_bar.set_visible(budget > 0.0);
        if budget > 0.0 {
            u.budget.set_label(&money((budget - month_cost).max(0.0)));
            u.budget_bar.set_value((month_cost / budget).min(1.0));
        } else {
            u.budget.set_label("No budget");
        }
        u.chart.set_days(usage::daily_costs(30).unwrap_or_default());

        let mut rows = u.rows.borrow_mut();
        for (group, row) in rows.drain(..) {
            group.remove(&row);
        }
        let model_rows: Vec<adw::ActionRow> = usage::by_model(month)
            .unwrap_or_default()
            .into_iter()
            .map(|m| {
                let size = if m.kind == "cleanup" {
                    tokens(m.input_tokens + m.output_tokens)
                } else {
                    minutes(m.audio_seconds)
                };
                let plural = if m.requests == 1 { "request" } else { "requests" };
                let row = adw::ActionRow::builder()
                    .title(&m.model)
                    .subtitle(format!("{} · {} {plural} · {size}", m.kind, m.requests))
                    .build();
                row.add_suffix(&amount(m.cost));
                row
            })
            .collect();
        let recent_rows: Vec<adw::ActionRow> = usage::recent(15)
            .unwrap_or_default()
            .into_iter()
            .map(|r| {
                let size = if r.kind == "cleanup" {
                    tokens(r.input_tokens + r.output_tokens)
                } else {
                    minutes(r.audio_seconds)
                };
                let row = adw::ActionRow::builder()
                    .title(format!("{} · {}", r.kind, r.model))
                    .subtitle(format!("{} · {size}", r.at.format("%b %-d, %H:%M")))
                    .build();
                row.add_suffix(&amount(r.cost));
                row
            })
            .collect();
        for (group, group_rows) in [(&u.models, model_rows), (&u.recent, recent_rows)] {
            let group_rows = if group_rows.is_empty() {
                vec![
                    adw::ActionRow::builder()
                        .title("Nothing yet")
                        .subtitle("Blurt something to see usage here")
                        .build(),
                ]
            } else {
                group_rows
            };
            for row in group_rows {
                group.add(&row);
                rows.push((group.clone(), row));
            }
        }
    }
}

fn call_daemon(method: &'static str) {
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return };
    bus.call(
        Some(BUS_NAME),
        OBJECT_PATH,
        INTERFACE,
        method,
        None,
        None,
        gio::DBusCallFlags::NONE,
        3000,
        gio::Cancellable::NONE,
        |_| {},
    );
}

/// A combo row over `options` (value, label) bound to a string setting. A value
/// not among the options, set by hand in the config, is added to them.
fn combo(
    settings: &Rc<Settings>,
    title: &str,
    options: &[(&str, &str)],
    get: fn(&Config) -> &String,
    set: fn(&mut Config, String),
    describe: Option<fn(&str) -> String>,
) -> adw::ComboRow {
    let current = get(&settings.config.borrow()).clone();
    let mut options: Vec<(String, String)> = options.iter().map(|(v, l)| ((*v).to_owned(), (*l).to_owned())).collect();
    if !options.iter().any(|(v, _)| *v == current) {
        options.push((current.clone(), current.clone()));
    }
    let labels: Vec<&str> = options.iter().map(|(_, l)| l.as_str()).collect();
    let row = adw::ComboRow::builder().title(title).model(&gtk::StringList::new(&labels)).build();
    row.set_selected(options.iter().position(|(v, _)| *v == current).unwrap_or(0) as u32);
    if let Some(describe) = describe {
        row.set_subtitle(&describe(&current));
    }
    let weak = Rc::downgrade(settings);
    row.connect_selected_notify(move |row| {
        let Some(settings) = weak.upgrade() else { return };
        let Some((value, _)) = options.get(row.selected() as usize) else { return };
        if let Some(describe) = describe {
            row.set_subtitle(&describe(value));
        }
        let value = value.clone();
        settings.set(move |c| set(c, value));
    });
    row
}

fn switch(
    settings: &Rc<Settings>,
    title: &str,
    get: fn(&Config) -> bool,
    set: fn(&mut Config, bool),
) -> adw::SwitchRow {
    let row = adw::SwitchRow::builder().title(title).active(get(&settings.config.borrow())).build();
    let weak = Rc::downgrade(settings);
    row.connect_active_notify(move |row| {
        if let Some(settings) = weak.upgrade() {
            let active = row.is_active();
            settings.set(move |c| set(c, active));
        }
    });
    row
}

fn entry(
    settings: &Rc<Settings>,
    title: &str,
    get: fn(&Config) -> &String,
    set: fn(&mut Config, String),
) -> adw::EntryRow {
    let row = adw::EntryRow::builder().title(title).text(get(&settings.config.borrow()).as_str()).build();
    let weak = Rc::downgrade(settings);
    row.connect_changed(move |row| {
        if let Some(settings) = weak.upgrade() {
            let text = row.text().trim().to_owned();
            settings.set(move |c| set(c, text));
        }
    });
    row
}

fn dictation_group(s: &Rc<Settings>) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::builder().title("Dictation").build();
    let change = gtk::Button::builder().label("Change…").valign(gtk::Align::Center).build();
    change.connect_clicked(|_| {
        let _ = std::process::Command::new("gnome-control-center").arg("keyboard").spawn();
    });
    s.shortcut_row.add_suffix(&change);
    group.add(&s.shortcut_row);
    group.add(&combo(
        s,
        "Hold to talk",
        &[("off", "Off"), ("ctrl", "Ctrl"), ("alt", "Alt"), ("shift", "Shift")],
        |c| &c.hold_key,
        |c, v| c.hold_key = v,
        Some(|key| {
            if key == "off" {
                "Only the shortcuts start dictation"
            } else {
                "Hold the key to start, release it to stop"
            }
            .into()
        }),
    ));
    let delay = adw::SpinRow::with_range(0.3, 3.0, 0.1);
    delay.set_title("Hold delay");
    delay.set_subtitle("Seconds to hold before recording starts");
    delay.set_digits(1);
    delay.set_value(s.config.borrow().hold_delay);
    let weak = Rc::downgrade(s);
    delay.connect_value_notify(move |row| {
        if let Some(s) = weak.upgrade() {
            let value = (row.value() * 10.0).round() / 10.0;
            s.set(move |c| c.hold_delay = value);
        }
    });
    group.add(&delay);
    s.hold_delay_row.replace(Some(delay));
    group
}

fn openai_group(s: &Rc<Settings>) -> adw::PreferencesGroup {
    let group =
        adw::PreferencesGroup::builder().title("OpenAI").description("The key is stored in your GNOME keyring").build();
    let key_row = adw::PasswordEntryRow::builder().title("API key").show_apply_button(true).build();
    key_row.set_text(&credentials::api_key().unwrap_or_default());
    let weak = Rc::downgrade(s);
    key_row.connect_apply(move |row| {
        let Some(s) = weak.upgrade() else { return };
        let key = row.text().trim().to_owned();
        if key.is_empty() {
            return;
        }
        match credentials::set_api_key(&key) {
            Ok(()) => s.toast("API key saved to keyring"),
            Err(err) => s.toast(&format!("Could not save the key: {err:#}")),
        }
    });
    group.add(&key_row);

    let test =
        adw::ActionRow::builder().title("Test connection").subtitle("Checks the saved key against the API").build();
    let button = gtk::Button::builder().label("Test").valign(gtk::Align::Center).build();
    let weak = Rc::downgrade(s);
    button.connect_clicked(move |_| {
        let Some(s) = weak.upgrade() else { return };
        let Some(key) = credentials::api_key() else {
            s.toast("Save an API key first");
            return;
        };
        let weak = Rc::downgrade(&s);
        glib::spawn_future_local(async move {
            let result = gio::spawn_blocking(move || openai::check_key(&key).map_err(|e| format!("{e:#}"))).await;
            let Some(s) = weak.upgrade() else { return };
            match result {
                Ok(Ok(())) => s.toast("Key works"),
                Ok(Err(err)) => s.toast(&format!("Key check failed: {err}")),
                Err(_) => s.toast("Key check failed"),
            }
        });
    });
    test.add_suffix(&button);
    group.add(&test);

    let link = adw::ActionRow::builder()
        .title("Create a key")
        .subtitle("platform.openai.com/api-keys")
        .activatable(true)
        .build();
    link.add_suffix(&gtk::Image::from_icon_name("adw-external-link-symbolic"));
    let window = s.window.downgrade();
    link.connect_activated(move |_| {
        gtk::UriLauncher::new(KEYS_URL).launch(window.upgrade().as_ref(), gio::Cancellable::NONE, |_| {});
    });
    group.add(&link);
    group
}

fn speech_group(s: &Rc<Settings>) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::builder().title("Speech recognition").build();
    let models: Vec<(&str, &str)> = TRANSCRIBE_PER_MINUTE.iter().map(|(m, _)| (*m, *m)).collect();
    group.add(&combo(
        s,
        "Transcription model",
        &models,
        |c| &c.transcribe_model,
        |c, v| c.transcribe_model = v,
        Some(|model| transcribe_rate(model).map(|r| format!("{}/min", rate(r))).unwrap_or_default()),
    ));
    let language = entry(s, "Language", |c| &c.language, |c, v| c.language = v);
    language.set_tooltip_text(Some("Language code such as en or de, or auto"));
    group.add(&language);
    let vocabulary = adw::EntryRow::builder()
        .title("Vocabulary (comma-separated)")
        .text(s.config.borrow().vocabulary.join(", "))
        .build();
    let weak = Rc::downgrade(s);
    vocabulary.connect_changed(move |row| {
        if let Some(s) = weak.upgrade() {
            let words: Vec<String> =
                row.text().split(',').map(str::trim).filter(|w| !w.is_empty()).map(str::to_owned).collect();
            s.set(move |c| c.vocabulary = words);
        }
    });
    group.add(&vocabulary);
    group
}

fn cleanup_group(s: &Rc<Settings>) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::builder()
        .title("Cleanup")
        .description("Removes fillers and false starts and fixes grammar")
        .build();
    group.add(&combo(
        s,
        "Cleanup",
        &[("openai", "OpenAI"), ("basic", "Filler removal only"), ("off", "Off")],
        |c| &c.cleanup,
        |c, v| c.cleanup = v,
        Some(|mode| match mode {
            "openai" => {
                let (input, output) = CLEANUP_PER_MILLION;
                format!("{CLEANUP_MODEL} · {} in · {} out per 1M tokens", rate(input), rate(output))
            }
            "basic" => "Runs locally, free".into(),
            _ => "Types the transcript as is".into(),
        }),
    ));
    let reasoning = combo(
        s,
        "Reasoning",
        &[("none", "None"), ("low", "Low")],
        |c| &c.cleanup_reasoning,
        |c, v| c.cleanup_reasoning = v,
        Some(|level| if level == "none" { "Fastest" } else { "Slower, handles messier sentences" }.into()),
    );
    group.add(&reasoning);
    s.reasoning_row.replace(Some(reasoning));
    group.add(&entry(s, "Extra instructions", |c| &c.cleanup_instructions, |c, v| c.cleanup_instructions = v));
    group
}

fn budget_group(s: &Rc<Settings>) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::builder().title("Budget").build();
    let spin = adw::SpinRow::with_range(0.0, 500.0, 1.0);
    spin.set_title("Monthly budget");
    spin.set_subtitle("USD, 0 for no budget");
    spin.set_digits(2);
    spin.set_value(s.config.borrow().monthly_budget);
    let weak = Rc::downgrade(s);
    spin.connect_value_notify(move |row| {
        if let Some(s) = weak.upgrade() {
            let value = row.value();
            s.set(move |c| c.monthly_budget = value);
        }
    });
    group.add(&spin);
    group.add(&combo(
        s,
        "When the budget is reached",
        &[("warn", "Warn me"), ("block", "Stop dictating")],
        |c| &c.budget_action,
        |c, v| c.budget_action = v,
        None,
    ));
    group
}

fn typing_group(s: &Rc<Settings>) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::builder().title("Typing and feedback").build();
    group.add(&switch(s, "Add a trailing space", |c| c.trailing_space, |c, v| c.trailing_space = v));
    group.add(&switch(s, "Sounds", |c| c.sounds, |c, v| c.sounds = v));
    group
}

fn usage_page(s: &Rc<Settings>) -> gtk::Widget {
    let page = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(18)
        .margin_top(24)
        .margin_bottom(24)
        .build();
    let stats = gtk::Box::builder().spacing(12).homogeneous(true).build();
    let (today_card, today) = stat("Today");
    let (month_card, month) = stat("This month");
    let (audio_card, audio) = stat("Audio this month");
    let (budget_card, budget) = stat("Budget left");
    for card in [&today_card, &month_card, &audio_card, &budget_card] {
        stats.append(card);
    }
    page.append(&stats);
    let budget_bar = gtk::LevelBar::builder().min_value(0.0).max_value(1.0).build();
    page.append(&budget_bar);

    let chart_card =
        gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).css_classes(["card"]).build();
    let heading = gtk::Label::builder()
        .label("Daily spend, last 30 days")
        .xalign(0.0)
        .css_classes(["heading"])
        .margin_top(14)
        .margin_start(14)
        .build();
    let chart = DailyChart::new();
    chart.widget().set_margin_start(6);
    chart.widget().set_margin_end(10);
    chart.widget().set_margin_bottom(8);
    chart_card.append(&heading);
    chart_card.append(chart.widget());
    page.append(&chart_card);

    let models = adw::PreferencesGroup::builder().title("This month by model").build();
    let recent = adw::PreferencesGroup::builder().title("Recent requests").build();
    page.append(&models);
    page.append(&recent);
    let note = gtk::Label::builder()
        .label(format!(
            "Estimated from audio length and token counts at OpenAI list prices ({PRICES_AS_OF}). \
             Your OpenAI invoice is the source of truth."
        ))
        .wrap(true)
        .xalign(0.0)
        .css_classes(["caption", "dim-label"])
        .build();
    page.append(&note);
    page.append(
        &gtk::LinkButton::builder()
            .uri(USAGE_URL)
            .label("Open the OpenAI usage dashboard")
            .halign(gtk::Align::Start)
            .build(),
    );
    s.usage.replace(Some(Usage {
        today,
        month,
        audio,
        budget,
        budget_bar,
        chart,
        models,
        recent,
        rows: RefCell::default(),
    }));
    let clamp = adw::Clamp::builder().maximum_size(720).child(&page).margin_start(12).margin_end(12).build();
    gtk::ScrolledWindow::builder().child(&clamp).hscrollbar_policy(gtk::PolicyType::Never).build().upcast()
}

/// Asks GitHub for a newer release and offers to install it. `action` stays
/// disabled while checking or updating.
fn check_for_updates(s: &Rc<Settings>, action: &gio::SimpleAction) {
    action.set_enabled(false);
    let (weak, action) = (Rc::downgrade(s), action.clone());
    glib::spawn_future_local(async move {
        let result = gio::spawn_blocking(|| update::check(crate::VERSION).map_err(|e| format!("{e:#}"))).await;
        let Some(s) = weak.upgrade() else { return };
        let version = match result {
            Ok(Ok(Some(version))) => version,
            Ok(Ok(None)) => {
                action.set_enabled(true);
                return s.toast("Blurt is up to date");
            }
            Ok(Err(err)) => {
                action.set_enabled(true);
                return s.toast(&format!("Could not check for updates: {err}"));
            }
            Err(_) => {
                action.set_enabled(true);
                return s.toast("Could not check for updates");
            }
        };
        let dialog = adw::AlertDialog::builder()
            .heading(format!("Blurt {version} is available"))
            .body(format!(
                "You have version {}. Updating installs the new version the way Blurt was installed, which may ask \
                 for your password, and restarts Blurt. See what changed on GitHub.",
                crate::VERSION
            ))
            .build();
        dialog.add_responses(&[("later", "Not Now"), ("notes", "What's New"), ("update", "Update")]);
        dialog.set_response_appearance("update", adw::ResponseAppearance::Suggested);
        dialog.set_default_response(Some("update"));
        dialog.set_close_response("later");
        let weak = Rc::downgrade(&s);
        dialog.connect_response(None, move |_, response| {
            let Some(s) = weak.upgrade() else { return };
            match response {
                "update" => install_update(&s, &action, version.clone()),
                "notes" => {
                    let url = format!("{REPO_URL}/releases/tag/v{version}");
                    gtk::UriLauncher::new(&url).launch(Some(&s.window), gio::Cancellable::NONE, |_| {});
                    action.set_enabled(true);
                }
                _ => action.set_enabled(true),
            }
        });
        dialog.present(Some(&s.window));
    });
}

fn install_update(s: &Rc<Settings>, action: &gio::SimpleAction, version: String) {
    let progress = adw::Toast::builder().title(format!("Updating to Blurt {version}…")).timeout(0).build();
    s.toasts.add_toast(progress.clone());
    let (weak, action) = (Rc::downgrade(s), action.clone());
    glib::spawn_future_local(async move {
        let result =
            gio::spawn_blocking(move || update::run(crate::VERSION, &version).map_err(|e| format!("{e:#}"))).await;
        progress.dismiss();
        let Some(s) = weak.upgrade() else { return };
        action.set_enabled(true);
        match result {
            // The update script starts the new version once this one has quit.
            Ok(Ok(update::Outcome::Updated)) => {
                if let Some(app) = s.window.application() {
                    app.quit();
                }
            }
            Ok(Ok(update::Outcome::UpToDate)) => s.toast("Blurt is up to date"),
            Ok(Err(err)) => s.toast(&format!("Update failed: {err}")),
            Err(_) => s.toast("Update failed"),
        }
    });
}

fn show_about(window: &adw::ApplicationWindow) {
    adw::AboutDialog::builder()
        .application_name("Blurt")
        .application_icon(APP_ID)
        .version(crate::VERSION)
        .developer_name("Ralph Najem")
        .comments("Talk anywhere you can type. Hold a key, say it, and Blurt types clean, filler-free text into whatever app you are in.")
        .website(REPO_URL)
        .issue_url(format!("{REPO_URL}/issues"))
        .build()
        .present(Some(window));
}

pub fn build(app: &adw::Application) -> gtk::Window {
    let window = adw::ApplicationWindow::builder()
        .application(app)
        .title("Blurt")
        .name("settings")
        .default_width(620)
        .default_height(820)
        .build();
    let config = Config::load().unwrap_or_default();
    let status = account::status();
    let settings = Rc::new(Settings {
        window: window.clone(),
        config: RefCell::new(config),
        save: Cell::new(None),
        toasts: adw::ToastOverlay::new(),
        stack: adw::ViewStack::new(),
        banner: adw::Banner::builder().title("Blurt isn't set up for your account yet").button_label("Set up").build(),
        shortcut_row: adw::ActionRow::builder().title("Shortcuts").build(),
        hold_delay_row: RefCell::default(),
        reasoning_row: RefCell::default(),
        usage: RefCell::default(),
    });
    settings.apply_status(&status);

    let preferences = adw::PreferencesPage::new();
    preferences.add(&dictation_group(&settings));
    preferences.add(&openai_group(&settings));
    preferences.add(&speech_group(&settings));
    preferences.add(&cleanup_group(&settings));
    preferences.add(&budget_group(&settings));
    preferences.add(&typing_group(&settings));
    let stack = &settings.stack;
    stack.add_titled_with_icon(&preferences, Some("settings"), "Settings", "emblem-system-symbolic");
    stack.add_titled_with_icon(&usage_page(&settings), Some("usage"), "Usage", "power-profile-balanced-symbolic");
    let weak = Rc::downgrade(&settings);
    stack.connect_visible_child_name_notify(move |_| {
        if let Some(s) = weak.upgrade() {
            s.refresh_usage();
        }
    });

    let header = adw::HeaderBar::new();
    header.set_title_widget(Some(
        &adw::ViewSwitcher::builder().stack(stack).policy(adw::ViewSwitcherPolicy::Wide).build(),
    ));
    let menu = gio::Menu::new();
    menu.append(Some("Show History"), Some("app.history"));
    menu.append(Some("Check for Updates…"), Some("win.check-updates"));
    menu.append(Some("About Blurt"), Some("win.about"));
    header.pack_end(&gtk::MenuButton::builder().icon_name("open-menu-symbolic").menu_model(&menu).build());
    let check = gio::SimpleAction::new("check-updates", None);
    let weak = Rc::downgrade(&settings);
    check.connect_activate(move |action, _| {
        if let Some(s) = weak.upgrade() {
            check_for_updates(&s, action);
        }
    });
    window.add_action(&check);
    let about = gio::SimpleAction::new("about", None);
    let w = window.downgrade();
    about.connect_activate(move |_, _| {
        if let Some(window) = w.upgrade() {
            show_about(&window);
        }
    });
    window.add_action(&about);

    let weak = Rc::downgrade(&settings);
    settings.banner.connect_button_clicked(move |banner| {
        let Some(s) = weak.upgrade() else { return };
        banner.set_button_label(None);
        let weak = Rc::downgrade(&s);
        glib::spawn_future_local(async move {
            let status = gio::spawn_blocking(|| account::set_up(&[])).await;
            let Some(s) = weak.upgrade() else { return };
            let Ok(status) = status else {
                s.banner.set_button_label(Some("Set up"));
                return s.toast("Setup didn't finish, see `blurt setup` in a terminal");
            };
            s.apply_status(&status);
            if !status.complete() {
                s.toast("Setup didn't finish, see `blurt setup` in a terminal");
            } else if !status.extension_loaded {
                s.toast("All set. Log out and back in once to show Blurt in the top bar");
            } else {
                s.toast("All set");
            }
        });
    });

    let view = adw::ToolbarView::new();
    view.add_top_bar(&header);
    view.add_top_bar(&settings.banner);
    view.set_content(Some(stack));
    settings.toasts.set_child(Some(&view));
    window.set_content(Some(&settings.toasts));
    settings.sync_sensitivity();

    let weak = Rc::downgrade(&settings);
    glib::timeout_add_seconds_local(REFRESH_SECONDS, move || {
        let Some(s) = weak.upgrade() else { return glib::ControlFlow::Break };
        if s.window.is_visible() {
            s.refresh_usage();
        }
        glib::ControlFlow::Continue
    });
    // The window owns the settings: drop them when it closes.
    let keep = RefCell::new(Some(settings));
    window.connect_destroy(move |_| {
        keep.take();
    });
    window.upcast()
}
