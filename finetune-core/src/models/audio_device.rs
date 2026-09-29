use crate::models::TransportType;
use serde::{Deserialize, Serialize};

/// Direzione di un dispositivo audio.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum DeviceDirection {
    Output,
    Input,
}

/// Rappresentazione portabile di un dispositivo audio (sink o source).
/// Compilata dal backend PipeWire partendo dai nodi del registry.
///
/// `id` è l'identificatore stabile del dispositivo (per PipeWire: l'object id
/// del nodo come stringa, oppure l'UID di wireplumber se disponibile).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioDevice {
    pub id: String,
    pub name: String,
    pub description: String,
    pub direction: DeviceDirection,
    pub transport: TransportType,
    pub channels: u32,
    pub sample_rate: u32,
    pub volume: f32,
    pub muted: bool,
    /// Di proprietà di FineTune (i nostri nodi interni, da nascondere nella UI).
    pub is_own: bool,
    /// Nodo nascosto nella UI (filtro WirePlumber).
    pub is_hidden: bool,
    pub media_class: String,
}

impl AudioDevice {
    pub fn display_name(&self) -> &str {
        if !self.description.is_empty() {
            &self.description
        } else {
            &self.name
        }
    }
}

/// Metadati trasporto monomero riusabile dalle view.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TransportInfo {
    pub transport: TransportType,
}

const _: () = {
    // Ensure TransportInfo is constructible from AudioDevice for callers.
    impl TransportInfo {
        pub fn from_device(_d: &AudioDevice) -> TransportInfo {
            TransportInfo {
                transport: _d.transport,
            }
        }
    }
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn device_has_display_name_fallback() {
        let mut d = AudioDevice {
            id: "1".into(),
            name: "sink".into(),
            description: "".into(),
            direction: DeviceDirection::Output,
            transport: TransportType::Usb,
            channels: 2,
            sample_rate: 48000,
            volume: 1.0,
            muted: false,
            is_own: false,
            is_hidden: false,
            media_class: "Audio/Sink".into(),
        };
        assert_eq!(d.display_name(), "sink");
        d.description = "USB Headphones".into();
        assert_eq!(d.display_name(), "USB Headphones");
    }
}