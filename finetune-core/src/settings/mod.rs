//! Persistenza delle impostazioni (port di `SettingsManager.swift`).

pub mod manager;
pub mod settings;
pub mod types;

pub use manager::{SettingsError, SettingsManager};
pub use settings::{AppSettings, Settings, SETTINGS_VERSION};
pub use types::{
    AppearancePreference, HUDStyle, IgnoredAppInfo, MenuBarIconStyle, MenuBarPopupSize,
    PinnedAppInfo, ShortcutCodable, UserEQPreset, VolumeControlTier, VolumeHotkeyStep,
};