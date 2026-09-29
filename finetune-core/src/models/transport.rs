/// Tipi di trasporto dispositivo (port di `FineTune/Models/.../TransportType.swift`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum TransportType {
    BuiltIn,
    Usb,
    Bluetooth,
    BluetoothLe,
    AirPlay,
    Virtual,
    ThunderBolt,
    Hdmi,
    DisplayPort,
    Aggregate,
    Unknown,
}

impl TransportType {
    pub fn is_bluetooth(self) -> bool {
        matches!(self, TransportType::Bluetooth | TransportType::BluetoothLe)
    }

    pub fn is_virtual(self) -> bool {
        matches!(self, TransportType::Virtual)
    }

    pub fn label(self) -> &'static str {
        match self {
            TransportType::BuiltIn => "Built-in",
            TransportType::Usb => "USB",
            TransportType::Bluetooth => "Bluetooth",
            TransportType::BluetoothLe => "Bluetooth LE",
            TransportType::AirPlay => "AirPlay",
            TransportType::Virtual => "Virtual",
            TransportType::ThunderBolt => "Thunderbolt",
            TransportType::Hdmi => "HDMI",
            TransportType::DisplayPort => "DisplayPort",
            TransportType::Aggregate => "Aggregate",
            TransportType::Unknown => "Unknown",
        }
    }

    /// Nome icona per la UI (port degli SF Symbol di FineTune).
    pub fn icon(self) -> &'static str {
        match self {
            TransportType::BuiltIn => "speaker",
            TransportType::Usb => "usb",
            TransportType::Bluetooth | TransportType::BluetoothLe => "bluetooth",
            TransportType::AirPlay => "airplay",
            TransportType::Virtual => "waveform",
            TransportType::ThunderBolt => "thunderbolt",
            TransportType::Hdmi => "tv",
            TransportType::DisplayPort => "display",
            TransportType::Aggregate => "stack",
            TransportType::Unknown => "speaker",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bluetooth_detection() {
        assert!(TransportType::Bluetooth.is_bluetooth());
        assert!(TransportType::BluetoothLe.is_bluetooth());
        assert!(!TransportType::Usb.is_bluetooth());
    }
}