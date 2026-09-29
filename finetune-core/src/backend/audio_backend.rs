// Astrazione del backend audio.
//
// Interfaccia indipendente dal backend reale (PipeWire, PulseAudio, ALSA…).
// Il resto dell'app (Core/UI) dipende solo da questo trait e dai tipi di dominio.

use serde::{Deserialize, Serialize};

/// Dispositivo di uscita (sink) visto dal backend.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioSink {
    /// ID stabile (per PipeWire: node id; per PulseAudio: nome sink).
    pub id: String,
    /// ID dell'oggetto PipeWire (per i link).
    pub node_id: u32,
    pub name: String,
    pub description: String,
    pub channels: u32,
    pub sample_rate: u32,
    pub volume: f32,
    pub muted: bool,
    pub is_default: bool,
    pub transport_hint: String,
}

/// Dispositivo di ingresso (microfono) visto dal backend.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioInput {
    /// ID stabile (per PipeWire: node id).
    pub id: String,
    /// ID dell'oggetto PipeWire.
    pub node_id: u32,
    pub name: String,
    pub description: String,
    pub channels: u32,
    pub sample_rate: u32,
    pub volume: f32,
    pub muted: bool,
    pub is_default: bool,
    pub transport_hint: String,
}

/// Stream giocato da un'applicazione (nodo Playback di PipeWire).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioStream {
    /// ID stabile del nodo.
    pub id: String,
    pub node_id: u32,
    /// PID del processo proprietario (0 = system/non mappato).
    pub pid: u32,
    /// Nome applicazione (client).
    pub app_name: String,
    /// Nome del media (es. titolo in riproduzione).
    pub media_name: String,
    pub volume: f32,
    /// Moltiplicatore boost attivo (1.0 = normale, 2..4 = amplificato).
    pub boost: f32,
    pub muted: bool,
    /// Lista dei sink a cui lo stream è collegato.
    pub linked_sink_ids: Vec<u32>,
}

/// Errori normalizzati del backend.
#[derive(Debug, thiserror::Error)]
pub enum BackendError {
    #[error("audio backend not connected")]
    NotConnected,
    #[error("device not found: {0}")]
    DeviceNotFound(String),
    #[error("stream not found: {0}")]
    StreamNotFound(String),
    #[error("io error: {0}")]
    Io(String),
    #[error("timeout")]
    Timeout,
    #[error("unsupported: {0}")]
    Unsupported(String),
}

/// Interfaccia astratta del backend audio.
pub trait AudioBackend {
    type Error: std::error::Error + Send + Sync + 'static;

    /// Enumera i sink (dispositivi di output).
    fn list_sinks(&self) -> Result<Vec<AudioSink>, Self::Error>;

    /// Enumera i dispositivi di ingresso (microfoni).
    fn list_inputs(&self) -> Result<Vec<AudioInput>, Self::Error>;

    /// Enumera gli stream audio attivi (applicazioni).
    fn list_streams(&self) -> Result<Vec<AudioStream>, Self::Error>;

    /// Imposta il volume di un sink (valore display 0.0 … 1.0, scala cubica interna).
    fn set_sink_volume(&self, id: &str, volume: f32) -> Result<(), Self::Error>;

    /// Imposta il mute di un sink.
    fn set_sink_mute(&self, id: &str, muted: bool) -> Result<(), Self::Error>;

    /// Imposta il volume di un ingresso (valore display 0.0 … 1.0).
    fn set_input_volume(&self, id: &str, volume: f32) -> Result<(), Self::Error>;

    /// Imposta il mute di un ingresso.
    fn set_input_mute(&self, id: &str, muted: bool) -> Result<(), Self::Error>;

    /// Imposta il volume base e il boost (moltiplicatore lineare 1…4) di uno
    /// stream in un'unica scrittura del nodo: channelVolumes = (volume³ · boost).
    fn set_stream_volume_with_boost(
        &self,
        id: &str,
        volume: f32,
        boost: f32,
    ) -> Result<(), Self::Error>;

    /// Imposta il mute di uno stream.
    fn set_stream_mute(&self, id: &str, muted: bool) -> Result<(), Self::Error>;

    /// Ripunta i link di uno stream verso i sink indicati (routing per-app).
    /// Passare una lista vuota rimuove tutti i link (nessun output).
    fn set_stream_links(&self, id: &str, sink_ids: &[String]) -> Result<(), Self::Error>;

    /// Imposta il sink di default (per node id o nome).
    fn set_default_sink(&self, id: &str) -> Result<(), Self::Error>;

    /// Imposta l'ingresso di default (per node id o nome).
    fn set_default_input(&self, id: &str) -> Result<(), Self::Error>;
}