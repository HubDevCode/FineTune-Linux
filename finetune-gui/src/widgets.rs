// FineTune Linux — widget custom (traduzione dei componenti Swift).

use eframe::egui::{
    self, Align2, Color32, CursorIcon, FontId, Id, Pos2, Rect, Response, Sense, Shape, Stroke, Ui,
    Vec2,
};

use crate::design::{self, Tokens};
use crate::icons::IconDrawer;

pub const VU_BARS: usize = 8;
const VU_THRESHOLDS: [f32; VU_BARS] = [-40.0, -30.0, -20.0, -14.0, -10.0, -6.0, -3.0, 0.0];

fn vu_color(t: &Tokens, i: usize) -> Color32 {
    match i {
        0..=3 => t.vu_green,
        4 | 5 => t.vu_yellow,
        6 => t.vu_orange,
        _ => t.vu_red,
    }
}

/// VU meter: 8 barre verticali con peak hold (port da VUMeter.swift).
pub struct VuMeter {
    pub bars: Vec<bool>,
    pub level: f32, // linear 0..1
}

impl VuMeter {
    pub fn new() -> Self {
        Self {
            bars: vec![false; VU_BARS],
            level: 0.0,
        }
    }

    pub fn update(&mut self, linear: f32, dt: f32) {
        self.level = (self.level * (1.0 - (dt * 0.05).min(1.0)))
            .max(linear)
            .clamp(0.0, 1.0);
        let db = if self.level > 0.0 { 20.0 * self.level.log10() } else { -60.0 };
        for (i, th) in VU_THRESHOLDS.iter().enumerate() {
            self.bars[i] = db >= *th;
        }
    }

    pub fn draw(&self, ui: &mut Ui, rect: Rect, muted: bool, t: &Tokens) {
        let p = ui.painter();
        let gap = 2.0;
        let w = (rect.width() - gap * (VU_BARS as f32 - 1.0)) / VU_BARS as f32;
        for i in 0..VU_BARS {
            let x = rect.left() + i as f32 * (w + gap);
            let bar = Rect::from_min_size(Pos2::new(x, rect.top()), Vec2::new(w, rect.height()));
            let color = if muted {
                t.vu_muted
            } else if self.bars[i] {
                vu_color(t, i)
            } else {
                t.vu_unlit
            };
            p.add(Shape::rect_filled(bar, 2.0, color));
        }
    }
}

/// Pulsante mute (icona volume con indicatore di stato).
pub fn mute_button(
    ui: &mut Ui,
    _id: Id,
    muted: bool,
    level: f32,
    t: &Tokens,
) -> Response {
    let size = egui::vec2(design::ROW_CONTENT_HEIGHT, design::ROW_CONTENT_HEIGHT);
    let (rect, resp) = ui.allocate_exact_size(size, Sense::click());
    if ui.is_rect_visible(rect) {
        let hovered = resp.hovered();
        let color = if muted {
            t.muted_indicator
        } else if hovered {
            t.interactive_hover
        } else {
            t.interactive_default
        };
        let icon_rect = Rect::from_center_size(
            rect.center(),
            egui::vec2(18.0, 18.0),
        );
        let icon = IconDrawer::new(ui);
        if muted {
            icon.speaker_muted(icon_rect, color);
        } else {
            let shown = if level <= 0.001 { 0.0 } else { level };
            icon.speaker(icon_rect, shown, color);
        }
    }
    resp
}

/// Slider "LiquidGlass": track 4px, fill accent, percentuale lineare.
pub struct GlassSlider {
    pub width: f32,
}

impl GlassSlider {
    pub fn ui(&self, ui: &mut Ui, value: &mut f32, t: &Tokens) -> Response {
        let height = 16.0;
        let (rect, mut resp) = ui.allocate_exact_size(
            egui::vec2(self.width, height),
            Sense::click_and_drag(),
        );
        let v = value.clamp(0.0, 1.0);
        if ui.is_rect_visible(rect) {
            let p = ui.painter();
            let track_h = 4.0;
            let track = Rect::from_center_size(
                rect.center(),
                egui::vec2(rect.width(), track_h),
            );
            p.add(Shape::rect_filled(track, track_h / 2.0, t.slider_track));
            let fill_w = track.width() * v;
            if fill_w > 0.0 {
                let fill = Rect::from_min_size(track.min, egui::vec2(fill_w, track_h));
                p.add(Shape::rect_filled(fill, track_h / 2.0, t.accent));
            }
            // marker unity (50%)
            let unity_x = track.left() + track.width() * 0.5;
            p.add(Shape::line(
                vec![
                    Pos2::new(unity_x, track.top() - 1.0),
                    Pos2::new(unity_x, track.bottom() + 1.0),
                ],
                Stroke::new(1.0, t.unity_marker),
            ));
            // thumb
            let cx = track.left() + track.width() * v;
            if resp.dragged() || resp.hovered() {
                p.circle_filled(Pos2::new(cx, rect.center().y), 5.5, t.hover_surface);
            }
            p.circle_filled(Pos2::new(cx, rect.center().y), 3.0, Color32::WHITE);
        }
        // input: drag (aggiorna il valore a ogni frame) + scroll su hover
        if resp.dragged() {
            if let Some(pos) = resp.interact_pointer_pos() {
                let newv = ((pos.x - rect.left()) / rect.width()).clamp(0.0, 1.0);
                if (newv - *value).abs() > 0.0005 {
                    *value = newv;
                    resp.mark_changed();
                }
            }
        } else if resp.hovered() {
            let scroll = ui.input(|i| i.smooth_scroll_delta).y;
            if scroll.abs() > f32::EPSILON {
                *value = (*value + scroll * 0.0008).clamp(0.0, 1.0);
                resp.mark_changed();
            }
        }
        resp
    }
}

/// Chevron boost 1x/2x/3x/4x (clic = ciclo avanti).
pub fn boost_chevrons(
    ui: &mut Ui,
    boost: u8,
    t: &Tokens,
) -> Option<u8> {
    let mut clicked = None;
    ui.horizontal(|ui| {
        for i in 0..3 {
            let lit = (boost as u8).saturating_sub(1) as usize >= i;
            let size = egui::vec2(16.0, 16.0);
            let (rect, resp) = ui.allocate_exact_size(size, Sense::click());
            if ui.is_rect_visible(rect) {
                let color = if lit {
                    t.accent
                } else if resp.hovered() {
                    t.interactive_hover
                } else {
                    t.interactive_default.gamma_multiply(0.25)
                };
                let icon_rect = Rect::from_center_size(rect.center(), egui::vec2(12.0, 12.0));
                IconDrawer::new(ui).chevron(icon_rect, color);
            }
            if resp.clicked() {
                clicked = Some(1);
            }
        }
    });
    if clicked.is_some() {
        Some(if boost >= 4 { 1 } else { boost + 1 })
    } else {
        None
    }
}

/// Percentuale editabile (larghezza 40, monospace).
pub fn editable_percentage(ui: &mut Ui, value: &mut f32, t: &Tokens) -> bool {
    let _ = t;
    let mut buf = format!("{}", (*value * 100.0).round() as i64);
    let w = ui.add(
        egui::TextEdit::singleline(&mut buf)
            .font(FontId::monospace(11.0))
            .desired_width(design::PERCENTAGE_WIDTH - 8.0)
            .horizontal_align(egui::Align::Center),
    );
    if w.lost_focus() {
        if let Ok(pct) = buf.parse::<f32>() {
            *value = (pct / 100.0).clamp(0.0, 1.0);
            return true;
        }
    }
    false
}

/// Picker device con popup (singolo / multi).
pub struct DevicePicker {
    pub open: bool,
    pub mode_multi: bool,
}

impl DevicePicker {
    pub fn new() -> Self {
        Self {
            open: false,
            mode_multi: false,
        }
    }

    /// options: (sink_id, label). selected: corrente. Returns (selected_to_route, follow_default).
    pub fn ui(
        &mut self,
        ui: &mut Ui,
        id: Id,
        options: &[(String, String)],
        selected: &[String],
        follows_default: bool,
        t: &Tokens,
    ) -> Option<PickerAction> {
        let size = egui::vec2(design::ROW_CONTENT_HEIGHT, design::ROW_CONTENT_HEIGHT);
        let (rect, resp) = ui.allocate_exact_size(size, Sense::click());
        let mut action = None;
        if ui.is_rect_visible(rect) {
            let g = IconDrawer::new(ui);
            let icon_rect = Rect::from_center_size(rect.center(), egui::vec2(16.0, 16.0));
            let color = if resp.hovered() {
                t.interactive_hover
            } else {
                t.interactive_default
            };
            if self.mode_multi && selected.is_empty() {
                g.waveform(icon_rect, color);
            } else if follows_default {
                g.globe(icon_rect, color);
            } else {
                g.speaker(icon_rect, 0.9, color);
            }
            // badge count per multi
            if self.mode_multi && !selected.is_empty() {
                ui.painter().circle_filled(
                    Pos2::new(rect.right() - 5.0, rect.top() + 5.0),
                    6.5,
                    t.accent,
                );
                ui.painter().text(
                    Pos2::new(rect.right() - 5.0, rect.top() + 4.0),
                    Align2::CENTER_CENTER,
                    &selected.len().to_string(),
                    FontId::proportional(8.0),
                    Color32::WHITE,
                );
            }
        }
        if resp.clicked() {
            self.open = !self.open;
        }
        if self.open {
            let popup_id = id.with("picker");
            let anchor = egui::PopupAnchor::from(&resp);
            let open = &mut self.open;
            let mut should_close = false;
            egui::Popup::new(popup_id, ui.ctx().clone(), anchor, resp.layer_id)
                .open_bool(open)
                .close_behavior(egui::PopupCloseBehavior::CloseOnClickOutside)
                .width(210.0)
                .show(|ui| {
                    let mode_multi = &mut self.mode_multi;
                    ui.horizontal(|ui| {
                        if ui
                            .selectable_label(!*mode_multi, "Single")
                            .clicked()
                        {
                            *mode_multi = false;
                        }
                        if ui
                            .selectable_label(*mode_multi, "Multi")
                            .on_hover_text("Selezione multipla")
                            .clicked()
                        {
                            *mode_multi = true;
                        }
                    });
                    ui.separator();
                    for (sid, label) in options {
                        if sid == "__system_audio__" && *mode_multi {
                            ui.add_enabled(false, egui::Button::new(label));
                            continue;
                        }
                        let is_sel = if *mode_multi {
                            selected.contains(sid)
                        } else {
                            if sid == "__system_audio__" {
                                follows_default
                            } else {
                                !follows_default && selected.contains(sid)
                            }
                        };
                        if ui.selectable_label(is_sel, label).clicked() {
                            should_close = true;
                            if *mode_multi {
                                action = Some(PickerAction::Toggle(sid.clone()));
                            } else if sid == "__system_audio__" {
                                action = Some(PickerAction::FollowDefault);
                            } else {
                                action = Some(PickerAction::Toggle(sid.clone()));
                            }
                        }
                    }
                    ui.add_space(4.0);
                });
            if should_close {
                self.open = false;
            }
        }
        action
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum PickerAction {
    FollowDefault,
    Toggle(String),
}

/// Badge rotondo con lettera iniziale (port di DeviceBadge).
pub fn app_badge(ui: &mut Ui, name: &str, is_active: bool, t: &Tokens) {
    let size = egui::vec2(design::ROW_CONTENT_HEIGHT, design::ROW_CONTENT_HEIGHT);
    let (rect, _resp) = ui.allocate_exact_size(size, Sense::hover());
    let letter = name.chars().next().unwrap_or('?').to_uppercase().to_string();
    let p = ui.painter();
    let center = rect.center();
    let r = rect.width() / 2.0;
    if is_active {
        p.circle_filled(center, r, t.accent);
        p.text(
            center,
            Align2::CENTER_CENTER,
            &letter,
            FontId::proportional(14.0),
            Color32::WHITE,
        );
    } else {
        p.circle_filled(center, r, t.badge_mono_fill);
        p.text(
            center,
            Align2::CENTER_CENTER,
            &letter,
            FontId::proportional(14.0),
            t.badge_mono_fg,
        );
    }
}

/// Cursor a "pointing hand" su hover (per tutta la riga cliccabile).
pub fn row_cursor(ui: &mut Ui, resp: &Response) {
    if resp.hovered() {
        ui.output_mut(|o| o.cursor_icon = CursorIcon::PointingHand);
    }
}