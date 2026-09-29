use serde::{Deserialize, Serialize};

/// Stile dell'icona della barra di sistema.
/// Port di `MenuBarIconStyle.swift`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
pub enum MenuBarIconStyle {
    #[serde(rename = "Default")]
    #[default]
    Default,
    #[serde(rename = "Speaker")]
    Speaker,
    #[serde(rename = "Device")]
    Device,
    #[serde(rename = "Waveform")]
    Waveform,
    #[serde(rename = "Equalizer")]
    Equalizer,
}

impl MenuBarIconStyle {
    /// Icona logica (per la UI Linux gli asset differiscono, ma il nome resta stabile).
    pub fn icon_name(self) -> &'static str {
        match self {
            MenuBarIconStyle::Default => "menu-bar",
            MenuBarIconStyle::Speaker => "speaker-wave-2",
            MenuBarIconStyle::Device => "headphones",
            MenuBarIconStyle::Waveform => "waveform",
            MenuBarIconStyle::Equalizer => "slider-vertical",
        }
    }
}

/// Stile dell'HUD mostrato dai media key.
/// Port di `HUDStyle.swift`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
pub enum HUDStyle {
    #[serde(rename = "tahoe")]
    #[default]
    Tahoe,
    #[serde(rename = "classic")]
    Classic,
}

/// Preferenza di aspetto (system/light/dark).
/// Port di `AppearancePreference.swift`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum AppearancePreference {
    #[default]
    System,
    Light,
    Dark,
}

/// Dimensione/densità del popup della barra di sistema.
/// Port di `MenuBarPopupSize.swift`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum MenuBarPopupSize {
    Compact,
    #[default]
    Comfortable,
    Spacious,
}

impl MenuBarPopupSize {
    /// Passo slider per pressione tasto (port di `sliderDelta`).
    pub fn slider_delta(self) -> f64 {
        match self {
            MenuBarPopupSize::Compact => 1.0 / 8.0,
            MenuBarPopupSize::Comfortable => 1.0 / 16.0,
            MenuBarPopupSize::Spacious => 1.0 / 32.0,
        }
    }
}

/// Passo del hotkey volume nel dominio slider. Port di `VolumeHotkeyStep.swift`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub enum VolumeHotkeyStep {
    #[serde(rename = "coarse")]
    Coarse,
    #[serde(rename = "normal")]
    #[default]
    Normal,
    #[serde(rename = "fine")]
    Fine,
    #[serde(rename = "extraFine")]
    ExtraFine,
}

impl VolumeHotkeyStep {
    pub fn slider_delta(self) -> f64 {
        match self {
            VolumeHotkeyStep::Coarse => 1.0 / 8.0,
            VolumeHotkeyStep::Normal => 1.0 / 16.0,
            VolumeHotkeyStep::Fine => 1.0 / 32.0,
            VolumeHotkeyStep::ExtraFine => 1.0 / 64.0,
        }
    }
}

/// Tier di controllo volume per dispositivo (hardware/DDC/software).
/// Port di `VolumeControlTier.swift`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum VolumeControlTier {
    Hardware,
    Ddc,
    Software,
}

/// Rappresentazione persistente delle shortcut globali (coppia keyCode+modifiers).
/// Port di `ShortcutCodable.swift`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
pub struct ShortcutCodable {
    pub key_code: i32,
    pub modifiers: u32,
}

/// Metadati di un'app bloccata. Port di `PinnedAppInfo`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PinnedAppInfo {
    pub persistence_identifier: String,
    pub display_name: String,
    /// Su Linux: nessun bundle id; restiamo fedeli al campo per forward-compat.
    pub bundle_id: Option<String>,
}

/// Metadati di un'app ignorata. Port di `IgnoredAppInfo`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct IgnoredAppInfo {
    pub persistence_identifier: String,
    pub display_name: String,
    pub bundle_id: Option<String>,
}

use crate::models::EQSettings;

/// Preset EQ creato dall'utente (curva EQ nominata su più app).
/// Port di `UserEQPreset.swift`. `created_at` è epoch-seconds UNIX.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UserEQPreset {
    pub id: String,
    pub name: String,
    /// I gain vengono copiati; `is_enabled` di EQSettings è ignorato per i preset.
    pub settings: EQSettings,
    pub created_at: i64,
}

use std::sync::atomic::{AtomicU64, Ordering};

impl UserEQPreset {
    pub fn new(name: &str, settings: EQSettings) -> Self {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let seq = COUNTER.fetch_add(1, Ordering::Relaxed);
        let id = format!("{nanos:x}-{seq}");
        Self {
            id,
            name: name.to_string(),
            settings,
            created_at: (nanos / 1_000_000_000) as i64,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn enums_roundtrip_json() {
        assert_eq!(
            serde_json::from_str::<MenuBarIconStyle>("\"Default\"").unwrap(),
            MenuBarIconStyle::Default
        );
        assert_eq!(
            serde_json::to_string(&VolumeHotkeyStep::ExtraFine).unwrap(),
            "\"extraFine\""
        );
        assert_eq!(
            serde_json::from_str::<AppearancePreference>("\"dark\"").unwrap(),
            AppearancePreference::Dark
        );
        assert_eq!(
            serde_json::from_str::<VolumeControlTier>("\"ddc\"").unwrap(),
            VolumeControlTier::Ddc
        );
    }

    #[test]
    fn hotkey_step_deltas() {
        assert!((VolumeHotkeyStep::Coarse.slider_delta() - 1.0 / 8.0).abs() < 1e-9);
        assert!((VolumeHotkeyStep::ExtraFine.slider_delta() - 1.0 / 64.0).abs() < 1e-9);
    }

    #[test]
    fn user_preset_gets_unique_ids() {
        let s = EQSettings::flat();
        let a = UserEQPreset::new("Test", s.clone());
        let b = UserEQPreset::new("Test", s);
        assert_ne!(a.id, b.id);
    }
}