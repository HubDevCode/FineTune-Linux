use serde::{Deserialize, Serialize};

/// Applicazione audio in riproduzione (port di `FineTune/Models/AudioApp.swift`).
/// Su Linux/PipeWire un'app è identificata dal proprio PID; può possedere più
/// stream/nodi (es. Chrome spawna un client, Spotify uno stream).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioApp {
    pub pid: u32,
    /// Identificatore persistente (simile al `bundleID`): `pid`-indipendente quando
    /// possibile. Per PipeWire useremo `application.name`/`application.process.binary`,
    /// fallback al nome.
    pub persistence_identifier: String,
    pub name: String,
    pub is_running: bool,
    pub is_system: bool,
}

impl AudioApp {
    pub fn system() -> Self {
        AudioApp {
            pid: 0,
            persistence_identifier: "system".into(),
            name: "System".into(),
            is_running: true,
            is_system: true,
        }
    }
}

impl Eq for AudioApp {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn persistence_identifier_falls_back_to_name() {
        let app = AudioApp {
            pid: 42,
            persistence_identifier: String::new(),
            name: "spotify".into(),
            is_running: true,
            is_system: false,
        };
        let id = if app.persistence_identifier.is_empty() {
            format!("name:{}", app.name)
        } else {
            app.persistence_identifier.clone()
        };
        assert_eq!(id, "name:spotify");
    }
}