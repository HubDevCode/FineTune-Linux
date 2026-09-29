// FineTune Linux — persistenza per-app (port di SettingsManager.swift).
// Unico file JSON ~/.config/finetune/settings.json, salvataggio atomico.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

pub const EQ_BANDS: usize = 10;
pub const EQ_FREQ: [f32; 10] = [31.25, 62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0];
pub const EQ_MAX_DB: f32 = 12.0;

pub fn flat_gains() -> [f32; EQ_BANDS] {
    [0.0; EQ_BANDS]
}

/// Preset EQ integrati (port da Models/EQPreset.swift).
pub fn builtin_presets() -> Vec<(&'static str, [f32; EQ_BANDS])> {
    vec![
        ("Flat", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        ("Bass Boost", [6.0, 6.0, 5.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        ("Bass Cut", [-6.0, -5.0, -4.0, -2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        ("Treble Boost", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 4.0, 5.0, 6.0]),
        ("Vocal Clarity", [-4.0, -2.0, -1.0, -3.0, 0.0, 2.0, 4.0, 4.0, 1.0, 0.0]),
        ("Podcast", [-6.0, -4.0, -2.0, -1.0, 0.0, 2.0, 4.0, 3.0, 1.0, 0.0]),
        ("Spoken Word", [-8.0, -6.0, -3.0, -2.0, 0.0, 2.0, 4.0, 4.0, 2.0, 0.0]),
        ("Loudness", [5.0, 4.0, 2.0, 0.0, -2.0, -2.0, 0.0, 2.0, 4.0, 5.0]),
        ("Late Night", [-6.0, -4.0, -2.0, 0.0, 0.0, 1.0, 2.0, 2.0, 1.0, 0.0]),
        ("Small Speakers", [3.0, 4.0, 5.0, 2.0, 0.0, 1.0, 2.0, 2.0, 1.0, 0.0]),
        ("Rock", [4.0, 3.0, 2.0, 0.0, -1.0, 0.0, 2.0, 3.0, 2.0, 1.0]),
        ("Pop", [3.0, 3.0, 2.0, 0.0, -1.0, 1.0, 2.0, 3.0, 3.0, 4.0]),
        ("Electronic", [7.0, 6.0, 4.0, 0.0, -2.0, -2.0, 1.0, 3.0, 4.0, 3.0]),
        ("Jazz", [3.0, 2.0, 1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 1.0]),
        ("Classical", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 2.0]),
        ("Hip-Hop", [6.0, 5.0, 4.0, 0.0, -1.0, 0.0, 2.0, 3.0, 4.0, 3.0]),
        ("R&B", [4.0, 4.0, 3.0, 1.0, -1.0, 0.0, 2.0, 3.0, 3.0, 2.0]),
        ("Deep", [5.0, 6.0, 4.0, 1.0, -2.0, -2.0, 0.0, 1.0, 2.0, 1.0]),
        ("Acoustic", [0.0, 1.0, 2.0, 2.0, 1.0, 0.0, 1.0, 2.0, 2.0, 1.0]),
        ("Movie", [4.0, 4.0, 3.0, -1.0, -1.0, 1.0, 3.0, 3.0, 2.0, 1.0]),
    ]
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct EqSettings {
    pub band_gains: Vec<f32>,
    pub is_enabled: bool,
}

impl EqSettings {
    pub fn flat() -> Self {
        Self {
            band_gains: flat_gains().to_vec(),
            is_enabled: false,
        }
    }
    pub fn from_gains(gains: [f32; EQ_BANDS]) -> Self {
        Self {
            band_gains: gains.to_vec(),
            is_enabled: true,
        }
    }
    pub fn normalized(&self) -> Vec<f32> {
        let mut v: Vec<f32> = self.band_gains.iter().take(EQ_BANDS).copied().collect();
        while v.len() < EQ_BANDS {
            v.push(0.0);
        }
        for g in v.iter_mut() {
            *g = g.clamp(-EQ_MAX_DB, EQ_MAX_DB);
            if g.is_nan() {
                *g = 0.0;
            }
        }
        v
    }
    pub fn is_flat(&self) -> bool {
        self.normalized().iter().all(|g| *g == 0.0)
    }
}

impl Default for EqSettings {
    fn default() -> Self {
        Self::flat()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserEqPreset {
    pub name: String,
    pub band_gains: Vec<f32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct Settings {
    pub version: u32,
    /// Identità app -> volume base (0..1).
    pub app_volumes: HashMap<String, f32>,
    pub app_mutes: HashMap<String, bool>,
    /// Identità app -> boost 1..4.
    pub app_boosts: HashMap<String, f32>,
    /// Identità app -> sink id (assente = segue default).
    pub app_routing: HashMap<String, String>,
    /// Identità app -> lista sink (multi-routing).
    pub app_selected_devices: HashMap<String, Vec<String>>,
    pub app_eq: HashMap<String, EqSettings>,
    /// App bloccate (in ordine di pin).
    pub pinned_apps: Vec<String>,
    pub ignored_apps: Vec<String>,
    /// Ordine manuale dei device (sink / source).
    pub output_device_priority: Vec<String>,
    pub input_device_priority: Vec<String>,
    pub hidden_output_devices: Vec<String>,
    pub hidden_input_devices: Vec<String>,
    pub user_eq_presets: Vec<UserEqPreset>,
    pub default_new_app_volume: f32,
    pub appearance: String,
    pub popup_size: String,
    pub volume_hotkey_step: String,
    pub media_key_control_enabled: bool,
}

impl Settings {
    fn fresh() -> Self {
        Self {
            version: 1,
            default_new_app_volume: 1.0,
            appearance: "system".into(),
            popup_size: "comfortable".into(),
            volume_hotkey_step: "normal".into(),
            media_key_control_enabled: true,
            ..Default::default()
        }
    }

    pub fn prune_stale(&mut self, live_keys: &[String]) {
        let keep: Vec<&str> = self
            .pinned_apps
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>();
        for key in self
            .app_volumes
            .keys()
            .cloned()
            .collect::<Vec<_>>()
        {
            let default = !live_keys.contains(&key)
                && !keep.contains(&key.as_str())
                && self.app_volumes.get(&key) == Some(&1.0)
                && self.app_mutes.get(&key).copied().unwrap_or(false) == false
                && self.app_boosts.get(&key).copied().unwrap_or(1.0) == 1.0
                && !self.app_routing.contains_key(&key)
                && self.app_eq.get(&key).map(|e| e.is_flat()).unwrap_or(true);
            if default {
                self.app_volumes.remove(&key);
                self.app_mutes.remove(&key);
                self.app_boosts.remove(&key);
                self.app_routing.remove(&key);
                self.app_eq.remove(&key);
            }
        }
    }

    pub fn reset_all(&mut self) {
        *self = Self::fresh();
    }
}

pub struct SettingsStore {
    pub path: PathBuf,
    pub settings: Settings,
}

impl SettingsStore {
    pub fn load() -> Self {
        let path =
            std::env::var("HOME").map_or_else(|_| PathBuf::from("/tmp/finetune"), |h| {
                PathBuf::from(h).join(".config/finetune/settings.json")
            });
        let settings = match fs::read_to_string(&path) {
            Ok(raw) => serde_json::from_str(&raw).unwrap_or_else(|_| {
                // file corrotto -> backup
                let backup = path.with_extension("json.backup");
                let _ = fs::copy(&path, &backup);
                Settings::fresh()
            }),
            Err(_) => Settings::fresh(),
        };
        Self { path, settings }
    }

    pub fn save(&self) {
        if let Some(dir) = self.path.parent() {
            let _ = fs::create_dir_all(dir);
        }
        let raw = serde_json::to_string_pretty(&self.settings).unwrap_or_default();
        let tmp = self.path.with_extension("json.tmp");
        if fs::write(&tmp, raw).is_ok() {
            let _ = fs::rename(&tmp, &self.path);
        }
    }

    // ---- accesso per chiave app ----

    pub fn app_key(&self) -> String {
        // identità desktop (nomi app), vedi models
        // segnaposto: chiamare con gettr parametrico
        String::new()
    }

    pub fn volume(&self, key: &str) -> Option<f32> {
        self.settings.app_volumes.get(key).copied()
    }
    pub fn set_volume(&mut self, key: &str, v: f32) {
        self.settings
            .app_volumes
            .insert(key.to_string(), v.clamp(0.0, 1.0));
    }
    pub fn remove_volume(&mut self, key: &str) {
        self.settings.app_volumes.remove(key);
    }

    pub fn muted(&self, key: &str) -> bool {
        self.settings.app_mutes.get(key).copied().unwrap_or(false)
    }
    pub fn set_muted(&mut self, key: &str, m: bool) {
        if m {
            self.settings.app_mutes.insert(key.to_string(), true);
        } else {
            self.settings.app_mutes.remove(key);
        }
    }

    pub fn boost(&self, key: &str) -> f32 {
        self.settings.app_boosts.get(key).copied().unwrap_or(1.0)
    }
    pub fn set_boost(&mut self, key: &str, b: f32) {
        let b = b.clamp(1.0, 4.0);
        if b == 1.0 {
            self.settings.app_boosts.remove(key);
        } else {
            self.settings.app_boosts.insert(key.to_string(), b);
        }
    }

    pub fn routing(&self, key: &str) -> Option<String> {
        self.settings.app_routing.get(key).cloned()
    }
    /// ritorna (removed, previous)
    pub fn set_routing(&mut self, key: &str, sink_id: Option<String>) -> bool {
        if let Some(s) = sink_id {
            self.settings.app_routing.insert(key.to_string(), s);
            true
        } else {
            self.settings.app_routing.remove(key).is_some()
        }
    }

    pub fn selected_devices(&self, key: &str) -> Vec<String> {
        self.settings
            .app_selected_devices
            .get(key)
            .cloned()
            .unwrap_or_default()
    }
    pub fn set_selected_devices(&mut self, key: &str, list: Vec<String>) {
        if list.is_empty() {
            self.settings.app_selected_devices.remove(key);
        } else {
            self.settings
                .app_selected_devices
                .insert(key.to_string(), list);
        }
    }

    pub fn eq(&self, key: &str) -> EqSettings {
        self.settings
            .app_eq
            .get(key)
            .cloned()
            .unwrap_or_else(EqSettings::flat)
    }
    pub fn set_eq(&mut self, key: &str, eq: EqSettings) {
        if eq.is_flat() && !eq.is_enabled {
            self.settings.app_eq.remove(key);
        } else {
            self.settings.app_eq.insert(key.to_string(), eq);
        }
    }

    pub fn is_pinned(&self, key: &str) -> bool {
        self.settings.pinned_apps.iter().any(|k| k == key)
    }
    pub fn set_pinned(&mut self, key: &str, pin: bool) {
        if pin {
            if !self.is_pinned(key) {
                self.settings.pinned_apps.push(key.to_string());
            }
        } else {
            self.settings.pinned_apps.retain(|k| k != key);
        }
    }
    pub fn is_ignored(&self, key: &str) -> bool {
        self.settings.ignored_apps.iter().any(|k| k == key)
    }
    pub fn set_ignored(&mut self, key: &str, ign: bool) {
        if ign {
            if !self.is_ignored(key) {
                self.settings.ignored_apps.push(key.to_string());
            }
        } else {
            self.settings.ignored_apps.retain(|k| k != key);
        }
    }

    // ---- priorità device (ordine in base al nome) ----

    pub fn device_priority(&self, input: bool) -> &[String] {
        if input {
            &self.settings.input_device_priority
        } else {
            &self.settings.output_device_priority
        }
    }
    pub fn set_device_priority(&mut self, input: bool, order: Vec<String>) {
        if input {
            self.settings.input_device_priority = order;
        } else {
            self.settings.output_device_priority = order;
        }
    }
    pub fn is_device_hidden(&self, input: bool, name: &str) -> bool {
        let list = if input {
            &self.settings.hidden_input_devices
        } else {
            &self.settings.hidden_output_devices
        };
        list.iter().any(|k| k == name)
    }
    pub fn set_device_hidden(&mut self, input: bool, name: &str, hide: bool) {
        let mut list = if input {
            self.settings.hidden_input_devices.clone()
        } else {
            self.settings.hidden_output_devices.clone()
        };
        if hide {
            if !list.iter().any(|k| k == name) {
                list.push(name.to_string());
            }
        } else {
            list.retain(|k| k != name);
        }
        if input {
            self.settings.hidden_input_devices = list;
        } else {
            self.settings.hidden_output_devices = list;
        }
    }
}