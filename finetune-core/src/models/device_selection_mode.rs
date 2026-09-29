use serde::{Deserialize, Serialize};

/// Modalità di selezione del dispositivo per un'app.
/// Port di `FineTune/Models/VolumeState.swift` → `DeviceSelectionMode`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DeviceSelectionMode {
    /// Route a un solo dispositivo.
    Single,
    /// Route a più dispositivi simultaneamente.
    Multi,
}

impl Default for DeviceSelectionMode {
    fn default() -> Self {
        DeviceSelectionMode::Single
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_is_single() {
        assert_eq!(DeviceSelectionMode::default(), DeviceSelectionMode::Single);
    }

    #[test]
    fn roundtrips_through_json() {
        let m = DeviceSelectionMode::Multi;
        let json = serde_json::to_string(&m).unwrap();
        assert_eq!(json, "\"multi\"");
        let back: DeviceSelectionMode = serde_json::from_str(&json).unwrap();
        assert_eq!(back, m);
    }
}