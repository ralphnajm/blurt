//! Everything you've dictated, newest first and searchable; click one to copy it.

use std::cell::{Cell, Ref, RefCell};
use std::rc::Rc;

use adw::prelude::*;
use gtk::glib::BoxedAnyObject;
use gtk::{gio, glib, pango};

use super::format::when;
use crate::core::history::{self, Entry};

const LINES: i32 = 4;

/// A history entry in the list model, with its text lowercased for search.
struct Item {
    entry: Entry,
    needle: String,
}

fn item(object: &glib::Object) -> Ref<'_, Item> {
    object.downcast_ref::<BoxedAnyObject>().expect("history item").borrow::<Item>()
}

struct State {
    store: gio::ListStore,
    filtered: gtk::FilterListModel,
    filter: gtk::CustomFilter,
    offset: Cell<u64>,
    query: Rc<RefCell<String>>,
    content: gtk::Stack,
    empty: adw::StatusPage,
    toasts: adw::ToastOverlay,
    clipboard: gtk::gdk::Clipboard,
}

impl State {
    /// Reads what was added to the history since last time.
    fn load(&self) {
        let chunk = history::read(self.offset.get());
        self.offset.set(chunk.offset);
        if chunk.reset {
            self.store.remove_all();
        }
        let items: Vec<BoxedAnyObject> = chunk
            .entries
            .into_iter()
            .rev()
            .map(|entry| BoxedAnyObject::new(Item { needle: entry.text.to_lowercase(), entry }))
            .collect();
        self.store.splice(0, 0, &items);
        self.sync_empty();
    }

    fn sync_empty(&self) {
        if self.filtered.n_items() > 0 {
            self.content.set_visible_child_name("list");
            return;
        }
        if self.store.n_items() > 0 {
            self.empty.set_title("No matches");
            self.empty.set_description(Some("Nothing you dictated contains that"));
        } else {
            self.empty.set_title("No transcriptions yet");
            self.empty.set_description(Some("Everything you dictate shows up here"));
        }
        self.content.set_visible_child_name("empty");
    }

    fn copy(&self, text: &str) {
        self.clipboard.set_text(text);
        self.toasts.add_toast(adw::Toast::builder().title("Copied to clipboard").timeout(2).build());
    }
}

fn setup_row(list_item: &gtk::ListItem, state: &Rc<State>) {
    let text = gtk::Label::builder()
        .xalign(0.0)
        .wrap(true)
        .wrap_mode(pango::WrapMode::WordChar)
        .lines(LINES)
        .ellipsize(pango::EllipsizeMode::End)
        .build();
    let meta = gtk::Label::builder().xalign(0.0).css_classes(["caption", "dim-label"]).build();
    let labels = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(4).hexpand(true).build();
    labels.append(&text);
    labels.append(&meta);
    let copy = gtk::Button::builder()
        .icon_name("edit-copy-symbolic")
        .valign(gtk::Align::Center)
        .tooltip_text("Copy")
        .css_classes(["flat"])
        .build();
    let (s, li) = (Rc::downgrade(state), list_item.downgrade());
    copy.connect_clicked(move |_| {
        let (Some(s), Some(li)) = (s.upgrade(), li.upgrade()) else { return };
        if let Some(object) = li.item() {
            let text = item(&object).entry.text.clone();
            s.copy(&text);
        }
    });
    let row = gtk::Box::builder().spacing(12).margin_top(6).margin_bottom(6).margin_start(12).margin_end(12).build();
    row.append(&labels);
    row.append(&copy);
    list_item.set_child(Some(&row));
}

fn bind_row(list_item: &gtk::ListItem) {
    let (Some(object), Some(row)) = (list_item.item(), list_item.child()) else { return };
    let item = item(&object);
    let entry = &item.entry;
    let labels = row.first_child().expect("labels");
    let text = labels.first_child().and_downcast::<gtk::Label>().expect("text label");
    let meta = labels.last_child().and_downcast::<gtk::Label>().expect("meta label");
    text.set_label(&entry.text);
    text.set_tooltip_text((entry.text.chars().count() > 200).then_some(entry.text.as_str()));
    let at = when(entry.at);
    meta.set_label(&if entry.typed { at } else { format!("{at} · not typed") });
}

pub fn build(app: &adw::Application) -> gtk::Window {
    let window = adw::ApplicationWindow::builder()
        .application(app)
        .title("Blurt History")
        .name("history")
        .default_width(560)
        .default_height(680)
        .build();

    let store = gio::ListStore::new::<BoxedAnyObject>();
    let query = Rc::new(RefCell::new(String::new()));
    let q = query.clone();
    let filter = gtk::CustomFilter::new(move |object| {
        let query = q.borrow();
        query.is_empty() || item(object).needle.contains(query.as_str())
    });
    let filtered = gtk::FilterListModel::new(Some(store.clone()), Some(filter.clone()));

    let empty = adw::StatusPage::builder().icon_name("edit-paste-symbolic").build();
    let content = gtk::Stack::new();
    let search = gtk::SearchEntry::builder().placeholder_text("Search transcriptions").width_chars(30).build();
    search.set_key_capture_widget(Some(&window));
    let header = adw::HeaderBar::builder().title_widget(&search).build();
    let view = adw::ToolbarView::new();
    view.add_top_bar(&header);
    view.set_content(Some(&content));
    let toasts = adw::ToastOverlay::new();
    toasts.set_child(Some(&view));
    window.set_content(Some(&toasts));

    let state = Rc::new(State {
        store,
        filtered: filtered.clone(),
        filter,
        offset: Cell::new(0),
        query,
        content: content.clone(),
        empty: empty.clone(),
        toasts,
        clipboard: window.clipboard(),
    });

    let factory = gtk::SignalListItemFactory::new();
    let s = state.clone();
    factory.connect_setup(move |_, object| {
        if let Some(list_item) = object.downcast_ref::<gtk::ListItem>() {
            setup_row(list_item, &s);
        }
    });
    factory.connect_bind(|_, object| {
        if let Some(list_item) = object.downcast_ref::<gtk::ListItem>() {
            bind_row(list_item);
        }
    });
    let list = gtk::ListView::builder()
        .model(&gtk::NoSelection::new(Some(filtered.clone())))
        .factory(&factory)
        .single_click_activate(true)
        .show_separators(true)
        .css_classes(["rich-list"])
        .build();
    let s = state.clone();
    list.connect_activate(move |_, position| {
        if let Some(object) = s.filtered.item(position) {
            let text = item(&object).entry.text.clone();
            s.copy(&text);
        }
    });
    let scrolled =
        gtk::ScrolledWindow::builder().child(&list).hscrollbar_policy(gtk::PolicyType::Never).vexpand(true).build();
    content.add_named(&scrolled, Some("list"));
    content.add_named(&empty, Some("empty"));

    let s = state.clone();
    filtered.connect_items_changed(move |_, _, _, _| s.sync_empty());
    let s = state.clone();
    search.connect_search_changed(move |entry| {
        *s.query.borrow_mut() = entry.text().trim().to_lowercase();
        s.filter.changed(gtk::FilterChange::Different);
    });

    let shortcuts = gtk::ShortcutController::new();
    let (w, entry) = (window.downgrade(), search.clone());
    shortcuts.add_shortcut(gtk::Shortcut::new(
        gtk::ShortcutTrigger::parse_string("Escape"),
        Some(gtk::CallbackAction::new(move |_, _| {
            if entry.text().is_empty() {
                if let Some(window) = w.upgrade() {
                    window.close();
                }
            } else {
                entry.set_text("");
            }
            glib::Propagation::Stop
        })),
    ));
    window.add_controller(shortcuts);

    state.load();
    if let Ok(monitor) =
        gio::File::for_path(history::path()).monitor_file(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE)
    {
        let s = state.clone();
        monitor.connect_changed(move |_, _, _, _| s.load());
        // Keep the monitor for as long as the window is open.
        window.connect_destroy(move |_| {
            monitor.cancel();
        });
    }
    window.upcast()
}
