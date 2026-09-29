use std::collections::{BTreeSet, HashMap};

use serde::{Deserialize, Serialize};
use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::autoeq::AutoEQSelection;
use crate::models::{DeviceSelectionMode, EQSettings};
use crate::settings::types::{
    AppearancePreference, HUDStyle, IgnoredAppInfo, MenuBarIconStyle, MenuBarPopupSize,
    PinnedAppInfo, ShortcutCodable, UserEQPreset, VolumeControlTier, VolumeHotkeyStep,
};

/// Versione dello schema settings (allineata al macOS v12).
pub const SETTINGS_VERSION: i64 = 12;

/// Impostazioni generali dell'app. Port di `AppSettings`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct AppSettings {
    pub launch_at_login: bool,
    pub menu_bar_icon_style: MenuBarIconStyle,

    pub default_new_app_volume: f32,

    pub lock_input_device: bool,

    pub show_device_disconnect_alerts: bool,

    pub loudness_compensation_enabled: bool,
    pub loudness_equalization_enabled: bool,

    pub hud_style: HUDStyle,
    pub media_key_control_enabled: bool,
    pub volume_hotkey_step: VolumeHotkeyStep,

    /// Derivazione stabile delle shortcut globali (coppia keyCode+modifiers).
    pub custom_shortcuts: HashMap<String, ShortcutCodable>,

    pub appearance: AppearancePreference,

    pub popup_size: MenuBarPopupSize,
}

impl Default for AppSettings {
    fn default() -> Self {
        AppSettings {
            launch_at_login: false,
            menu_bar_icon_style: MenuBarIconStyle::Default,
            default_new_app_volume: 1.0,
            lock_input_device: true,
            show_device_disconnect_alerts: true,
            loudness_compensation_enabled: false,
            loudness_equalization_enabled: false,
            hud_style: HUDStyle::Tahoe,
            media_key_control_enabled: true,
            volume_hotkey_step: VolumeHotkeyStep::Normal,
            custom_shortcuts: HashMap::new(),
            appearance: AppearancePreference::System,
            popup_size: MenuBarPopupSize::Comfortable,
        }
    }
}

impl AppSettings {
    pub fn set_unified_loudness_enabled(&mut self, enabled: bool) {
        self.loudness_compensation_enabled = enabled;
        self.loudness_equalization_enabled = enabled;
    }
}

/// Set completo delle impostazioni persistite.
/// Port di `SettingsManager.Settings` (schema v12). Le chiavi per-app restano
/// `String` = persistence identifier (adattamento al dominio PipeWire).
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub version: i64,
    pub app_volumes: HashMap<String, f32>,
    pub app_device_routing: HashMap<String, String>,
    pub app_mutes: HashMap<String, bool>,
    pub app_boosts: HashMap<String, f32>,
    #[serde(rename = "appEQSettings")]
    pub app_eq_settings: HashMap<String, EQSettings>,
    pub app_settings: AppSettings,
    pub system_sounds_follows_default: bool,
    pub app_device_selection_mode: HashMap<String, DeviceSelectionMode>,
    #[serde(rename = "appSelectedDeviceUIDs")]
    pub app_selected_device_ids: HashMap<String, Vec<String>>,
    #[serde(rename = "lockedInputDeviceUID")]
    pub locked_input_device_id: Option<String>,
    #[serde(rename = "preferredInputDeviceUID")]
    pub preferred_input_device_id: Option<String>,
    pub pinned_apps: BTreeSet<String>,
    pub pinned_app_info: HashMap<String, PinnedAppInfo>,
    pub ignored_apps: BTreeSet<String>,
    pub ignored_app_info: HashMap<String, IgnoredAppInfo>,

    pub ddc_volumes: HashMap<String, i32>,
    pub ddc_mute_states: HashMap<String, bool>,
    pub ddc_saved_volumes: HashMap<String, i32>,

    pub software_device_volumes: HashMap<String, f32>,
    pub software_device_mute_states: HashMap<String, bool>,
    pub software_device_saved_volumes: HashMap<String, f32>,

    pub device_volume_tier_override: HashMap<String, VolumeControlTier>,
    pub device_icon_overrides: HashMap<String, String>,

    pub output_device_priority: Vec<String>,
    pub input_device_priority: Vec<String>,

    #[serde(rename = "hiddenOutputDeviceUIDs")]
    pub hidden_output_device_ids: BTreeSet<String>,
    #[serde(rename = "hiddenInputDeviceUIDs")]
    pub hidden_input_device_ids: BTreeSet<String>,

    #[serde(rename = "deviceAutoEQ")]
    pub device_auto_eq: HashMap<String, AutoEQSelection>,
    #[serde(rename = "favoriteAutoEQProfiles")]
    pub favorite_auto_eq_profiles: BTreeSet<String>,
    #[serde(rename = "autoEQPreampEnabled")]
    pub auto_eq_preamp_enabled: bool,

    pub user_eq_presets: Vec<UserEQPreset>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            version: SETTINGS_VERSION,
            app_volumes: HashMap::new(),
            app_device_routing: HashMap::new(),
            app_mutes: HashMap::new(),
            app_boosts: HashMap::new(),
            app_eq_settings: HashMap::new(),
            app_settings: AppSettings::default(),
            system_sounds_follows_default: true,
            app_device_selection_mode: HashMap::new(),
            app_selected_device_ids: HashMap::new(),
            locked_input_device_id: None,
            preferred_input_device_id: None,
            pinned_apps: BTreeSet::new(),
            pinned_app_info: HashMap::new(),
            ignored_apps: BTreeSet::new(),
            ignored_app_info: HashMap::new(),
            ddc_volumes: HashMap::new(),
            ddc_mute_states: HashMap::new(),
            ddc_saved_volumes: HashMap::new(),
            software_device_volumes: HashMap::new(),
            software_device_mute_states: HashMap::new(),
            software_device_saved_volumes: HashMap::new(),
            device_volume_tier_override: HashMap::new(),
            device_icon_overrides: HashMap::new(),
            output_device_priority: Vec::new(),
            input_device_priority: Vec::new(),
            hidden_output_device_ids: BTreeSet::new(),
            hidden_input_device_ids: BTreeSet::new(),
            device_auto_eq: HashMap::new(),
            favorite_auto_eq_profiles: BTreeSet::new(),
            auto_eq_preamp_enabled: true,
            user_eq_presets: Vec::new(),
        }
    }
}

fn is_finite_nonneg(v: &f32) -> bool {
    v.is_finite() && *v >= 0.0
}

/// Clamp volumi numerici salvati al range [0, min(1)] — come su macOS (il boost
/// per-app è separato; i volumi > 1 importati vengono troncati).
fn sanitize_volume_map(map: HashMap<String, f32>) -> HashMap<String, f32> {
    map.into_iter()
        .filter(|(_, v)| is_finite_nonneg(v))
        .map(|(k, v)| (k, v.min(1.0)))
        .collect()
}

impl<'de> Deserialize<'de> for Settings {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        use serde::de::Error;

        // Decode come Value e applica le stesse regole del decoder Swift:
        // chiavi mancanti → default; clamping sui volumi.
        let v = Value::deserialize(deserializer)?;
        let obj = v
            .as_object()
            .ok_or_else(|| D::Error::custom("settings must be an object"))?;

        let get = |key: &str| obj.get(key);

        fn parse_value<T>(obj: &serde_json::Map<String, Value>, key: &str) -> T
        where
            T: DeserializeOwned + Default,
        {
            obj.get(key)
                .and_then(|v| serde_json::from_value(v.clone()).ok())
                .unwrap_or_default()
        }

        /// Come il decoder Swift: le singole entrate non decodificabili vanno
        /// scartate, la mappa rimanente viene conservata (chiave `myKey` → `my_key`).
        fn parse_map_value<T>(obj: &serde_json::Map<String, Value>, key: &str) -> HashMap<String, T>
        where
            T: DeserializeOwned,
        {
            let mut out = HashMap::new();
            if let Some(Value::Object(map)) = obj.get(key) {
                for (k, v) in map {
                    if let Ok(item) = serde_json::from_value(v.clone()) {
                        out.insert(k.clone(), item);
                    }
                }
            }
            out
        }

        let app_volumes = sanitize_volume_map(parse_map_value(obj, "appVolumes"));
        let app_boosts: HashMap<String, f32> =
            parse_map_value::<f32>(obj, "appBoosts")
                .into_iter()
                .map(|(k, v)| (k, if v.is_finite() { v } else { 1.0 }))
                .collect();
        let software_volumes = sanitize_volume_map(parse_map_value(obj, "softwareDeviceVolumes"));
        let software_saved = sanitize_volume_map(parse_map_value(obj, "softwareDeviceSavedVolumes"));

        let mut decoded_app_settings: AppSettings = parse_value(obj, "appSettings");
        if !decoded_app_settings.default_new_app_volume.is_finite()
            || decoded_app_settings.default_new_app_volume < 0.0
        {
            decoded_app_settings.default_new_app_volume = 1.0;
        }

        let nonempty_str = |v: Option<&Value>| {
            v.and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(String::from)
        };

        Ok(Settings {
            version: get("version").and_then(|v| v.as_i64()).unwrap_or(SETTINGS_VERSION),
            app_volumes,
            app_device_routing: parse_map_value(obj, "appDeviceRouting"),
            app_mutes: parse_map_value(obj, "appMutes"),
            app_boosts,
            app_eq_settings: parse_map_value(obj, "appEQSettings"),
            app_settings: decoded_app_settings,
            system_sounds_follows_default: get("systemSoundsFollowsDefault")
                .and_then(|v| v.as_bool())
                .unwrap_or(true),
            app_device_selection_mode: parse_map_value(obj, "appDeviceSelectionMode"),
            app_selected_device_ids: parse_map_value(obj, "appSelectedDeviceUIDs"),
            locked_input_device_id: nonempty_str(get("lockedInputDeviceUID")),
            preferred_input_device_id: nonempty_str(get("preferredInputDeviceUID")),
            pinned_apps: parse_value(obj, "pinnedApps"),
            pinned_app_info: parse_map_value(obj, "pinnedAppInfo"),
            ignored_apps: parse_value(obj, "ignoredApps"),
            ignored_app_info: parse_map_value(obj, "ignoredAppInfo"),
            ddc_volumes: parse_map_value(obj, "ddcVolumes"),
            ddc_mute_states: parse_map_value(obj, "ddcMuteStates"),
            ddc_saved_volumes: parse_map_value(obj, "ddcSavedVolumes"),
            software_device_volumes: software_volumes,
            software_device_mute_states: parse_map_value(obj, "softwareDeviceMuteStates"),
            software_device_saved_volumes: software_saved,
            device_volume_tier_override: parse_map_value(obj, "deviceVolumeTierOverride"),
            device_icon_overrides: parse_map_value(obj, "deviceIconOverrides"),
            output_device_priority: parse_value(obj, "outputDevicePriority"),
            input_device_priority: parse_value(obj, "inputDevicePriority"),
            hidden_output_device_ids: parse_value(obj, "hiddenOutputDeviceUIDs"),
            hidden_input_device_ids: parse_value(obj, "hiddenInputDeviceUIDs"),
            device_auto_eq: parse_map_value(obj, "deviceAutoEQ"),
            favorite_auto_eq_profiles: parse_value(obj, "favoriteAutoEQProfiles"),
            auto_eq_preamp_enabled: get("autoEQPreampEnabled")
                .and_then(|v| v.as_bool())
                .unwrap_or(true),
            user_eq_presets: parse_value(obj, "userEQPresets"),
        })
    }
}

fn default_data_dir() -> std::path::PathBuf {
    std::env::var_os("XDG_DATA_HOME")
        .map(std::path::PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".local/share"))
        })
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("finetune")
}

pub(crate) fn data_dir() -> std::path::PathBuf {
    default_data_dir()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_keys_default() {
        let s: Settings = serde_json::from_str("{}").unwrap();
        assert_eq!(s.version, SETTINGS_VERSION);
        assert_eq!(s.auto_eq_preamp_enabled, true);
        assert!(s.app_eq_settings.is_empty());
    }

    #[test]
    fn clamps_old_volumes_above_one() {
        let raw = r#"{"appVolumes":{"spotify":1.5,"edge":-0.5,"firefox":0.7}}"#;
        let s: Settings = serde_json::from_str(raw).unwrap();
        assert_eq!(s.app_volumes.get("spotify"), Some(&1.0));
        assert!(!s.app_volumes.contains_key("edge"));
        assert_eq!(s.app_volumes.get("firefox"), Some(&0.7));
    }

    #[test]
    fn rejects_nan_boost() {
        let raw = r#"{"appBoosts":{"a":2.0,"b":null}}"#;
        let s: Settings = serde_json::from_str(raw).unwrap();
        assert_eq!(s.app_boosts.get("a"), Some(&2.0));
        assert_eq!(s.app_boosts.get("b"), None);
    }

    #[test]
    fn decodes_pinned_apps() {
        let raw = r#"{
            "pinnedApps":["spotify"],
            "pinnedAppInfo":{"spotify":{"persistenceIdentifier":"spotify","displayName":"Spotify","bundleID":null}}
        }"#;
        let s: Settings = serde_json::from_str(raw).unwrap();
        assert!(s.pinned_apps.contains("spotify"));
        assert_eq!(s.pinned_app_info["spotify"].display_name, "Spotify");
    }

    #[test]
    fn app_settings_defaults_sanitize() {
        let raw = r#"{"appSettings":{"defaultNewAppVolume":-3.0}}"#;
        let s: Settings = serde_json::from_str(raw).unwrap();
        assert_eq!(s.app_settings.default_new_app_volume, 1.0);
    }

    #[test]
    fn roundtrip_serialize_deserialize() {
        let mut s = Settings::default();
        s.app_volumes.insert("firefox".into(), 0.42);
        s.app_device_routing.insert("firefox".into(), "sink-1".into());
        s.device_auto_eq.insert(
            "sink-1".into(),
            AutoEQSelection { profile_id: "hd600".into(), is_enabled: true },
        );
        let json = serde_json::to_string(&s).unwrap();
        let back: Settings = serde_json::from_str(&json).unwrap();
        assert_eq!(back.app_volumes["firefox"], 0.42);
        assert_eq!(back.app_device_routing["firefox"], "sink-1");
        assert!(back.device_auto_eq["sink-1"].is_enabled);
    }
}
