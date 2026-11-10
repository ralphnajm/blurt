//! The settings and usage window, and the history window.

mod chart;
mod format;
mod history_window;
mod settings_window;

use adw::prelude::*;
use gtk::{gio, glib};

pub const APP_ID: &str = "dev.blurt.Blurt";
pub const REPO_URL: &str = "https://github.com/ralphnajm/blurt";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Start {
    Settings,
    History,
}

impl Start {
    fn action(self) -> &'static str {
        match self {
            Self::Settings => "settings",
            Self::History => "history",
        }
    }
}

fn show(app: &adw::Application, start: Start) {
    // Each window is named after the action that shows it.
    let existing = app.windows().into_iter().find(|w| w.widget_name() == start.action());
    let window = existing.unwrap_or_else(|| match start {
        Start::Settings => settings_window::build(app),
        Start::History => history_window::build(app),
    });
    window.present();
}

pub fn run(start: Start) -> std::process::ExitCode {
    let app = adw::Application::builder().application_id(APP_ID).flags(gio::ApplicationFlags::empty()).build();
    for which in [Start::Settings, Start::History] {
        let action = gio::SimpleAction::new(which.action(), None);
        let weak = app.downgrade();
        action.connect_activate(move |_, _| {
            if let Some(app) = weak.upgrade() {
                show(&app, which);
            }
        });
        app.add_action(&action);
    }
    app.connect_startup(|_| gtk::Window::set_default_icon_name(APP_ID));
    app.connect_activate(move |app| show(app, start));
    if app.register(gio::Cancellable::NONE).is_ok() && app.is_remote() {
        // Blurt is already open: ask that instance to show the window, then exit.
        app.activate_action(start.action(), None);
        if let Some(connection) = app.dbus_connection() {
            let _ = connection.flush_sync(gio::Cancellable::NONE);
        }
        return std::process::ExitCode::SUCCESS;
    }
    let code = app.run_with_args::<&str>(&[]);
    if code == glib::ExitCode::SUCCESS { std::process::ExitCode::SUCCESS } else { std::process::ExitCode::FAILURE }
}
