use std::collections::{BTreeMap, HashSet};
use std::io;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::autoeq::AutoEQSelection;
use crate::models::{BoostLevel, DeviceSelectionMode, EQSettings};
use crate::settings::settings::{data_dir, Settings};
use crate::settings::types::{
    IgnoredAppInfo, PinnedAppInfo, UserEQPreset, VolumeControlTier,
};

/// Debounce di salvataggio: scrive 500 ms dopo l'ultima mutazione (port di
/// `SettingsManager.scheduleSave`).
const DEBOUNCE_MS: u64 = 500;
/// Tetto massimo di attesa del worker di salvataggio.
const MAX_WAIT_MS: u64 = 2000;

/// Errori del manager di persistenza.
#[derive(Debug)]
pub enum SettingsError {
    Io(io::Error),
    Json(serde_json::Error),
}

impl std::fmt::Display for SettingsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SettingsError::Io(e) => write!(f, "I/O error: {e}"),
            SettingsError::Json(e) => write!(f, "JSON error: {e}"),
        }
    }
}

impl std::error::Error for SettingsError {}

impl From<io::Error> for SettingsError {
    fn from(e: io::Error) -> Self {
        SettingsError::Io(e)
    }
}

impl From<serde_json::Error> for SettingsError {
    fn from(e: serde_json::Error) -> Self {
        SettingsError::Json(e)
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

struct Inner {
    settings: Mutex<Settings>,
    path: PathBuf,
    dirty_gen: AtomicU64,
    written_gen: AtomicU64,
    last_mutation_ms: AtomicU64,
    worker_active: AtomicBool,
    #[allow(dead_code)]
    loading: AtomicBool,
}

/// Gestore delle impostazioni: debounce di salvataggio, scrittura atomica,
/// backup dei file corrotti. Port di `SettingsManager`.
pub struct SettingsManager {
    inner: Arc<Inner>,
}

impl SettingsManager {
    pub fn new(directory: Option<&Path>) -> Self {
        let dir = directory.map(Path::to_owned).unwrap_or_else(data_dir);
        let path = dir.join("settings.json");
        let inner = Arc::new(Inner {
            settings: Mutex::new(Settings::default()),
            path,
            dirty_gen: AtomicU64::new(0),
            written_gen: AtomicU64::new(0),
            last_mutation_ms: AtomicU64::new(0),
            worker_active: AtomicBool::new(false),
            loading: AtomicBool::new(false),
        });
        let manager = SettingsManager { inner };
        manager.load_from_disk();
        manager
    }

    pub fn path(&self) -> &Path {
        &self.inner.path
    }

    pub fn get_settings(&self) -> Settings {
        self.inner.settings.lock().unwrap().clone()
    }

    // MARK: - Per-App Volumes

    pub fn get_volume(&self, identifier: &str) -> Option<f32> {
        self.inner.settings.lock().unwrap().app_volumes.get(identifier).copied()
    }

    pub fn set_volume(&self, identifier: &str, volume: f32) {
        let v = if volume.is_finite() { volume.clamp(0.0, 1.0) } else { 1.0 };
        self.inner.settings.lock().unwrap().app_volumes.insert(identifier.to_string(), v);
        self.schedule_save();
    }

    pub fn get_boost(&self, identifier: &str) -> Option<BoostLevel> {
        let s = self.inner.settings.lock().unwrap();
        s.app_boosts.get(identifier).and_then(|&raw| BoostLevel::from_raw_value(raw))
    }

    pub fn set_boost(&self, identifier: &str, boost: BoostLevel) {
        self.inner.settings.lock().unwrap().app_boosts.insert(identifier.to_string(), boost.raw_value());
        self.schedule_save();
    }

    pub fn get_device_routing(&self, identifier: &str) -> Option<String> {
        self.inner.settings.lock().unwrap().app_device_routing.get(identifier).cloned()
    }

    pub fn set_device_routing(&self, identifier: &str, device_id: &str) {
        self.inner.settings.lock().unwrap().app_device_routing.insert(identifier.to_string(), device_id.to_string());
        self.schedule_save();
    }

    /// true se l'app segue il default di sistema (nessuna destinazione esplicita).
    pub fn is_following_default(&self, identifier: &str) -> bool {
        self.inner.settings.lock().unwrap().app_device_routing.get(identifier).is_none()
    }

    pub fn set_follow_default(&self, identifier: &str) {
        self.inner.settings.lock().unwrap().app_device_routing.remove(identifier);
        self.schedule_save();
    }

    pub fn get_mute(&self, identifier: &str) -> Option<bool> {
        self.inner.settings.lock().unwrap().app_mutes.get(identifier).copied()
    }

    pub fn set_mute(&self, identifier: &str, muted: bool) {
        self.inner.settings.lock().unwrap().app_mutes.insert(identifier.to_string(), muted);
        self.schedule_save();
    }

    pub fn get_eq_settings(&self, identifier: &str) -> EQSettings {
        self.inner
            .settings
            .lock()
            .unwrap()
            .app_eq_settings
            .get(identifier)
            .cloned()
            .unwrap_or_else(EQSettings::flat)
    }

    pub fn set_eq_settings(&self, identifier: &str, eq: &EQSettings) {
        self.inner.settings.lock().unwrap().app_eq_settings.insert(identifier.to_string(), eq.clone());
        self.schedule_save();
    }

    pub fn get_device_selection_mode(&self, identifier: &str) -> Option<DeviceSelectionMode> {
        self.inner.settings.lock().unwrap().app_device_selection_mode.get(identifier).copied()
    }

    pub fn set_device_selection_mode(&self, identifier: &str, mode: DeviceSelectionMode) {
        self.inner.settings.lock().unwrap().app_device_selection_mode.insert(identifier.to_string(), mode);
        self.schedule_save();
    }

    pub fn get_selected_device_ids(&self, identifier: &str) -> Vec<String> {
        self.inner.settings.lock().unwrap().app_selected_device_ids.get(identifier).cloned().unwrap_or_default()
    }

    pub fn set_selected_device_ids(&self, identifier: &str, ids: Vec<String>) {
        let mut ids: Vec<String> = ids;
        ids.sort();
        ids.dedup();
        // Nota: le UID dei sink sono già quelle del dominio PipeWire.
        self.inner.settings.lock().unwrap().app_selected_device_ids.insert(identifier.to_string(), ids);
        self.schedule_save();
    }

    // MARK: - Input Lock

    pub fn locked_input_device_id(&self) -> Option<String> {
        self.inner.settings.lock().unwrap().locked_input_device_id.clone()
    }

    pub fn set_locked_input_device_id(&self, id: Option<String>) {
        self.inner.settings.lock().unwrap().locked_input_device_id = id;
        self.schedule_save();
    }

    pub fn preferred_input_device_id(&self) -> Option<String> {
        self.inner.settings.lock().unwrap().preferred_input_device_id.clone()
    }

    pub fn set_preferred_input_device_id(&self, id: Option<String>) {
        self.inner.settings.lock().unwrap().preferred_input_device_id = id;
        self.schedule_save();
    }

    // MARK: - Pinned Apps

    pub fn pin_app(&self, identifier: &str, info: PinnedAppInfo) {
        let mut s = self.inner.settings.lock().unwrap();
        s.pinned_apps.insert(identifier.to_string());
        s.pinned_app_info.insert(identifier.to_string(), info);
        drop(s);
        self.schedule_save();
    }

    pub fn unpin_app(&self, identifier: &str) {
        let mut s = self.inner.settings.lock().unwrap();
        s.pinned_apps.remove(identifier);
        s.pinned_app_info.remove(identifier);
        drop(s);
        self.schedule_save();
    }

    pub fn is_pinned(&self, identifier: &str) -> bool {
        self.inner.settings.lock().unwrap().pinned_apps.contains(identifier)
    }

    pub fn pinned_app_info(&self) -> Vec<PinnedAppInfo> {
        let s = self.inner.settings.lock().unwrap();
        s.pinned_apps.iter().filter_map(|id| s.pinned_app_info.get(id).cloned()).collect()
    }

    // MARK: - Ignored Apps

    pub fn ignore_app(&self, identifier: &str, info: IgnoredAppInfo) {
        let mut s = self.inner.settings.lock().unwrap();
        s.ignored_apps.insert(identifier.to_string());
        s.ignored_app_info.insert(identifier.to_string(), info);
        s.pinned_apps.remove(identifier);
        s.pinned_app_info.remove(identifier);
        s.app_volumes.remove(identifier);
        s.app_boosts.remove(identifier);
        s.app_mutes.remove(identifier);
        s.app_device_routing.remove(identifier);
        s.app_eq_settings.remove(identifier);
        s.app_device_selection_mode.remove(identifier);
        s.app_selected_device_ids.remove(identifier);
        drop(s);
        self.schedule_save();
    }

    pub fn unignore_app(&self, identifier: &str) {
        let mut s = self.inner.settings.lock().unwrap();
        s.ignored_apps.remove(identifier);
        s.ignored_app_info.remove(identifier);
        drop(s);
        self.schedule_save();
    }

    pub fn is_ignored(&self, identifier: &str) -> bool {
        self.inner.settings.lock().unwrap().ignored_apps.contains(identifier)
    }

    pub fn ignored_app_info(&self) -> Vec<IgnoredAppInfo> {
        let s = self.inner.settings.lock().unwrap();
        s.ignored_apps.iter().filter_map(|id| s.ignored_app_info.get(id).cloned()).collect()
    }

    // MARK: - DDC / Software device volume
    // Sul kernel Linux i tier DDC/software possono non applicarsi, ma lo schema
    // di persistenza resta fedele all'originale per forward-compatibility.

    pub fn get_ddc_volume(&self, device_id: &str) -> Option<i32> {
        self.inner.settings.lock().unwrap().ddc_volumes.get(device_id).copied()
    }

    pub fn set_ddc_volume(&self, device_id: &str, volume: i32) {
        self.inner.settings.lock().unwrap().ddc_volumes.insert(device_id.to_string(), volume.clamp(0, 100));
        self.schedule_save();
    }

    pub fn get_software_device_volume(&self, device_id: &str) -> Option<f32> {
        self.inner.settings.lock().unwrap().software_device_volumes.get(device_id).copied()
    }

    pub fn set_software_device_volume(&self, device_id: &str, volume: f32) {
        let v = if volume.is_finite() { volume.clamp(0.0, 1.0) } else { 1.0 };
        self.inner.settings.lock().unwrap().software_device_volumes.insert(device_id.to_string(), v);
        self.schedule_save();
    }

    pub fn get_software_device_mute_state(&self, device_id: &str) -> bool {
        self.inner.settings.lock().unwrap().software_device_mute_states.get(device_id).copied().unwrap_or(false)
    }

    pub fn set_software_device_mute_state(&self, device_id: &str, muted: bool) {
        self.inner.settings.lock().unwrap().software_device_mute_states.insert(device_id.to_string(), muted);
        self.schedule_save();
    }

    pub fn get_software_device_saved_volume(&self, device_id: &str) -> Option<f32> {
        self.inner.settings.lock().unwrap().software_device_saved_volumes.get(device_id).copied()
    }

    pub fn set_software_device_saved_volume(&self, device_id: &str, volume: f32) {
        let v = if volume.is_finite() { volume.clamp(0.0, 1.0) } else { 1.0 };
        self.inner.settings.lock().unwrap().software_device_saved_volumes.insert(device_id.to_string(), v);
        self.schedule_save();
    }

    // MARK: - Tier Override / Icon Override

    pub fn get_device_volume_tier_override(&self, device_id: &str) -> Option<VolumeControlTier> {
        self.inner.settings.lock().unwrap().device_volume_tier_override.get(device_id).copied()
    }

    pub fn set_device_volume_tier_override(&self, device_id: &str, tier: Option<VolumeControlTier>) {
        let mut s = self.inner.settings.lock().unwrap();
        if let Some(t) = tier {
            s.device_volume_tier_override.insert(device_id.to_string(), t);
        } else {
            s.device_volume_tier_override.remove(device_id);
        }
        drop(s);
        self.schedule_save();
    }

    pub fn get_device_icon_override(&self, device_id: &str) -> Option<String> {
        self.inner.settings.lock().unwrap().device_icon_overrides.get(device_id).cloned()
    }

    pub fn set_device_icon_override(&self, device_id: &str, icon: Option<String>) {
        let mut s = self.inner.settings.lock().unwrap();
        if let Some(i) = icon {
            s.device_icon_overrides.insert(device_id.to_string(), i);
        } else {
            s.device_icon_overrides.remove(device_id);
        }
        drop(s);
        self.schedule_save();
    }

    // MARK: - Priority

    pub fn device_priority_order(&self) -> Vec<String> {
        self.inner.settings.lock().unwrap().output_device_priority.clone()
    }

    pub fn ensure_device_in_priority(&self, id: &str) {
        let mut s = self.inner.settings.lock().unwrap();
        if !s.output_device_priority.iter().any(|x| x == id) {
            s.output_device_priority.push(id.to_string());
            drop(s);
            self.schedule_save();
        }
    }

    pub fn input_device_priority_order(&self) -> Vec<String> {
        self.inner.settings.lock().unwrap().input_device_priority.clone()
    }

    pub fn ensure_input_device_in_priority(&self, id: &str) {
        let mut s = self.inner.settings.lock().unwrap();
        if !s.input_device_priority.iter().any(|x| x == id) {
            s.input_device_priority.push(id.to_string());
            drop(s);
            self.schedule_save();
        }
    }

    /// Pure function: fonde l'ordine ricomposto dei dispositivi connessi nella
    /// lista completa, preservando le posizioni dei disconnessi (ancoraggio).
    pub fn merge_priority_order(old_priority: &[String], connected_order: &[String]) -> Vec<String> {
        let connected: HashSet<&String> = connected_order.iter().collect();

        let mut anchored_groups: BTreeMap<Option<String>, Vec<String>> = BTreeMap::new();
        let mut current_anchor: Option<String> = None;
        for id in old_priority {
            if connected.contains(id) {
                current_anchor = Some(id.clone());
            } else {
                anchored_groups.entry(current_anchor.clone()).or_default().push(id.clone());
            }
        }

        let mut result = Vec::new();
        if let Some(prefix) = anchored_groups.remove(&None) {
            result.extend(prefix);
        }
        for id in connected_order {
            result.push(id.clone());
            if let Some(group) = anchored_groups.remove(&Some(id.clone())) {
                result.extend(group);
            }
        }
        result
    }

    // MARK: - System Sounds

    pub fn is_system_sounds_following_default(&self) -> bool {
        self.inner.settings.lock().unwrap().system_sounds_follows_default
    }

    pub fn set_system_sounds_follow_default(&self, follows: bool) {
        self.inner.settings.lock().unwrap().system_sounds_follows_default = follows;
        self.schedule_save();
    }

    // MARK: - App Settings

    pub fn app_settings(&self) -> crate::settings::settings::AppSettings {
        self.inner.settings.lock().unwrap().app_settings.clone()
    }

    pub fn update_app_settings<F>(&self, mutate: F)
    where
        F: FnOnce(&mut crate::settings::settings::AppSettings),
    {
        let mut s = self.inner.settings.lock().unwrap();
        mutate(&mut s.app_settings);
        if !s.app_settings.default_new_app_volume.is_finite() || s.app_settings.default_new_app_volume < 0.0 {
            s.app_settings.default_new_app_volume = 1.0;
        }
        drop(s);
        self.schedule_save();
    }

    // MARK: - AutoEQ

    pub fn get_auto_eq_selection(&self, device_id: &str) -> Option<AutoEQSelection> {
        self.inner.settings.lock().unwrap().device_auto_eq.get(device_id).cloned()
    }

    pub fn set_auto_eq_selection(&self, device_id: &str, selection: Option<AutoEQSelection>) {
        let mut s = self.inner.settings.lock().unwrap();
        if let Some(sel) = selection {
            s.device_auto_eq.insert(device_id.to_string(), sel);
        } else {
            s.device_auto_eq.remove(device_id);
        }
        drop(s);
        self.schedule_save();
    }

    pub fn is_auto_eq_favorite(&self, id: &str) -> bool {
        self.inner.settings.lock().unwrap().favorite_auto_eq_profiles.contains(id)
    }

    pub fn favorite_auto_eq_profile(&self, id: &str) {
        self.inner.settings.lock().unwrap().favorite_auto_eq_profiles.insert(id.to_string());
        self.schedule_save();
    }

    pub fn unfavorite_auto_eq_profile(&self, id: &str) {
        self.inner.settings.lock().unwrap().favorite_auto_eq_profiles.remove(id);
        self.schedule_save();
    }

    pub fn auto_eq_preamp_enabled(&self) -> bool {
        self.inner.settings.lock().unwrap().auto_eq_preamp_enabled
    }

    pub fn set_auto_eq_preamp_enabled(&self, enabled: bool) {
        self.inner.settings.lock().unwrap().auto_eq_preamp_enabled = enabled;
        self.schedule_save();
    }

    // MARK: - User EQ Presets

    pub fn user_eq_presets(&self) -> Vec<UserEQPreset> {
        let mut v = self.inner.settings.lock().unwrap().user_eq_presets.clone();
        v.sort_by(|a, b| b.created_at.cmp(&a.created_at));
        v
    }

    /// Crea un preset EQ nominato: trims, fallback "Untitled", suffix " (2)", " (3)"...
    pub fn create_user_preset(&self, name: &str, eq_settings: &EQSettings) -> UserEQPreset {
        let trimmed = name.trim();
        let base = if trimmed.is_empty() { "Untitled" } else { trimmed };
        let final_name = self.unique_preset_name(base, None);
        let preset = UserEQPreset::new(&final_name, eq_settings.clone());
        self.inner.settings.lock().unwrap().user_eq_presets.push(preset.clone());
        self.schedule_save();
        preset
    }

    pub fn rename_user_preset(&self, id: &str, name: &str) {
        let trimmed = name.trim();
        if trimmed.is_empty() {
            return;
        }
        let mut s = self.inner.settings.lock().unwrap();
        let idx = s.user_eq_presets.iter().position(|p| p.id == id);
        if let Some(i) = idx {
            let existing: Vec<UserEQPreset> = s.user_eq_presets.clone();
            s.user_eq_presets[i].name = Self::unique_preset_name_inner(&existing, trimmed, Some(id));
            drop(s);
            self.schedule_save();
        }
    }

    fn unique_preset_name(&self, name: &str, exclude: Option<&str>) -> String {
        let presets = self.inner.settings.lock().unwrap().user_eq_presets.clone();
        Self::unique_preset_name_inner(&presets, name, exclude)
    }

    fn unique_preset_name_inner(presets: &[UserEQPreset], name: &str, exclude: Option<&str>) -> String {
        let existing: HashSet<&str> = presets
            .iter()
            .filter(|p| Some(p.id.as_str()) != exclude)
            .map(|p| p.name.as_str())
            .collect();
        if !existing.contains(name) {
            return name.to_string();
        }
        let mut counter = 2;
        loop {
            let candidate = format!("{name} ({counter})");
            if !existing.contains(candidate.as_str()) {
                return candidate;
            }
            counter += 1;
        }
    }

    pub fn delete_user_preset(&self, id: &str) {
        let mut s = self.inner.settings.lock().unwrap();
        s.user_eq_presets.retain(|p| p.id != id);
        drop(s);
        self.schedule_save();
    }

    // MARK: - Hidden Devices

    pub fn toggle_output_device_hidden(&self, id: &str) {
        let mut s = self.inner.settings.lock().unwrap();
        if !s.hidden_output_device_ids.remove(id) {
            s.hidden_output_device_ids.insert(id.to_string());
        }
        drop(s);
        self.schedule_save();
    }

    pub fn is_output_device_hidden(&self, id: &str) -> bool {
        self.inner.settings.lock().unwrap().hidden_output_device_ids.contains(id)
    }

    pub fn toggle_input_device_hidden(&self, id: &str) {
        let mut s = self.inner.settings.lock().unwrap();
        if !s.hidden_input_device_ids.remove(id) {
            s.hidden_input_device_ids.insert(id.to_string());
        }
        drop(s);
        self.schedule_save();
    }

    pub fn is_input_device_hidden(&self, id: &str) -> bool {
        self.inner.settings.lock().unwrap().hidden_input_device_ids.contains(id)
    }

    // MARK: - Pruning

    pub fn prune_stale_settings(&self, keeping: &HashSet<String>) {
        let mut s = self.inner.settings.lock().unwrap();
        let all: Vec<String> = s
            .app_volumes
            .keys()
            .chain(s.app_boosts.keys())
            .chain(s.app_mutes.keys())
            .chain(s.app_eq_settings.keys())
            .chain(s.app_device_selection_mode.keys())
            .chain(s.app_selected_device_ids.keys())
            .cloned()
            .collect();

        let mut pruned = 0;
        for id in all {
            if keeping.contains(&id) {
                continue;
            }
            if s.pinned_apps.contains(&id) {
                continue;
            }
            if s.app_device_routing.contains_key(&id) {
                continue;
            }
            let volume = s.app_volumes.get(&id).copied();
            let mute = s.app_mutes.get(&id).copied();
            let boost = s.app_boosts.get(&id).copied();
            let eq = s.app_eq_settings.get(&id);
            let mode = s.app_device_selection_mode.get(&id);
            let uids = s.app_selected_device_ids.get(&id);

            let is_default_volume = volume.is_none() || volume == Some(1.0);
            let is_default_boost = boost.is_none() || boost == Some(1.0);
            let is_default_mute = mute.is_none() || mute == Some(false);
            let is_default_eq = eq.is_none() || eq == Some(&EQSettings::flat());
            let is_default_mode = mode.is_none();
            let is_default_uids = uids.map(|u| u.is_empty()).unwrap_or(true);

            if is_default_volume
                && is_default_boost
                && is_default_mute
                && is_default_eq
                && is_default_mode
                && is_default_uids
            {
                s.app_volumes.remove(&id);
                s.app_boosts.remove(&id);
                s.app_mutes.remove(&id);
                s.app_eq_settings.remove(&id);
                s.app_device_selection_mode.remove(&id);
                s.app_selected_device_ids.remove(&id);
                pruned += 1;
            }
        }
        let do_save = pruned > 0;
        drop(s);
        if do_save {
            log::info!("Pruned {pruned} stale app settings entries");
            self.schedule_save();
        }
    }

    // MARK: - Reset

    pub fn reset_all_settings(&self) {
        *self.inner.settings.lock().unwrap() = Settings::default();
        self.schedule_save();
        log::info!("Reset all settings to defaults");
    }

    // MARK: - Persistence

    fn schedule_save(&self) {
        self.inner
            .last_mutation_ms
            .store(now_ms(), Ordering::Relaxed);
        self.inner.dirty_gen.fetch_add(1, Ordering::Relaxed);
        // Un solo worker alla volta; gli altri salvataggi si fondono nello stesso giro.
        if self.inner.worker_active.swap(true, Ordering::AcqRel) {
            return;
        }
        let inner = Arc::clone(&self.inner);
        std::thread::Builder::new()
            .name("settings-save".into())
            .spawn(move || save_worker(inner))
            .expect("spawn settings-save thread");
    }

    #[allow(dead_code)]
    fn flush_sync(&self) {
        let _ = self.write_to_disk();
        self.inner.written_gen.store(self.inner.dirty_gen.load(Ordering::Relaxed), Ordering::Relaxed);
    }

    fn load_from_disk(&self) {
        let path = &self.inner.path;
        if !path.exists() {
            return;
        }
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(e) => {
                log::error!("Failed to read settings {:?}: {e}", path);
                return;
            }
        };
        match serde_json::from_slice::<Settings>(&bytes) {
            Ok(s) => {
                *self.inner.settings.lock().unwrap() = s;
                log::debug!("Loaded settings from {:?}", path);
            }
            Err(e) => {
                log::error!("Failed to load settings: {e}");
                // Backup del file corrotto prima di ripartire con i default
                let backup = path.with_extension("backup.json");
                let _ = std::fs::remove_file(&backup);
                let _ = std::fs::copy(path, &backup);
                log::warn!("Backed up corrupted settings to {:?}", backup);
            }
        }
    }

    pub fn write_to_disk(&self) -> Result<(), SettingsError> {
        let snapshot = self.inner.settings.lock().unwrap().clone();
        let data = serde_json::to_vec_pretty(&snapshot)?;
        atomic_write(&self.inner.path, data)?;
        self.inner.written_gen.store(self.inner.dirty_gen.load(Ordering::Relaxed), Ordering::Relaxed);
        log::debug!("Saved settings to {:?}", self.inner.path);
        Ok(())
    }
}

fn save_worker(inner: Arc<Inner>) {
    let start = now_ms();
    // Debounce trailing: resta attivo finché l'ultima mutazione è vicina.
    loop {
        let last_mut = inner.last_mutation_ms.load(Ordering::Relaxed);
        if now_ms().saturating_sub(last_mut) >= DEBOUNCE_MS {
            break;
        }
        if now_ms().saturating_sub(start) >= MAX_WAIT_MS {
            break;
        }
        std::thread::sleep(Duration::from_millis(20));
    }

    let snapshot = inner.settings.lock().unwrap().clone();
    let data = serde_json::to_vec_pretty(&snapshot).expect("serialize settings");
    if let Err(e) = atomic_write(&inner.path, data) {
        log::error!("Failed to write settings {:?}: {e}", inner.path);
    } else {
        log::debug!("Saved settings to {:?}", inner.path);
    }
    inner.written_gen.store(inner.dirty_gen.load(Ordering::Relaxed), Ordering::Relaxed);
    inner.worker_active.store(false, Ordering::Release);
}

/// Scrittura atomica: temp file nella stessa directory + rename.
fn atomic_write(path: &Path, data: Vec<u8>) -> io::Result<()> {
    let dir = path.parent().unwrap_or_else(|| Path::new("."));
    std::fs::create_dir_all(dir)?;
    let tmp = dir.join(format!(
        ".settings-{}.tmp",
        std::process::id()
    ));
    std::fs::write(&tmp, &data)?;
    // fsync per durabilità (come Data.write(.atomic) su macOS)
    if let Ok(f) = std::fs::File::open(&tmp) {
        let _ = f.sync_all();
    }
    std::fs::rename(&tmp, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::types::ShortcutCodable;
    use serde_json::json;

    fn scratch() -> PathBuf {
        use std::sync::atomic::{AtomicU32, Ordering};
        static N: AtomicU32 = AtomicU32::new(0);
        let n = N.fetch_add(1, Ordering::Relaxed);
        let d = std::env::temp_dir().join(format!("finetune-settings-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn load_missing_file_returns_defaults() {
        let m = SettingsManager::new(Some(&scratch()));
        assert!(m.get_settings().app_volumes.is_empty());
        assert_eq!(m.app_settings().default_new_app_volume, 1.0);
    }

    #[test]
    fn set_and_flush_roundtrip() {
        let dir = scratch();
        let m = SettingsManager::new(Some(&dir));
        m.set_volume("firefox", 0.55);
        m.set_boost("firefox", BoostLevel::X2);
        m.set_mute("firefox", true);
        for i in 0..5u32 {
            m.set_volume("firefox", 0.44 + (i as f32) * 0.001);
        }
        m.flush_sync();

        let path = dir.join("settings.json");
        assert!(path.exists());

        let m2 = SettingsManager::new(Some(&dir));
        assert!((m2.get_volume("firefox").unwrap() - 0.444).abs() < 1e-3);
        assert_eq!(m2.get_boost("firefox"), Some(BoostLevel::X2));
        assert_eq!(m2.get_mute("firefox"), Some(true));
    }

    #[test]
    fn debounced_write_happens_within_budget() {
        let dir = scratch();
        let m = SettingsManager::new(Some(&dir));
        m.set_volume("app", 0.7);
        // Il worker annota sole delle generazioni; attesa al di sopra del debounce.
        std::thread::sleep(Duration::from_millis(1200));
        m.flush_sync();
        assert!(dir.join("settings.json").exists());
    }

    #[test]
    fn corrupted_file_is_backed_up() {
        let dir = scratch();
        let path = dir.join("settings.json");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&path, "{not json").unwrap();
        let m = SettingsManager::new(Some(&dir));
        assert!(m.get_settings().app_eq_settings.is_empty());
        assert!(path.with_extension("backup.json").exists());
    }

    #[test]
    fn merge_priority_preserves_disconnected_positions() {
        let old = vec!["a".into(), "lost1".into(), "b".into(), "lost2".into(), "c".into()];
        let connected = vec!["c".into(), "b".into(), "a".into()];
        let merged = SettingsManager::merge_priority_order(&old, &connected);
        // lost1 era ancorata ad "a" (fine ordine), lost2 a "b".
        assert!(merged.iter().position(|x| x == "c").unwrap()
            < merged.iter().position(|x| x == "b").unwrap());
        assert_eq!(merged.last().map(String::as_str), Some("lost1"));
    }

    #[test]
    fn user_preset_name_collisions() {
        let dir = scratch();
        let m = SettingsManager::new(Some(&dir));
        let eq = EQSettings::flat();
        let p1 = m.create_user_preset("Bass", &eq);
        let p2 = m.create_user_preset("Bass", &eq);
        let p3 = m.create_user_preset("Bass", &eq);
        assert_eq!(p1.name, "Bass");
        assert_eq!(p2.name, "Bass (2)");
        assert_eq!(p3.name, "Bass (3)");
        m.rename_user_preset(&p1.id, "Bass");
        // no-op decisione: nome identico → ok
        m.delete_user_preset(&p2.id);
        assert_eq!(m.user_eq_presets().len(), 2);
    }

    #[test]
    fn prune_stale_settings_removes_defaults() {
        let dir = scratch();
        let m = SettingsManager::new(Some(&dir));
        m.set_volume("deadbeef", 1.0);
        m.set_volume("keepeep", 0.75);
        let keeping: HashSet<String> = ["keepeep".into()].into_iter().collect();
        m.prune_stale_settings(&keeping);
        assert!(m.get_volume("deadbeef").is_none());
        assert_eq!(m.get_volume("keepeep"), Some(0.75));
    }

    #[test]
    fn app_settings_custom_shortcuts_persist() {
        let dir = scratch();
        let m = SettingsManager::new(Some(&dir));
        m.update_app_settings(|a| {
            a.custom_shortcuts.insert("toggleMute".to_string(), ShortcutCodable { key_code: 49, modifiers: 0 });
        });
        m.flush_sync();
        let m2 = SettingsManager::new(Some(&dir));
        let aps = m2.app_settings();
        assert_eq!(aps.custom_shortcuts["toggleMute"].key_code, 49);
    }

    #[test]
    fn settings_serializes_pretty_and_json_valid() {
        let dir = scratch();
        let m = SettingsManager::new(Some(&dir));
        m.set_device_routing("firefox", "sink-57");
        m.flush_sync();
        let raw = std::fs::read_to_string(dir.join("settings.json")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(v["appDeviceRouting"]["firefox"], json!("sink-57"));
    }
}