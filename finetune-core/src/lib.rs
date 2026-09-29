// FineTune Linux — core libreria.
//
// Port dell'app FineTune (macOS) a Linux. Questo crate contiene solo codice
// portabile: modelli di dominio, matematica DSP real-time-safe, AutoEQ,
// persistenza e l'astrazione del backend audio (AudioBackend).
// Nessun riferimento a PipeWire o a framework GUI va inserito qui.

pub mod backend;
pub mod models;
pub mod dsp;
pub mod autoeq;
pub mod settings;

pub use models::*;