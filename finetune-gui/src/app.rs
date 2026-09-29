// FineTune Linux — stato e layout della GUI (port di MenuBarPopupView.swift).

use std::collections::HashMap;

use eframe::egui::{
    self, Align, Frame, Id, Layout, Margin, Pos2, Rect, RichText, Sense, Stroke, Vec2,
};

use finetune_backend_pipewire::PipeWireBackend;
use finetune_core::backend::{
    AudioBackend, AudioInput, AudioSink, AudioStream, BackendError,
};

use crate::design::{self, Tokens};
use crate::settings::{EqSettings, SettingsStore, UserEqPreset};
use crate::widgets::{self, DevicePicker, PickerAction, VuMeter};

const REFRESH_SECS: f64 = 1.5;

#[derive(Clone, Copy, PartialEq)]
enum Tab {
    Output,
    Input,
}

fn sink_label(s: &AudioSink) -> String {
    if s.description.is_empty() {
        s.name.clone()
    } else {
        s.description.clone()
    }
}

/// Identità stabile per la persistenza: binary name del processo, fallback nome app.
fn stream_identity(s: &AudioStream) -> String {
    if s.pid != 0 {
        if let Ok(path) = std::fs::read_link(format!("/proc/{}/exe", s.pid)) {
            if let Some(base) = path.file_name() {
                let b = base.to_string_lossy().into_owned();
                if !b.is_empty() {
                    return b;
                }
            }
        }
    }
    if !s.app_name.is_empty() {
        s.app_name.clone()
    } else {
        format!("stream:{}", s.id)
    }
}

pub struct FineTuneApp {
    backend: PipeWireBackend,
    pub settings: SettingsStore,
    tab: Tab,
    sinks: Vec<AudioSink>,
    inputs: Vec<AudioInput>,
    streams: Vec<AudioStream>,
    error: Option<String>,
    expanded_eq: Option<String>,
    pickers: HashMap<String, DevicePicker>,
    editing_priority: bool,
    show_settings: bool,
    last_refresh: f64,
    last_stream_count: usize,
    vu: HashMap<String, VuMeter>,
    needs_save: bool,
}

impl FineTuneApp {
    pub fn new() -> Self {
        let mut app = Self {
            backend: PipeWireBackend::new(),
            settings: SettingsStore::load(),
            tab: Tab::Output,
            sinks: Vec::new(),
            inputs: Vec::new(),
            streams: Vec::new(),
            error: None,
            expanded_eq: None,
            pickers: HashMap::new(),
            editing_priority: false,
            show_settings: false,
            last_refresh: 0.0,
            last_stream_count: 0,
            vu: HashMap::new(),
            needs_save: false,
        };
        app.refresh();
        app
    }

    fn set_err(&mut self, e: BackendError) {
        self.error = Some(e.to_string());
    }

    pub fn refresh(&mut self) {
        match self.backend.list_sinks() {
            Ok(sinks) => {
                self.sinks = sinks;
                self.error = None;
            }
            Err(e) => self.set_err(e),
        }
        match self.backend.list_inputs() {
            Ok(inputs) => self.inputs = inputs,
            Err(e) => self.set_err(e),
        }
        match self.backend.list_streams() {
            Ok(streams) => {
                self.last_stream_count = streams.len();
                self.streams = streams;
            }
            Err(e) => self.set_err(e),
        }
        if self.error.is_none() {
            let live_keys: Vec<String> = self
                .streams
                .iter()
                .map(stream_identity)
                .collect::<Vec<_>>();
            self.settings.settings.prune_stale(&live_keys);
        }
        if self.needs_save {
            self.settings.save();
            self.needs_save = false;
        }
    }

    fn mark_save(&mut self) {
        self.needs_save = true;
    }

    // ---- accesso ai valori effettivi per uno stream ----

    fn key(&self, s: &AudioStream) -> String {
        stream_identity(s)
    }

    fn eff_volume(&self, s: &AudioStream) -> f32 {
        self.settings.volume(&self.key(s)).unwrap_or(s.volume).clamp(0.0, 1.0)
    }

    fn eff_boost(&self, s: &AudioStream) -> f32 {
        let b = self.settings.boost(&self.key(s));
        if b != 1.0 {
            b
        } else {
            let q = s.boost.round().clamp(1.0, 4.0);
            q
        }
    }

    fn eff_muted(&self, s: &AudioStream) -> bool {
        let stored = self.settings.muted(&self.key(s));
        stored || (!self.settings.settings.app_mutes.contains_key(&self.key(s)) && s.muted)
    }

    fn default_sink_id(&self) -> Option<String> {
        self.sinks.iter().find(|s| s.is_default).map(|s| s.id.clone())
    }

    fn current_links(&self, s: &AudioStream) -> Vec<String> {
        if let Some(r) = self.settings.routing(&self.key(s)) {
            vec![r]
        } else {
            let multi = self.settings.selected_devices(&self.key(s));
            if !multi.is_empty() {
                multi
            } else {
                s.linked_sink_ids.iter().map(|id| id.to_string()).collect()
            }
        }
    }

    // ---- azioni ----

    fn set_stream_volume(&mut self, s: &AudioStream, vol: f32) {
        let key = self.key(s);
        self.settings.set_volume(&key, vol);
        let boost = self.settings.boost(&key).clamp(1.0, 4.0);
        if let Err(e) = self.backend.set_stream_volume_with_boost(&s.id, vol, boost) {
            self.set_err(e);
        }
        if vol > 0.0 && self.settings.muted(&key) {
            self.settings.set_muted(&key, false);
            let _ = self.backend.set_stream_mute(&s.id, false);
        }
        self.mark_save();
    }

    fn set_stream_boost(&mut self, s: &AudioStream, boost: f32) {
        let key = self.key(s);
        self.settings.set_boost(&key, boost);
        let vol = self.eff_volume(s);
        if let Err(e) = self.backend.set_stream_volume_with_boost(&s.id, vol, boost) {
            self.set_err(e);
        }
        self.mark_save();
    }

    fn set_stream_mute(&mut self, s: &AudioStream, muted: bool) {
        let key = self.key(s);
        self.settings.set_muted(&key, muted);
        if let Err(e) = self.backend.set_stream_mute(&s.id, muted) {
            self.set_err(e);
        }
        if !muted {
            let vol = self.eff_volume(s);
            if vol <= 0.001 {
                self.settings.set_volume(&key, 1.0);
                let _ = self
                    .backend
                    .set_stream_volume_with_boost(&s.id, 1.0, self.settings.boost(&key));
            }
        }
        self.mark_save();
    }

    fn route_stream(&mut self, s: &AudioStream, action: PickerAction) {
        let key = self.key(s);
        let multi = self
            .pickers
            .get(&key)
            .map(|p| p.mode_multi)
            .unwrap_or(false);
        let default_id = self.default_sink_id().unwrap_or_else(|| s.id.clone());

        let target: Option<Vec<String>> = match (multi, action) {
            (false, PickerAction::FollowDefault) => {
                self.settings.set_routing(&key, None);
                self.settings.set_selected_devices(&key, vec![]);
                Some(vec![default_id])
            }
            (false, PickerAction::Toggle(sink)) => {
                self.settings.set_routing(&key, Some(sink.clone()));
                self.settings.set_selected_devices(&key, vec![]);
                Some(vec![sink])
            }
            (true, PickerAction::FollowDefault) => {
                self.settings.set_routing(&key, None);
                self.settings.set_selected_devices(&key, vec![]);
                Some(vec![default_id])
            }
            (true, PickerAction::Toggle(sink)) => {
                let mut list = self.settings.selected_devices(&key);
                if list.is_empty() {
                    // parte dal routing corrente
                    list = self.current_links(s);
                }
                if let Some(pos) = list.iter().position(|x| x == &sink) {
                    list.remove(pos);
                } else {
                    list.push(sink);
                }
                list.sort();
                list.dedup();
                if list.is_empty() {
                    self.settings.set_selected_devices(&key, vec![]);
                    self.settings.set_routing(&key, None);
                    Some(vec![default_id])
                } else {
                    self.settings.set_selected_devices(&key, list.clone());
                    Some(list)
                }
            }
        };

        if let Some(target) = target {
            let target_ids: Vec<u32> = target
                .iter()
                .filter_map(|x| x.parse::<u32>().ok())
                .collect();
            if let Err(e) = self.backend.set_stream_links(&s.id, &target) {
                self.set_err(e);
            } else {
                // aggiorna la cache locale: il refresh successivo non deve
                // ripristinare lo stato mostrato (sincronizzazione con l'audio).
                if let Some(cached) = self.streams.iter_mut().find(|cs| cs.id == s.id) {
                    cached.linked_sink_ids = target_ids;
                }
            }
        }
        self.mark_save();
    }

    // ---- resilienza connessione device della riga ----

    fn save_now(&mut self) {
        self.settings.save();
        self.needs_save = false;
    }

    // ---- UI per i device ----

    fn device_rows(&mut self, ui: &mut egui::Ui, t: &Tokens) {
        let input_tab = self.tab == Tab::Input;
        let editing = self.editing_priority;

        if !editing {
            ui.label(
                RichText::new(if input_tab {
                    "Tap a device to set it as default input"
                } else {
                    "Tap a device to set it as default output"
                })
                .size(9.0)
                .color(t.text_quaternary),
            );
            ui.add_space(design::S_XS);
        }

        // ordine da priorità salvata
        let prio = self.settings.device_priority(input_tab).to_vec();
        let mut order_ids: Vec<String> = Vec::new();

        let items: Vec<(String, String, bool, bool)> = if input_tab {
            self.inputs
                .iter()
                .map(|i| {
                    (
                        i.id.clone(),
                        if i.description.is_empty() {
                            i.name.clone()
                        } else {
                            i.description.clone()
                        },
                        i.is_default,
                        self.settings.is_device_hidden(true, &i.name),
                    )
                })
                .collect()
        } else {
            self.sinks
                .iter()
                .map(|s| {
                    (
                        s.id.clone(),
                        sink_label(s),
                        s.is_default,
                        self.settings.is_device_hidden(false, &s.name),
                    )
                })
                .collect()
        };

        // costruzione ordine: priorità salvata per nome, poi il resto
        let mut rest: Vec<(String, String, bool, bool)> = Vec::new();
        for item in items {
            if prio.iter().any(|p| *p == item.1) {
                order_ids.push(item.0.clone());
            } else {
                rest.push(item);
            }
        }
        let mut sorted: Vec<(String, String, bool, bool)> = Vec::new();
        for name in prio.iter() {
            if let Some(item) = rest.iter().find(|i| &i.1 == name) {
                sorted.push(item.clone());
            }
        }
        sorted.extend(rest.clone());
        // i device visibili: tutto tranne nascosti; fallback a tutto se vuoto
        let shown: Vec<(String, String, bool, bool)> = {
            let visible: Vec<_> = sorted.iter().filter(|(_, _, _, hidden)| !*hidden).cloned().collect();
            if visible.is_empty() {
                sorted.clone()
            } else {
                visible
            }
        };

        if shown.is_empty() {
            ui.label(RichText::new("Nessun dispositivo disponibile.").weak().small());
            ui.add_space(4.0);
            return;
        }

        let n = shown.len();
        for (idx, (id, label, is_default, _hidden)) in shown.iter().cloned().enumerate() {
            if editing {
                ui.horizontal(|ui| {
                    let can_up = idx > 0;
                    let can_down = idx + 1 < n;
                    if ui
                        .add_enabled(can_up, egui::Button::new(RichText::new("▲").size(10.0)))
                        .on_disabled_hover_text("")
                        .clicked()
                    {
                        self.move_priority(input_tab, &label, -1);
                    }
                    if ui
                        .add_enabled(can_down, egui::Button::new(RichText::new("▼").size(10.0)))
                        .clicked()
                    {
                        self.move_priority(input_tab, &label, 1);
                    }
                    ui.label(RichText::new(label).size(13.0));
                    ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                        if ui
                            .button(RichText::new(if _hidden {
                                "Nascosto"
                            } else {
                                "Nascondi"
                            }).size(10.0))
                            .clicked()
                        {
                            let name = if input_tab {
                                self.inputs.iter().find(|i| i.id == id).map(|i| i.name.clone())
                            } else {
                                self.sinks.iter().find(|i| i.id == id).map(|i| i.name.clone())
                            };
                            if let Some(nm) = name {
                                self.settings.set_device_hidden(input_tab, &nm, !_hidden);
                                self.mark_save();
                            }
                        }
                    });
                });
                ui.add_space(2.0);
                continue;
            }

            if input_tab {
                if let Some(input) = self.inputs.iter().find(|i| i.id == id).cloned() {
                    self.input_row(ui, t, &input, is_default);
                }
            } else if let Some(sink) = self.sinks.iter().find(|i| i.id == id).cloned() {
                self.sink_row(ui, t, &sink, is_default);
            }
        }
    }

    fn move_priority(&mut self, input_tab: bool, name: &str, delta: i32) {
        let prio = self.settings.device_priority(input_tab).to_vec();
        let mut order = prio.clone();
        if order.iter().all(|p| p != name) {
            order.push(name.to_string());
        }
        let pos = order.iter().position(|p| p == name).unwrap();
        let new_pos = (pos as i32 + delta).clamp(0, order.len() as i32 - 1) as usize;
        if new_pos != pos {
            order.swap(pos, new_pos);
        }
        self.settings.set_device_priority(input_tab, order);
        self.mark_save();
    }

    fn sink_row(&mut self, ui: &mut egui::Ui, t: &Tokens, sink: &AudioSink, is_default: bool) {
        let name = sink_label(sink);
        self.device_row_ui(
            ui,
            t,
            &sink.id,
            &name,
            sink.volume,
            sink.muted,
            is_default,
            false,
            |s, value, muted| {
                if let Some(v) = value {
                    let _ = s.backend.set_sink_volume(&sink.id, v);
                }
                if let Some(m) = muted {
                    let _ = s.backend.set_sink_mute(&sink.id, m);
                }
                // aggiorna la cache così il valore resta (niente revert al refresh)
                if let Some(sk) = s.sinks.iter_mut().find(|x| x.id == sink.id) {
                    if let Some(v) = value {
                        sk.volume = v;
                    }
                    if let Some(m) = muted {
                        sk.muted = m;
                    }
                }
            },
        );
    }

    fn input_row(&mut self, ui: &mut egui::Ui, t: &Tokens, input: &AudioInput, is_default: bool) {
        let name = if input.description.is_empty() {
            input.name.clone()
        } else {
            input.description.clone()
        };
        self.device_row_ui(
            ui,
            t,
            &input.id,
            &name,
            input.volume,
            input.muted,
            is_default,
            true,
            |s, value, muted| {
                if let Some(v) = value {
                    let _ = s.backend.set_input_volume(&input.id, v);
                }
                if let Some(m) = muted {
                    let _ = s.backend.set_input_mute(&input.id, m);
                }
                if let Some(inp) = s.inputs.iter_mut().find(|x| x.id == input.id) {
                    if let Some(v) = value {
                        inp.volume = v;
                    }
                    if let Some(m) = muted {
                        inp.muted = m;
                    }
                }
            },
        );
    }

    #[allow(clippy::too_many_arguments)]
    fn device_row_ui(
        &mut self,
        ui: &mut egui::Ui,
        t: &Tokens,
        id: &str,
        label: &str,
        volume: f32,
        muted: bool,
        is_default: bool,
        is_input: bool,
        mut apply: impl FnMut(&mut Self, Option<f32>, Option<bool>),
    ) {
        let mut set_default = false;
        // contenuto della riga (hover dipinto PRIMA -> resta dietro; qualsiasi tap = default)
        let row_band = ui.scope(|ui| {
            let avail = ui.available_rect_before_wrap();
            let band = Rect::from_min_size(
                avail.min,
                Vec2::new(avail.width(), design::ROW_CONTENT_HEIGHT),
            );
            let hovered = ui.rect_contains_pointer(band.expand(2.0));
            if hovered {
                ui.painter().rect_filled(band.expand(2.0), design::row_corner(), t.hover_surface);
            }
            let band_resp = ui.interact(band, Id::new(("band", id)), Sense::click());
            // band click -> imposta default (i controlli sopra intercettano i loro click)
            if band_resp.clicked() {
                set_default = true;
            }
            ui.horizontal(|ui| {
                ui.spacing_mut().item_spacing.x = design::S_SM;
                // icona dispositivo + spunta default (tap = imposta default)
                let (badge_rect, badge_resp) =
                    ui.allocate_exact_size(Vec2::splat(design::ROW_CONTENT_HEIGHT), Sense::click());
                if badge_resp.clicked() && !is_default {
                    set_default = true;
                }
                let g = crate::icons::IconDrawer::new(ui);
                let badge_icon = Rect::from_center_size(badge_rect.center(), Vec2::splat(14.0));
                let icon_color = if is_default { t.accent } else { t.badge_mono_fg };
                if is_input {
                    g.mic(badge_icon, false, icon_color);
                } else {
                    g.speaker(badge_icon, 0.5, icon_color);
                }
                if is_default {
                    let check_rect = Rect::from_center_size(
                        Pos2::new(badge_rect.right() - 3.0, badge_rect.bottom() - 3.0),
                        Vec2::splat(9.0),
                    );
                    crate::icons::IconDrawer::new(ui).check(check_rect, t.accent);
                }
                let mut name_text = RichText::new(label).size(13.0);
                if is_default {
                    name_text = name_text.strong();
                }
                ui.add(egui::Label::new(name_text).truncate());
                if is_default {
                    ui.label(
                        RichText::new("Default")
                            .size(9.0)
                            .color(t.accent),
                    );
                }
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    // % editabile
                    let mut pct_vol = volume;
                    if widgets::editable_percentage(ui, &mut pct_vol, t) && (pct_vol - volume).abs() > 0.001 {
                        apply(self, Some(pct_vol), Some(false));
                    }
                    ui.add_space(4.0);
                    // slider
                    let mut v = volume;
                    let resp = widgets::GlassSlider { width: design::SLIDER_WIDTH }.ui(ui, &mut v, t);
                    if (resp.dragged() || resp.drag_stopped()) && (v - volume).abs() > 0.001 {
                        apply(self, Some(v), Some(false));
                    }
                    ui.add_space(4.0);
                    // mute
                    if widgets::mute_button(ui, Id::new(("dm", id)), muted, volume, t).clicked() {
                        apply(self, None, Some(!muted));
                    }
                    ui.add_space(2.0);
                });
            });
            let _ = hovered;
        });

        if set_default {
            // tap sulla riga -> imposta default, poi refresh immediato
            if is_input {
                let _ = self.backend.set_default_input(id);
            } else {
                let _ = self.backend.set_default_sink(id);
            }
            self.last_refresh = f64::MIN;
        }
        let _ = row_band;
    }

    fn stream_row(&mut self, ui: &mut egui::Ui, t: &Tokens, s: &AudioStream) {
        let key = self.key(s);
        let name = if s.app_name.is_empty() {
            s.media_name.clone()
        } else {
            s.app_name.clone()
        };
        let volume = self.eff_volume(s);
        let boost = self.eff_boost(s);
        let muted = self.eff_muted(s);

        self.pickers.entry(key.clone()).or_insert_with(DevicePicker::new);
        let multi = self.pickers.get(&key).map(|p| p.mode_multi).unwrap_or(false);
        let follows_default = self.settings.routing(&key).is_none()
            && self.settings.selected_devices(&key).is_empty();
        let options: Vec<(String, String)> = std::iter::once((
            "__system_audio__".to_string(),
            "System Audio".to_string(),
        ))
        .chain(self.sinks.iter().map(|s| (s.id.clone(), sink_label(s))))
        .collect();
        let selected = self.current_links(s);

        let eq_open = self.expanded_eq.as_deref() == Some(key.as_str());
        let is_pinned = self.settings.is_pinned(&key);
        let is_ignored = self.settings.is_ignored(&key);

        let inner = ui.scope(|ui| {
            let avail = ui.available_rect_before_wrap();
            let band = Rect::from_min_size(
                avail.min,
                Vec2::new(avail.width(), design::ROW_CONTENT_HEIGHT),
            );
            if ui.rect_contains_pointer(band.expand(2.0)) {
                ui.painter().rect_filled(band.expand(2.0), design::row_corner(), t.hover_surface);
            }
            ui.horizontal(|ui| {
                ui.spacing_mut().item_spacing.x = design::S_SM;
                // VU meter
                let vu = self.vu.entry(key.clone()).or_insert_with(VuMeter::new);
                let vu_rect = Rect::from_min_size(ui.cursor().min, Vec2::new(10.0, design::ROW_CONTENT_HEIGHT - 4.0));
                vu.draw(ui, vu_rect, muted, t);
                ui.add_space(0.0);
                // icona app
                widgets::app_badge(ui, &name, true, t);
                ui.add(egui::Label::new(RichText::new(name).size(13.0).strong()).truncate());
                // sottotitolo routing
                if !follows_default {
                    let sub = if multi {
                        if selected.len() == 1 {
                            format!(
                                "Multi · {}",
                                self.sinks
                                    .iter()
                                    .find(|x| x.id == selected[0])
                                    .map(sink_label)
                                    .unwrap_or_else(|| selected[0].clone())
                            )
                        } else if selected.len() > 1 {
                            format!("Multi · {} devices", selected.len())
                        } else {
                            "Multi".to_string()
                        }
                    } else {
                        self.sinks
                            .iter()
                            .find(|x| selected.contains(&x.id))
                            .map(sink_label)
                            .unwrap_or_else(|| "Multi".to_string())
                    };
                    ui.label(RichText::new(sub).size(9.0).color(t.text_tertiary));
                }
                let _ = is_pinned;
                let _ = is_ignored;

                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    ui.spacing_mut().item_spacing.x = design::S_XS;
                    // bottone EQ
                    let eq_color = if eq_open {
                        t.interactive_active
                    } else if ui.rect_contains_pointer(Rect::from_min_size(ui.cursor().min, Vec2::splat(design::ROW_CONTENT_HEIGHT))) {
                        t.interactive_hover
                    } else {
                        t.interactive_default
                    };
                    let eq_btn = ui.allocate_exact_size(Vec2::splat(design::ROW_CONTENT_HEIGHT), Sense::click());
                    if ui.is_rect_visible(eq_btn.0) {
                        let er = Rect::from_center_size(eq_btn.0.center(), Vec2::splat(15.0));
                        if eq_open {
                            crate::icons::IconDrawer::new(ui).xmark(er, eq_color);
                        } else {
                            crate::icons::IconDrawer::new(ui).equalizer(er, eq_color);
                        }
                    }
                    if eq_btn.1.clicked() {
                        if eq_open {
                            self.expanded_eq = None;
                        } else {
                            self.expanded_eq = Some(key.clone());
                        }
                    }
                    // picker device
                    if let Some(action) = self
                        .pickers
                        .get_mut(&key)
                        .unwrap()
                        .ui(
                            ui,
                            Id::new(("dp", &key)),
                            &options,
                            &selected,
                            follows_default,
                            t,
                        )
                    {
                        self.route_stream(s, action);
                    }
                    // boost chevrons
                    let b = boost.round() as u8;
                    if let Some(next) = widgets::boost_chevrons(ui, b, t) {
                        self.set_stream_boost(s, next as f32);
                    }
                    // % editabile
                    let mut pct_vol = volume;
                    if widgets::editable_percentage(ui, &mut pct_vol, t) && (pct_vol - volume).abs() > 0.001 {
                        if pct_vol > 0.0 && muted {
                            self.set_stream_mute(s, false);
                        }
                        self.set_stream_volume(s, pct_vol);
                    }
                    ui.add_space(2.0);
                    // slider
                    let mut v = volume;
                    let resp = widgets::GlassSlider { width: design::SLIDER_WIDTH }.ui(ui, &mut v, t);
                    if (resp.dragged() || resp.drag_stopped()) && (v - volume).abs() > 0.001 {
                        if v > 0.0 && muted {
                            self.set_stream_mute(s, false);
                        }
                        self.set_stream_volume(s, v);
                    }
                    ui.add_space(4.0);
                    // mute
                    if widgets::mute_button(ui, Id::new(("sm", &key)), muted, volume, t).clicked() {
                        if muted {
                            // attualmente muto -> alzo volume se era 0, poi unmuta
                            if volume <= 0.001 {
                                self.set_stream_volume(s, 1.0);
                            }
                            self.set_stream_mute(s, false);
                        } else {
                            self.set_stream_mute(s, true);
                        }
                    }
                });
            });
        });
        let _ = inner;

        // pannello EQ espandibile
        if eq_open {
            ui.add_space(design::S_XS);
            self.eq_panel(ui, t, s);
        }
        ui.add_space(design::S_SM);
    }

    fn eq_panel(&mut self, ui: &mut egui::Ui, t: &Tokens, s: &AudioStream) {
        let key = self.key(s);
        let mut eq = self.settings.eq(&key);
        Frame::group(ui.style())
            .inner_margin(Margin::symmetric(10, 10))
            .corner_radius(design::row_corner())
            .fill(t.recessed)
            .stroke(Stroke::new(1.0, t.glass_row_border))
            .show(ui, |ui| {
                let builtin = crate::settings::builtin_presets();
                let user_presets = self.settings.settings.user_eq_presets.clone();
                let current_name = self.current_preset_name(&eq);
                // header: toggle EQ + preset picker + save/rename
                ui.horizontal(|ui| {
                    ui.toggle_value(&mut eq.is_enabled, "EQ");
                    ui.label(RichText::new("Preset").weak().small());
                    let changed_preset = egui::ComboBox::from_id_salt(("eqpreset", &key))
                        .selected_text(if current_name.is_empty() {
                            "Custom".to_string()
                        } else {
                            current_name.clone()
                        })
                        .width(130.0)
                        .show_ui(ui, |ui| {
                            let mut picked: Option<String> = None;
                            for (name, _gains) in &builtin {
                                if ui.selectable_label(current_name == *name, *name).clicked() {
                                    picked = Some(name.to_string());
                                }
                            }
                            for up in &user_presets {
                                if ui.selectable_label(current_name == up.name, up.name.clone()).clicked()
                                {
                                    picked = Some(up.name.clone());
                                }
                            }
                            picked
                        });
                    if let Some(picked) = changed_preset.inner {
                        if let Some(picked) = picked {
                            if let Some((_, gains)) = builtin.iter().find(|(n, _)| *n == picked) {
                                eq.band_gains = (*gains).to_vec();
                            } else if let Some(up) = user_presets.iter().find(|u| u.name == picked) {
                                eq.band_gains = up.band_gains.clone();
                            }
                            eq.is_enabled = true;
                        }
                    }
                    // salva come preset (solo se custom)
                    if current_name.is_empty()
                        && ui
                            .button(RichText::new("+ Salva").size(10.0))
                            .on_hover_text("Salva come preset")
                            .clicked()
                    {
                        let base = "Custom";
                        let mut name = base.to_string();
                        let mut i = 2;
                        while user_presets.iter().any(|p| p.name == name) {
                            name = format!("{base} ({i})");
                            i += 1;
                        }
                        self.settings.settings.user_eq_presets.push(UserEqPreset {
                            name,
                            band_gains: eq.normalized(),
                        });
                        self.mark_save();
                    }
                });
                ui.add_space(6.0);

                // 10 slider verticali
                let gains = eq.normalized();
                let mut new_gains = gains.clone();
                ui.horizontal(|ui| {
                    ui.spacing_mut().item_spacing.x = design::S_SM;
                    for (i, freq) in crate::settings::EQ_FREQ.iter().enumerate() {
                        ui.vertical(|ui| {
                            let resp = ui.add(
                                egui::Slider::new(&mut new_gains[i], -12.0..=12.0)
                                    .vertical()
                                    .show_value(true)
                                    .custom_formatter(|v, _| format!("{v:+.0}")),
                            );
                            if resp.changed() {
                            }
                            ui.label(
                                RichText::new(format_freq(*freq))
                                    .size(9.0)
                                    .monospace()
                                    .color(t.text_tertiary),
                            );
                            ui.label(RichText::new("Hz").size(8.0).color(t.text_quaternary));
                        });
                    }
                });
                if new_gains != gains {
                    eq.band_gains = new_gains.clone();
                }
                eq.band_gains = eq.normalized();
                ui.add_space(4.0);
                ui.label(
                    RichText::new("Impostazioni EQ salvate per questa app (DSP in fase di porting).")
                        .size(9.0)
                        .color(t.text_tertiary),
                );
                let stored = self.settings.eq(&key);
                if stored != eq {
                    let clone = eq.clone();
                    self.settings.set_eq(&key, clone);
                    self.mark_save();
                }
            });
    }

    fn current_preset_name(&self, eq: &EqSettings) -> String {
        let gains = eq.normalized();
        for (n, g) in crate::settings::builtin_presets() {
            if g.to_vec() == gains {
                return n.to_string();
            }
        }
        for up in &self.settings.settings.user_eq_presets {
            if up.band_gains == gains {
                return up.name.clone();
            }
        }
        String::new()
    }

    fn apps_section(&mut self, ui: &mut egui::Ui, t: &Tokens) {
        section_header(ui, "Apps", t);
        let ignored = self.settings.settings.ignored_apps.len();
        if ignored > 0 && !self.editing_priority {
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                ui.label(RichText::new(format!("{ignored} ignored")).size(10.0).color(t.text_tertiary));
            });
        }
        ui.add_space(design::S_XS);

        if self.streams.is_empty() {
            ui.add_space(10.0);
            let center_x = ui.cursor().min.x + ui.available_width() / 2.0;
            let icon_rect = Rect::from_center_size(
                Pos2::new(center_x, ui.cursor().min.y + 14.0),
                Vec2::splat(24.0),
            );
            crate::icons::IconDrawer::new(ui).speaker_muted(icon_rect, t.text_tertiary);
            ui.add_space(26.0);
            ui.vertical_centered(|ui| {
                ui.label(RichText::new("No apps playing audio").size(13.0).color(t.text_secondary));
                ui.label(
                    RichText::new(format!("{ignored} ignored · edit to manage"))
                        .size(10.0)
                        .color(t.text_tertiary),
                );
            });
            ui.add_space(20.0);
            return;
        }

        let streams = self.streams.clone();
        for s in &streams {
            self.stream_row(ui, t, s);
            ui.add_space(design::S_SM);
        }
    }
}

fn section_header(ui: &mut egui::Ui, title: &str, t: &Tokens) {
    ui.label(
        RichText::new(title.to_uppercase())
            .size(12.0)
            .strong()
            .color(t.section_header_text),
    );
}

fn format_freq(f: f32) -> String {
    if f >= 1000.0 {
        format!("{}k", (f / 1000.0) as u32)
    } else {
        format!("{}", f as u32)
    }
}

impl eframe::App for FineTuneApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        // spaziature "Liquid Glass"
        let spacing = &mut ui.spacing_mut();
        spacing.item_spacing = egui::vec2(design::S_SM, 6.0);
        spacing.button_padding = egui::vec2(8.0, 4.0);

        let dark = match self.settings.settings.appearance.as_str() {
            "light" => false,
            "dark" => true,
            _ => ui.visuals().dark_mode,
        };
        let t = Tokens::new(dark);
        let t = &t;

        Frame::central_panel(ui.style())
            .inner_margin(Margin::symmetric(design::CONTENT_PADDING as i8, 12))
            .fill(t.window_bg)
            .show(ui, |ui| {
                ui.set_max_width(design::POPUP_WIDTH - 2.0 * design::CONTENT_PADDING);

                let now = ui.input(|i| i.time);
                let pointer_down = ui.input(|i| i.pointer.any_down());
                if !pointer_down && now - self.last_refresh >= REFRESH_SECS {
                    self.last_refresh = now;
                    self.refresh();
                }
                // salvataggio differito
                if self.needs_save && now - self.last_refresh >= 0.0 {
                    self.settings.save();
                    self.needs_save = false;
                }

                self.header(ui, t);
                ui.add_space(design::S_XS);

                if let Some(err) = &self.error {
                    ui.colored_label(egui::Color32::from_rgb(220, 60, 60), err);
                    ui.add_space(design::S_XS);
                }

                // il footer è ancorato in basso: riserviamo l'altezza del footer e
                // facciamo riempire alla scrollarea lo spazio rimanente.
                let scroll_avail = ui.available_height();
                let scroll_h = (scroll_avail - design::FOOTER_RESERVE).max(design::MIN_SCROLL_HEIGHT);

                egui::ScrollArea::vertical()
                    .max_height(scroll_h)
                    .min_scrolled_height(scroll_h)
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        ui.set_min_width(ui.available_width());
                        section_header(ui, if self.tab == Tab::Output { "Devices" } else { "Input" }, t);
                        ui.add_space(design::S_XS);
                        self.device_rows(ui, t);
                        ui.add_space(design::S_SM);
                        ui.separator();
                        ui.add_space(design::S_SM);
                        self.apps_section(ui, t);
                        ui.add_space(design::S_MD);
                        if self.show_settings {
                            ui.separator();
                            ui.add_space(design::S_SM);
                            self.settings_panel(ui, t);
                            ui.add_space(design::S_SM);
                        }
                    });

                ui.separator();
                // footer
                ui.horizontal(|ui| {
                    let donate = ui
                        .button(RichText::new("♥ Donate").size(12.0))
                        .on_hover_text("Supporta FineTune su ko-fi");
                    if donate.clicked() {
                        let _ = std::process::Command::new("xdg-open")
                            .arg("https://ko-fi.com/")
                            .spawn();
                    }
                    ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                        if ui
                            .button(RichText::new("Quit").size(10.0).color(t.text_secondary))
                            .clicked()
                        {
                            ui.ctx().send_viewport_cmd(egui::ViewportCommand::Close);
                        }
                    });
                });
            });
    }
}

impl FineTuneApp {
    fn header(&mut self, ui: &mut egui::Ui, t: &Tokens) {
        ui.horizontal(|ui| {
            // pill Output/Input
            let pill_h = 26.0;
            let tab_rect = ui.allocate_exact_size(Vec2::new(112.0, pill_h), Sense::hover()).0;
            let seg_w = tab_rect.width() / 2.0;
            ui.painter().rect_filled(
                tab_rect,
                design::radius(design::BUTTON_RADIUS + 3),
                t.glass_fill,
            );
            ui.painter().rect_stroke(
                tab_rect,
                design::radius(design::BUTTON_RADIUS + 3),
                Stroke::new(0.5, t.glass_row_border),
                egui::StrokeKind::Middle,
            );
            for (tab, is_input) in [(Tab::Output, false), (Tab::Input, true)] {
                let seg_n = match tab {
                    Tab::Output => 0u8,
                    Tab::Input => 1u8,
                };
                let seg = if seg_n == 0 {
                    Rect::from_min_size(tab_rect.min, Vec2::new(seg_w, pill_h))
                } else {
                    Rect::from_min_max(
                        Pos2::new(tab_rect.center().x, tab_rect.top()),
                        tab_rect.max,
                    )
                };
                let resp = ui.interact(seg, Id::new(("tab", seg_n)), Sense::click());
                let active = self.tab == tab;
                if active {
                    ui.painter().rect_filled(
                        seg,
                        design::radius(design::BUTTON_RADIUS),
                        t.glass_fill_strong,
                    );
                }
                let icon_rect = Rect::from_center_size(seg.center(), Vec2::splat(15.0));
                let color = if active {
                    t.interactive_active
                } else {
                    t.interactive_default
                };
                let g = crate::icons::IconDrawer::new(ui);
                if is_input {
                    g.mic(icon_rect, false, color);
                } else {
                    g.speaker(icon_rect, 0.9, color);
                }
                if resp.clicked() {
                    if self.tab != tab {
                        self.tab = tab;
                        self.expanded_eq = None;
                    }
                }
            }
            ui.add_space(design::S_SM);

            // stato device di default
            if !self.editing_priority {
                let out = self.sinks.iter().find(|s| s.is_default).map(sink_label);
                let inp = self.inputs.iter().find(|i| i.is_default).map(|i| {
                    if i.description.is_empty() {
                        i.name.clone()
                    } else {
                        i.description.clone()
                    }
                });
                let text = match (out, inp) {
                    (Some(o), Some(i)) => format!("{o} · {i}"),
                    (Some(o), None) => o,
                    (None, Some(i)) => i,
                    (None, None) => "—".to_string(),
                };
                ui.add(egui::Label::new(
                    RichText::new(text).size(11.0).color(t.text_secondary),
                ).truncate());
            } else {
                ui.label(RichText::new("Usa le frecce per ordinare la priorità").size(11.0).color(t.text_secondary));
            }

            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                // settings
                let gear = ui.allocate_exact_size(Vec2::splat(24.0), Sense::click());
                if ui.is_rect_visible(gear.0) {
                    crate::icons::IconDrawer::new(ui).gear(
                        Rect::from_center_size(gear.0.center(), Vec2::splat(15.0)),
                        t.interactive_default,
                    );
                }
                if gear.1.clicked() {
                    self.show_settings = !self.show_settings;
                }
                ui.add_space(design::S_XS);
                // edit priority pencil/check
                let edit = ui.allocate_exact_size(Vec2::splat(24.0), Sense::click());
                if ui.is_rect_visible(edit.0) {
                    let color = if self.editing_priority {
                        t.interactive_active
                    } else {
                        t.interactive_default
                    };
                    let er = Rect::from_center_size(edit.0.center(), Vec2::splat(14.0));
                    if self.editing_priority {
                        crate::icons::IconDrawer::new(ui).check(er, color);
                    } else {
                        crate::icons::IconDrawer::new(ui).pencil(er, color);
                    }
                }
                if edit.1.clicked() {
                    self.editing_priority = !self.editing_priority;
                    self.expanded_eq = None;
                }
            });
        });
    }

    fn settings_panel(&mut self, ui: &mut egui::Ui, t: &Tokens) {
        ui.add_space(design::S_SM);
        Frame::group(ui.style())
            .inner_margin(Margin::symmetric(12, 10))
            .corner_radius(design::row_corner())
            .fill(t.recessed)
            .stroke(Stroke::new(1.0, t.glass_row_border))
            .show(ui, |ui| {
                ui.label(RichText::new("Impostazioni").strong());
                ui.add_space(design::S_XS);

                let mut appearance = self.settings.settings.appearance.clone();
                egui::ComboBox::from_id_salt("appearance")
                    .selected_text(appearance.as_str())
                    .show_ui(ui, |ui| {
                        for a in ["system", "light", "dark"] {
                            ui.selectable_value(&mut appearance, a.to_string(), a);
                        }
                    });
                if appearance != self.settings.settings.appearance {
                    self.settings.settings.appearance = appearance;
                    self.mark_save();
                }

                let mut def_vol = self.settings.settings.default_new_app_volume;
                ui.horizontal(|ui| {
                    ui.label(RichText::new("Volume nuove app").size(11.0).color(t.text_secondary));
                    ui.add_sized(
                        egui::vec2(140.0, 18.0),
                        egui::Slider::new(&mut def_vol, 0.0..=1.0).show_value(true),
                    );
                });
                if def_vol != self.settings.settings.default_new_app_volume {
                    self.settings.settings.default_new_app_volume = def_vol;
                    self.mark_save();
                }
            });
    }
}