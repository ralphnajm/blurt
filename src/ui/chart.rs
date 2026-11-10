//! A bar chart of daily spend, drawn with Cairo.

use std::cell::{Cell, RefCell};
use std::f64::consts::PI;
use std::rc::Rc;

use chrono::NaiveDate;
use gtk::prelude::*;
use gtk::{cairo, gdk, pango};

use super::format::money;

#[derive(Default)]
struct State {
    days: RefCell<Vec<(NaiveDate, f64)>>,
    hover: Cell<Option<usize>>,
    /// The plot area's left edge and width, from the last draw.
    plot: Cell<(f64, f64)>,
}

impl State {
    fn index_at(&self, x: f64) -> Option<usize> {
        let (left, width) = self.plot.get();
        let len = self.days.borrow().len();
        if len == 0 || width <= 0.0 || x < left || x > left + width {
            return None;
        }
        Some((((x - left) / (width / len as f64)) as usize).min(len - 1))
    }
}

#[derive(Clone)]
pub struct DailyChart {
    area: gtk::DrawingArea,
    state: Rc<State>,
}

impl DailyChart {
    pub fn new() -> Self {
        let area = gtk::DrawingArea::builder().content_height(170).hexpand(true).has_tooltip(true).build();
        let state = Rc::new(State::default());

        let s = state.clone();
        area.set_draw_func(move |area, cr, width, height| {
            let _ = draw(area, &s, cr, f64::from(width), f64::from(height));
        });

        let s = state.clone();
        area.connect_query_tooltip(move |_, x, _, _, tooltip| {
            let Some(index) = s.index_at(f64::from(x)) else { return false };
            let (day, cost) = s.days.borrow()[index];
            tooltip.set_text(Some(&format!("{} · {}", day.format("%a %b %-d"), money(cost))));
            true
        });

        let motion = gtk::EventControllerMotion::new();
        let (s, a) = (state.clone(), area.downgrade());
        motion.connect_motion(move |_, x, _| {
            let index = s.index_at(x);
            if index != s.hover.get() {
                s.hover.set(index);
                if let Some(area) = a.upgrade() {
                    area.queue_draw();
                }
            }
        });
        let (s, a) = (state.clone(), area.downgrade());
        motion.connect_leave(move |_| {
            s.hover.set(None);
            if let Some(area) = a.upgrade() {
                area.queue_draw();
            }
        });
        area.add_controller(motion);
        Self { area, state }
    }

    pub fn widget(&self) -> &gtk::DrawingArea {
        &self.area
    }

    pub fn set_days(&self, days: Vec<(NaiveDate, f64)>) {
        *self.state.days.borrow_mut() = days;
        self.area.queue_draw();
    }
}

/// The theme's accent color. `adw::StyleManager::accent_color_rgba` needs libadwaita 1.6.
#[allow(deprecated)]
fn accent_color(area: &gtk::DrawingArea) -> gdk::RGBA {
    area.style_context().lookup_color("accent_bg_color").unwrap_or_else(|| gdk::RGBA::new(0.21, 0.52, 0.89, 1.0))
}

#[allow(clippy::too_many_arguments)]
fn text(
    area: &gtk::DrawingArea,
    cr: &cairo::Context,
    label: &str,
    x: f64,
    y: f64,
    align: f64,
    color: &gdk::RGBA,
    alpha: f64,
) {
    let layout = area.create_pango_layout(Some(label));
    let attrs = pango::AttrList::new();
    attrs.insert(pango::AttrFloat::new_scale(0.83));
    layout.set_attributes(Some(&attrs));
    let (width, height) = layout.pixel_size();
    cr.set_source_rgba(f64::from(color.red()), f64::from(color.green()), f64::from(color.blue()), alpha);
    cr.move_to(x - f64::from(width) * align, y - f64::from(height) / 2.0);
    pangocairo::functions::show_layout(cr, &layout);
}

fn draw(
    area: &gtk::DrawingArea,
    state: &State,
    cr: &cairo::Context,
    width: f64,
    height: f64,
) -> Result<(), cairo::Error> {
    let fg = area.color();
    let accent = accent_color(area);
    let (left, right, top, bottom) = (52.0, 8.0, 16.0, 24.0);
    let (plot_w, plot_h) = (width - left - right, height - top - bottom);
    state.plot.set((left, plot_w));
    let days = state.days.borrow();
    let peak = days.iter().map(|(_, cost)| *cost).fold(0.0, f64::max);
    let scale_max = if peak > 0.0 { peak * 1.15 } else { 0.01 };
    let rgba = |c: &gdk::RGBA, alpha: f64| {
        cr.set_source_rgba(f64::from(c.red()), f64::from(c.green()), f64::from(c.blue()), alpha);
    };
    cr.set_line_width(1.0);
    for fraction in [0.0, 0.5, 1.0] {
        let y = top + plot_h - plot_h * fraction;
        rgba(&fg, if fraction == 0.0 { 0.22 } else { 0.08 });
        cr.move_to(left, y.round() + 0.5);
        cr.line_to(left + plot_w, y.round() + 0.5);
        cr.stroke()?;
        text(area, cr, &money(scale_max * fraction), left - 8.0, y, 1.0, &fg, 0.55);
    }
    if days.is_empty() {
        return Ok(());
    }
    let slot = plot_w / days.len() as f64;
    let bar_w = (slot - 2.0).max(1.0);
    let radius = (bar_w / 2.0).min(4.0);
    let hover = state.hover.get();
    let mut peak_index = 0;
    for (i, (_, cost)) in days.iter().enumerate() {
        if *cost > days[peak_index].1 {
            peak_index = i;
        }
        if *cost <= 0.0 {
            continue;
        }
        let bar_h = (plot_h * cost / scale_max).max(2.0);
        let x = left + i as f64 * slot + 1.0;
        let y = top + plot_h - bar_h;
        let r = radius.min(bar_h);
        rgba(&accent, if hover.is_none_or(|h| h == i) { 1.0 } else { 0.45 });
        cr.new_path();
        cr.move_to(x, top + plot_h);
        cr.line_to(x, y + r);
        cr.arc(x + r, y + r, r, PI, 1.5 * PI);
        cr.line_to(x + bar_w - r, y);
        cr.arc(x + bar_w - r, y + r, r, 1.5 * PI, 2.0 * PI);
        cr.line_to(x + bar_w, top + plot_h);
        cr.close_path();
        cr.fill()?;
    }
    if peak > 0.0 {
        let px = left + peak_index as f64 * slot + slot / 2.0;
        let py = top + plot_h - plot_h * peak / scale_max - 9.0;
        text(area, cr, &money(peak), px, py, 0.5, &fg, 0.8);
    }
    let last = days.len() - 1;
    for i in [0, days.len() / 2, last] {
        let label = days[i].0.format("%b %-d").to_string();
        let (align, offset) = if i == 0 {
            (0.0, 0.0)
        } else if i == last {
            (1.0, slot)
        } else {
            (0.5, slot / 2.0)
        };
        text(area, cr, &label, left + i as f64 * slot + offset, height - bottom / 2.0 + 2.0, align, &fg, 0.55);
    }
    Ok(())
}
