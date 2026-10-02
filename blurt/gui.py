import subprocess
import threading
from collections.abc import Callable
from datetime import date, datetime

import cairo
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, PangoCairo

from blurt import __version__, account, history, usage
from blurt.config import Config, load_config, save_config
from blurt.data import ICONS_DIR
from blurt.credentials import get_api_key, set_api_key
from blurt.daemon import APP_ID, BUS_NAME, INTERFACE, OBJECT_PATH
from blurt.openai_api import check_key
from blurt.pricing import (
    PRICES_AS_OF,
    CLEANUP_MODEL,
    TEXT_PER_MILLION,
    TRANSCRIBE_PER_MINUTE,
)

REPO_URL = "https://github.com/ralphnajm/blurt"
KEYS_URL = "https://platform.openai.com/api-keys"
USAGE_URL = "https://platform.openai.com/usage"
REFRESH_SECONDS = 5
HISTORY_LINES = 4


def money(value: float) -> str:
    if value == 0:
        return "$0.00"
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:.2f}"


def rate(value: float) -> str:
    return "$" + f"{value:.4f}".rstrip("0").rstrip(".")


def minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f} min"


def tokens(count: int) -> str:
    return f"{count / 1000:.1f}k tokens" if count >= 1000 else f"{count} tokens"


def shortcut_label(name: str) -> str:
    try:
        accel = account.shortcut(name)
    except (OSError, subprocess.CalledProcessError):
        accel = ""
    if not accel:
        return "not set"
    ok, key, mods = Gtk.accelerator_parse(accel)
    return Gtk.accelerator_get_label(key, mods) if ok else accel


def when(at: datetime) -> str:
    days = (date.today() - at.date()).days
    if days == 0:
        return f"Today, {at:%H:%M}"
    if days == 1:
        return f"Yesterday, {at:%H:%M}"
    if at.year == date.today().year:
        return at.strftime("%b %-d, %H:%M")
    return at.strftime("%b %-d %Y, %H:%M")


def shortcuts_summary() -> str:
    return f"{shortcut_label('toggle')} start/stop · {shortcut_label('cancel')} cancel"


class DailyChart(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.set_content_height(170)
        self.set_hexpand(True)
        self.set_has_tooltip(True)
        self._days: list[tuple[date, float]] = []
        self._hover: int | None = None
        self._plot = (0.0, 0.0, 0.0, 0.0)
        self.set_draw_func(self._draw)
        self.connect("query-tooltip", self._on_tooltip)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    def set_days(self, days: list[tuple[date, float]]) -> None:
        self._days = days
        self.queue_draw()

    def _index_at(self, x: float) -> int | None:
        left, _top, width, _height = self._plot
        if not self._days or width <= 0 or x < left or x > left + width:
            return None
        return min(int((x - left) / (width / len(self._days))), len(self._days) - 1)

    def _on_motion(self, _ctrl: Gtk.EventControllerMotion, x: float, _y: float) -> None:
        index = self._index_at(x)
        if index != self._hover:
            self._hover = index
            self.queue_draw()

    def _on_leave(self, _ctrl: Gtk.EventControllerMotion) -> None:
        self._hover = None
        self.queue_draw()

    def _on_tooltip(self, _w: Gtk.Widget, x: int, _y: int, _kb: bool, tooltip: Gtk.Tooltip) -> bool:
        index = self._index_at(x)
        if index is None:
            return False
        day, cost = self._days[index]
        tooltip.set_text(f"{day.strftime('%a %b %-d')} · {money(cost)}")
        return True

    def _text(
        self,
        cr: cairo.Context,
        text: str,
        x: float,
        y: float,
        align: float,
        rgba: Gdk.RGBA,
        alpha: float,
    ) -> None:
        layout = self.create_pango_layout(text)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_scale_new(0.83))
        layout.set_attributes(attrs)
        width, height = layout.get_pixel_size()
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, alpha)
        cr.move_to(x - width * align, y - height / 2)
        PangoCairo.show_layout(cr, layout)

    def _draw(self, _area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int) -> None:
        fg = self.get_color()
        accent = Adw.StyleManager.get_default().get_accent_color_rgba()
        left, right, top, bottom = 52.0, 8.0, 16.0, 24.0
        plot_w, plot_h = width - left - right, height - top - bottom
        self._plot = (left, top, plot_w, plot_h)
        peak = max((cost for _d, cost in self._days), default=0.0)
        scale_max = peak * 1.15 if peak > 0 else 0.01
        cr.set_line_width(1)
        for fraction in (0, 0.5, 1):
            y = top + plot_h - plot_h * fraction
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.22 if fraction == 0 else 0.08)
            cr.move_to(left, round(y) + 0.5)
            cr.line_to(left + plot_w, round(y) + 0.5)
            cr.stroke()
            self._text(cr, money(scale_max * fraction), left - 8, y, 1.0, fg, 0.55)
        if not self._days:
            return
        slot = plot_w / len(self._days)
        bar_w = max(slot - 2, 1)
        radius = min(4.0, bar_w / 2)
        peak_index = max(range(len(self._days)), key=lambda i: self._days[i][1])
        for i, (_day, cost) in enumerate(self._days):
            if cost <= 0:
                continue
            bar_h = max(plot_h * cost / scale_max, 2)
            x = left + i * slot + 1
            y = top + plot_h - bar_h
            r = min(radius, bar_h)
            alpha = 1.0 if self._hover in (None, i) else 0.45
            cr.set_source_rgba(accent.red, accent.green, accent.blue, alpha)
            cr.new_path()
            cr.move_to(x, top + plot_h)
            cr.line_to(x, y + r)
            cr.arc(x + r, y + r, r, 3.14159, 4.71239)
            cr.line_to(x + bar_w - r, y)
            cr.arc(x + bar_w - r, y + r, r, 4.71239, 6.28318)
            cr.line_to(x + bar_w, top + plot_h)
            cr.close_path()
            cr.fill()
        if peak > 0:
            px = left + peak_index * slot + slot / 2
            py = top + plot_h - plot_h * peak / scale_max - 9
            self._text(cr, money(peak), px, py, 0.5, fg, 0.8)
        for i in (0, len(self._days) // 2, len(self._days) - 1):
            label = self._days[i][0].strftime("%b %-d")
            align = 0.0 if i == 0 else 1.0 if i == len(self._days) - 1 else 0.5
            x = left + i * slot + (0 if align == 0 else slot if align == 1 else slot / 2)
            self._text(cr, label, x, height - bottom / 2 + 2, align, fg, 0.55)


class Stat(Gtk.Box):
    def __init__(self, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("card")
        self.set_hexpand(True)
        caption = Gtk.Label(label=title, xalign=0)
        caption.add_css_class("caption")
        caption.add_css_class("dim-label")
        self.value = Gtk.Label(label="–", xalign=0)
        self.value.add_css_class("title-3")
        self.value.add_css_class("numeric")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.set_margin_top(12)
        inner.set_margin_bottom(12)
        inner.set_margin_start(14)
        inner.set_margin_end(14)
        inner.append(caption)
        inner.append(self.value)
        self.append(inner)


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Blurt")
        self.set_default_size(620, 820)
        self.config = load_config()
        self._account = account.status()
        self._save_id = 0
        self._toasts = Adw.ToastOverlay()
        self._stack = Adw.ViewStack()
        self._stack.add_titled_with_icon(
            self._settings_page(), "settings", "Settings", "emblem-system-symbolic"
        )
        self._stack.add_titled_with_icon(
            self._usage_page(), "usage", "Usage", "power-profile-balanced-symbolic"
        )
        self._stack.connect("notify::visible-child-name", lambda *_: self._refresh_usage())
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.ViewSwitcher(stack=self._stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        )
        menu = Gio.Menu()
        menu.append("Show History", "app.history")
        menu.append("About Blurt", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_: self._show_about())
        self.add_action(about)
        self._banner = Adw.Banner(
            title="Blurt isn't set up for your account yet", button_label="Set up"
        )
        self._banner.connect("button-clicked", self._on_set_up)
        self._banner.set_revealed(not self._account.complete)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self._banner)
        view.set_content(self._stack)
        self._toasts.set_child(view)
        self.set_content(self._toasts)
        self._sync_sensitivity()
        GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)

    def toast(self, message: str) -> bool:
        self._toasts.add_toast(Adw.Toast(title=message, timeout=3))
        return False

    def _settings_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        page.add(self._service_group())
        page.add(self._openai_group())
        page.add(self._speech_group())
        page.add(self._cleanup_group())
        page.add(self._budget_group())
        page.add(self._typing_group())
        return page

    def _service_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Service")
        self._service_row = Adw.SwitchRow(
            title="Background service", subtitle="Listens for the dictation shortcut"
        )
        self._service_row.set_active(self._account.service_active)
        self._service_row.connect("notify::active", self._on_service_toggled)
        group.add(self._service_row)
        self._shortcut_row = Adw.ActionRow(title="Shortcuts", subtitle=shortcuts_summary())
        change = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)
        change.connect("clicked", lambda *_: subprocess.Popen(["gnome-control-center", "keyboard"]))
        self._shortcut_row.add_suffix(change)
        group.add(self._shortcut_row)
        group.add(
            self._combo(
                "Hold to talk",
                None,
                [("off", "Off"), ("ctrl", "Ctrl"), ("alt", "Alt"), ("shift", "Shift")],
                "hold_key",
                lambda key: (
                    "Hold the key to start, release it to stop"
                    if key != "off"
                    else "Only the shortcuts start dictation"
                ),
            )
        )
        self._hold_delay_row = Adw.SpinRow.new_with_range(0.3, 3.0, 0.1)
        self._hold_delay_row.set_title("Hold delay")
        self._hold_delay_row.set_subtitle("Seconds to hold before recording starts")
        self._hold_delay_row.set_digits(1)
        self._hold_delay_row.set_value(self.config.hold_delay)
        self._hold_delay_row.connect(
            "notify::value", lambda row, _p: self._set("hold_delay", round(row.get_value(), 1))
        )
        group.add(self._hold_delay_row)
        return group

    def _openai_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="OpenAI", description="The key is stored in your GNOME keyring"
        )
        self._key_row = Adw.PasswordEntryRow(title="API key", show_apply_button=True)
        self._key_row.set_text(get_api_key() or "")
        self._key_row.connect("apply", self._on_key_apply)
        group.add(self._key_row)
        test = Adw.ActionRow(
            title="Test connection", subtitle="Checks the saved key against the API"
        )
        button = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        button.connect("clicked", self._on_test_key)
        test.add_suffix(button)
        group.add(test)
        link = Adw.ActionRow(
            title="Create a key", subtitle="platform.openai.com/api-keys", activatable=True
        )
        link.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
        link.connect("activated", lambda *_: Gtk.UriLauncher(uri=KEYS_URL).launch(self, None, None))
        group.add(link)
        return group

    def _speech_group(self) -> Adw.PreferencesGroup:
        c = self.config
        group = Adw.PreferencesGroup(title="Speech recognition")
        self._model_row = self._combo(
            "Transcription model",
            None,
            [(m, m) for m in TRANSCRIBE_PER_MINUTE],
            "transcribe_model",
            lambda m: f"{rate(TRANSCRIBE_PER_MINUTE[m])}/min" if m in TRANSCRIBE_PER_MINUTE else "",
        )
        group.add(self._model_row)
        group.add(self._entry("Language", "language", "Language code such as en or de, or auto"))
        vocabulary = Adw.EntryRow(title="Vocabulary (comma-separated)")
        vocabulary.set_text(", ".join(c.vocabulary))
        vocabulary.connect(
            "changed",
            lambda row: self._set(
                "vocabulary", [w.strip() for w in row.get_text().split(",") if w.strip()]
            ),
        )
        group.add(vocabulary)
        return group

    def _cleanup_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Cleanup", description="Removes fillers and false starts and fixes grammar"
        )
        price_in, price_out = TEXT_PER_MILLION[CLEANUP_MODEL]
        group.add(
            self._combo(
                "Cleanup",
                None,
                [("openai", "OpenAI"), ("basic", "Filler removal only"), ("off", "Off")],
                "cleanup",
                lambda mode: (
                    f"{CLEANUP_MODEL} · {rate(price_in)} in · {rate(price_out)} out per 1M tokens"
                    if mode == "openai"
                    else "Runs locally, free"
                    if mode == "basic"
                    else "Types the transcript as is"
                ),
            )
        )
        self._reasoning_row = self._combo(
            "Reasoning",
            None,
            [("none", "None"), ("low", "Low")],
            "cleanup_reasoning",
            lambda level: "Fastest" if level == "none" else "Slower, handles messier sentences",
        )
        group.add(self._reasoning_row)
        group.add(self._entry("Extra instructions", "cleanup_instructions", None))
        return group

    def _budget_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Budget")
        spin = Adw.SpinRow.new_with_range(0, 500, 1)
        spin.set_title("Monthly budget")
        spin.set_subtitle("USD, 0 for no budget")
        spin.set_digits(2)
        spin.set_value(self.config.monthly_budget)
        spin.connect(
            "notify::value", lambda row, _p: self._set("monthly_budget", float(row.get_value()))
        )
        group.add(spin)
        group.add(
            self._combo(
                "When the budget is reached",
                None,
                [("warn", "Warn me"), ("block", "Stop dictating")],
                "budget_action",
            )
        )
        return group

    def _typing_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Typing and feedback")
        group.add(self._switch("Add a trailing space", None, "trailing_space"))
        group.add(self._switch("Sounds", None, "sounds"))
        return group

    def _combo(
        self,
        title: str,
        subtitle: str | None,
        options: list[tuple[str, str]],
        key: str,
        describe: Callable[[str], str] | None = None,
    ) -> Adw.ComboRow:
        values = [v for v, _l in options]
        current = getattr(self.config, key)
        if current not in values:
            options = [*options, (current, current)]
            values.append(current)
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for _v, label in options]))
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_selected(values.index(current))

        def changed(r: Adw.ComboRow, _p: object) -> None:
            value = values[r.get_selected()]
            if describe:
                r.set_subtitle(describe(value))
            self._set(key, value)

        if describe:
            row.set_subtitle(describe(current))
        row.connect("notify::selected", changed)
        return row

    def _switch(self, title: str, subtitle: str | None, key: str) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_active(getattr(self.config, key))
        row.connect("notify::active", lambda r, _p: self._set(key, r.get_active()))
        return row

    def _entry(self, title: str, key: str, tooltip: str | None) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title)
        row.set_text(getattr(self.config, key))
        if tooltip:
            row.set_tooltip_text(tooltip)
        row.connect("changed", lambda r: self._set(key, r.get_text().strip()))
        return row

    def _set(self, key: str, value: object) -> None:
        setattr(self.config, key, value)
        self._sync_sensitivity()
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(400, self._save)

    def _save(self) -> bool:
        self._save_id = 0
        save_config(self.config)
        self._call_daemon("Reload")
        return False

    def _sync_sensitivity(self) -> None:
        c: Config = self.config
        self._reasoning_row.set_sensitive(c.cleanup == "openai")
        self._hold_delay_row.set_sensitive(c.hold_key != "off")

    def _call_daemon(
        self, method: str, timeout_ms: int = 3000, done: Callable[[str | None], None] | None = None
    ) -> None:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def finish(conn: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                conn.call_finish(result)
                error = None
            except GLib.Error as exc:
                error = exc.message
            if done:
                done(error)

        bus.call(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            method,
            None,
            None,
            Gio.DBusCallFlags.NONE,
            timeout_ms,
            None,
            finish,
        )

    def _show_about(self) -> None:
        dialog = Adw.AboutDialog(
            application_name="Blurt",
            application_icon=APP_ID,
            version=__version__,
            developer_name="Ralph Najem",
            comments="Talk anywhere you can type. Press a shortcut, say it, and Blurt types "
            "clean, filler-free text into whatever app you are in.",
            website=REPO_URL,
            issue_url=f"{REPO_URL}/issues",
        )
        dialog.present(self)

    def _on_set_up(self, _banner: Adw.Banner) -> None:
        self._banner.set_button_label("")

        def work() -> None:
            status = account.set_up()
            GLib.idle_add(self._after_set_up, status)

        threading.Thread(target=work, daemon=True).start()

    def _after_set_up(self, status: account.Status) -> bool:
        self._account = status
        self._banner.set_revealed(not status.complete)
        self._banner.set_button_label("Set up")
        self._service_row.set_active(status.service_active)
        self._shortcut_row.set_subtitle(shortcuts_summary())
        if not status.complete:
            self.toast("Setup didn't finish, see `blurt setup` in a terminal")
        elif not status.extension_loaded:
            self.toast("All set. Log out and back in once to show Blurt in the top bar")
        else:
            self.toast("All set")
        return False

    def _on_service_toggled(self, row: Adw.SwitchRow, _param: object) -> None:
        action = ["enable", "--now"] if row.get_active() else ["disable", "--now"]
        subprocess.run(["systemctl", "--user", *action, account.SERVICE], check=False)
        self.toast("Service started" if row.get_active() else "Service stopped")

    def _on_key_apply(self, row: Adw.PasswordEntryRow) -> None:
        key = row.get_text().strip()
        if not key:
            return
        set_api_key(key)
        self.toast("API key saved to keyring")

    def _on_test_key(self, _button: Gtk.Button) -> None:
        key = get_api_key()
        if not key:
            self.toast("Save an API key first")
            return

        def run() -> None:
            try:
                check_key(key)
                GLib.idle_add(self.toast, "Key works")
            except Exception as exc:
                GLib.idle_add(self.toast, f"Key check failed: {exc}")

        threading.Thread(target=run, daemon=True).start()

    def _usage_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for side in ("top", "bottom"):
            getattr(box, f"set_margin_{side}")(24)
        stats = Gtk.Box(spacing=12, homogeneous=True)
        self._stat_today = Stat("Today")
        self._stat_month = Stat("This month")
        self._stat_audio = Stat("Audio this month")
        self._stat_budget = Stat("Budget left")
        for stat in (self._stat_today, self._stat_month, self._stat_audio, self._stat_budget):
            stats.append(stat)
        box.append(stats)
        self._budget_bar = Gtk.LevelBar(min_value=0, max_value=1)
        box.append(self._budget_bar)
        chart_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        chart_card.add_css_class("card")
        heading = Gtk.Label(label="Daily spend, last 30 days", xalign=0)
        heading.add_css_class("heading")
        heading.set_margin_top(14)
        heading.set_margin_start(14)
        self._chart = DailyChart()
        self._chart.set_margin_start(6)
        self._chart.set_margin_end(10)
        self._chart.set_margin_bottom(8)
        chart_card.append(heading)
        chart_card.append(self._chart)
        box.append(chart_card)
        self._models_group = Adw.PreferencesGroup(title="This month by model")
        self._recent_group = Adw.PreferencesGroup(title="Recent requests")
        self._rows: dict[Adw.PreferencesGroup, list[Gtk.Widget]] = {
            self._models_group: [],
            self._recent_group: [],
        }
        box.append(self._models_group)
        box.append(self._recent_group)
        note = Gtk.Label(
            label=f"Estimated from audio length and token counts at OpenAI list prices ({PRICES_AS_OF}). "
            "Your OpenAI invoice is the source of truth.",
            wrap=True,
            xalign=0,
        )
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        box.append(note)
        link = Gtk.LinkButton(
            uri=USAGE_URL, label="Open the OpenAI usage dashboard", halign=Gtk.Align.START
        )
        box.append(link)
        clamp = Adw.Clamp(maximum_size=720, child=box)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        return Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def _tick(self) -> bool:
        if self.get_visible() and self._stack.get_visible_child_name() == "usage":
            self._refresh_usage()
        return True

    def _refresh_usage(self) -> None:
        if self._stack.get_visible_child_name() != "usage":
            return
        now = datetime.now()
        month = usage.month_start()
        today = datetime(now.year, now.month, now.day)
        month_cost = usage.cost_since(month)
        self._stat_today.value.set_label(money(usage.cost_since(today)))
        self._stat_month.value.set_label(money(month_cost))
        self._stat_audio.value.set_label(minutes(usage.audio_seconds_since(month)))
        budget = self.config.monthly_budget
        self._budget_bar.set_visible(budget > 0)
        if budget > 0:
            self._stat_budget.value.set_label(money(max(budget - month_cost, 0)))
            self._budget_bar.set_value(min(month_cost / budget, 1))
        else:
            self._stat_budget.value.set_label("No budget")
        self._chart.set_days(usage.daily_costs(30))
        self._replace_rows(
            self._models_group, [self._model_row_for(m) for m in usage.by_model(month)]
        )
        self._replace_rows(self._recent_group, [self._request_row(r) for r in usage.recent(15)])

    def _replace_rows(self, group: Adw.PreferencesGroup, rows: list[Gtk.Widget]) -> None:
        for row in self._rows[group]:
            group.remove(row)
        if not rows:
            rows = [
                Adw.ActionRow(title="Nothing yet", subtitle="Blurt something to see usage here")
            ]
        for row in rows:
            group.add(row)
        self._rows[group] = rows

    def _model_row_for(self, m: usage.ModelUsage) -> Adw.ActionRow:
        amount = (
            minutes(m.audio_seconds)
            if m.kind != "cleanup"
            else tokens(m.input_tokens + m.output_tokens)
        )
        plural = "request" if m.requests == 1 else "requests"
        row = Adw.ActionRow(title=m.model, subtitle=f"{m.kind} · {m.requests} {plural} · {amount}")
        row.add_suffix(self._amount(m.cost))
        return row

    def _request_row(self, r: usage.Request) -> Adw.ActionRow:
        amount = (
            minutes(r.audio_seconds)
            if r.kind != "cleanup"
            else tokens(r.input_tokens + r.output_tokens)
        )
        row = Adw.ActionRow(
            title=f"{r.kind} · {r.model}", subtitle=f"{r.at.strftime('%b %-d, %H:%M')} · {amount}"
        )
        row.add_suffix(self._amount(r.cost))
        return row

    def _amount(self, cost: float) -> Gtk.Label:
        label = Gtk.Label(label=money(cost))
        label.add_css_class("numeric")
        return label


class HistoryItem(GObject.Object):
    def __init__(self, entry: history.Entry) -> None:
        super().__init__()
        self.entry = entry
        self.needle = entry.text.casefold()


class HistoryWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Blurt History")
        self.set_default_size(560, 680)
        self._offset = 0
        self._query = ""
        self._store = Gio.ListStore(item_type=HistoryItem)
        self._filter = Gtk.CustomFilter.new(self._matches)
        filtered = Gtk.FilterListModel(model=self._store, filter=self._filter)
        filtered.connect("items-changed", lambda *_: self._sync_empty())
        self._filtered = filtered

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        self._list = Gtk.ListView(
            model=Gtk.NoSelection(model=filtered),
            factory=factory,
            single_click_activate=True,
            show_separators=True,
        )
        self._list.add_css_class("rich-list")
        self._list.connect("activate", lambda _l, position: self._copy_at(position))
        scrolled = Gtk.ScrolledWindow(
            child=self._list, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True
        )

        self._empty = Adw.StatusPage(icon_name="edit-paste-symbolic")
        self._content = Gtk.Stack()
        self._content.add_named(scrolled, "list")
        self._content.add_named(self._empty, "empty")

        self._search = Gtk.SearchEntry(placeholder_text="Search transcriptions")
        self._search.set_width_chars(30)
        self._search.connect("search-changed", self._on_search)
        self._search.set_key_capture_widget(self)
        header = Adw.HeaderBar(title_widget=self._search)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self._content)
        self._toasts = Adw.ToastOverlay(child=view)
        self.set_content(self._toasts)

        close = Gtk.ShortcutController()
        close.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                action=Gtk.CallbackAction.new(self._on_escape),
            )
        )
        self.add_controller(close)

        self._load()
        self._monitor = Gio.File.new_for_path(str(history.HISTORY_PATH)).monitor_file(
            Gio.FileMonitorFlags.NONE, None
        )
        self._monitor.connect("changed", lambda *_: self._load())

    def _load(self) -> None:
        entries, self._offset, reset = history.read(self._offset)
        if reset:
            self._store.remove_all()
        for entry in entries:
            self._store.insert(0, HistoryItem(entry))
        self._sync_empty()

    def _setup_row(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        text = Gtk.Label(
            xalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            lines=HISTORY_LINES,
            ellipsize=Pango.EllipsizeMode.END,
        )
        meta = Gtk.Label(xalign=0)
        meta.add_css_class("caption")
        meta.add_css_class("dim-label")
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        labels.append(text)
        labels.append(meta)
        copy = Gtk.Button(
            icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Copy"
        )
        copy.add_css_class("flat")
        copy.connect("clicked", lambda _b: self._copy(item.get_item().entry))
        row = Gtk.Box(spacing=12)
        for side in ("top", "bottom"):
            getattr(row, f"set_margin_{side}")(6)
        for side in ("start", "end"):
            getattr(row, f"set_margin_{side}")(12)
        row.append(labels)
        row.append(copy)
        item.set_child(row)

    def _bind_row(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        entry = item.get_item().entry
        labels = item.get_child().get_first_child()
        text, meta = labels.get_first_child(), labels.get_last_child()
        text.set_label(entry.text)
        text.set_tooltip_text(entry.text if len(entry.text) > 200 else None)
        meta.set_label(when(entry.at) if entry.typed else f"{when(entry.at)} · not typed")

    def _matches(self, item: HistoryItem) -> bool:
        return not self._query or self._query in item.needle

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self._query = entry.get_text().strip().casefold()
        self._filter.changed(Gtk.FilterChange.DIFFERENT)

    def _on_escape(self, *_args: object) -> bool:
        if self._search.get_text():
            self._search.set_text("")
        else:
            self.close()
        return True

    def _sync_empty(self) -> None:
        if self._filtered.get_n_items():
            self._content.set_visible_child_name("list")
            return
        if self._store.get_n_items():
            self._empty.set_title("No matches")
            self._empty.set_description("Nothing you dictated contains that")
        else:
            self._empty.set_title("No transcriptions yet")
            self._empty.set_description("Everything you dictate shows up here")
        self._content.set_visible_child_name("empty")

    def _copy_at(self, position: int) -> None:
        item = self._filtered.get_item(position)
        if item is not None:
            self._copy(item.entry)

    def _copy(self, entry: history.Entry) -> None:
        self.get_clipboard().set(entry.text)
        self._toasts.add_toast(Adw.Toast(title="Copied to clipboard", timeout=2))


class App(Adw.Application):
    def __init__(self, start: str) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._start = start
        self.connect("startup", self._on_startup)
        self.connect("activate", lambda *_: self.show(self._start))
        for name in ("settings", "history"):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, name=name: self.show(name))
            self.add_action(action)

    def _on_startup(self, _app: Adw.Application) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.IconTheme.get_for_display(display).add_search_path(str(ICONS_DIR))
        Gtk.Window.set_default_icon_name(APP_ID)

    def show(self, name: str) -> None:
        kind = HistoryWindow if name == "history" else Window
        window = next((w for w in self.get_windows() if isinstance(w, kind)), None)
        (window or kind(self)).present()


def run(start: str = "settings") -> int:
    app = App(start)
    app.register(None)
    if app.get_is_remote():
        # Blurt is already open; ask that instance to show the window and exit.
        app.activate_action(start, None)
        connection = app.get_dbus_connection()
        if connection is not None:
            connection.flush_sync(None)
        return 0
    return app.run([])
