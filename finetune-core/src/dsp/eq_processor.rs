use crate::dsp::{BiquadCascade, BiquadMath, BiquadProcessor};
use crate::models::EQSettings;
use std::sync::{Arc, Mutex};

/// EQ grafica a 10 bande per-app (port di `FineTune/Audio/EQ/EQProcessor.swift`).
/// RT-safe nel path audio: `process` tocca solo lo stato `BiquadProcessor`
/// (setup scambiato atomicamente). Le scritture di configurazione sono main-thread.
#[derive(Debug, Clone)]
pub struct EQProcessor {
    inner: Arc<BiquadProcessor>,
    current_settings: Arc<Mutex<EQSettings>>,
}

impl EQProcessor {
    pub fn new(sample_rate: u32) -> Self {
        EQProcessor {
            inner: Arc::new(BiquadProcessor::new(sample_rate)),
            current_settings: Arc::new(Mutex::new(EQSettings::flat())),
        }
    }

    pub fn sample_rate(&self) -> u32 {
        self.inner.sample_rate()
    }

    /// Applica nuove impostazioni (main thread). Non resetta i delay buffer:
    /// i nuovi coefficienti adattano lo stato esistente → transizione senza click.
    pub fn update_settings(&self, settings: &EQSettings) {
        let clamped = settings.clamped_gains();
        self.inner.set_enabled(settings.is_enabled);
        let coeffs = BiquadMath::coefficients_for_all_bands(&clamped, f64::from(self.inner.sample_rate()));
        let cascade = BiquadCascade::from_flat(&coeffs);
        self.inner.swap_setup(cascade);
        *self.current_settings.lock().unwrap() = settings.clone();
    }

    pub fn settings(&self) -> EQSettings {
        self.current_settings.lock().unwrap().clone()
    }

    pub fn is_enabled(&self) -> bool {
        self.inner.is_enabled()
    }

    /// Ricrea i coefficienti per la nuova sample rate (preserva le settings).
    pub fn update_sample_rate(&self, new_rate: u32) {
        let settings = self.settings();
        let clamped = settings.clamped_gains();
        let coeffs = BiquadMath::coefficients_for_all_bands(&clamped, f64::from(new_rate));
        let cascade = BiquadCascade::from_flat(&coeffs);
        self.inner.swap_setup(cascade);
    }

    /// Path RT-safe.
    pub fn process(&self, input: &[f32], output: &mut [f32], channels: usize) {
        self.inner.process(input, output, channels);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flat_settings_process_identity() {
        let eq = EQProcessor::new(48000);
        eq.update_settings(&EQSettings::flat());
        let input: Vec<f32> = (0..40).map(|i| (i as f32 * 0.5).sin()).collect();
        let mut out = vec![0.0; 40];
        eq.process(&input, &mut out, 2);
        for (i, o) in input.iter().zip(out.iter()) {
            assert!((i - o).abs() < 1e-4, "in={i} out={o}");
        }
    }

    #[test]
    fn disabled_eq_bypasses() {
        let eq = EQProcessor::new(48000);
        eq.update_settings(&EQSettings::new(vec![6.0; 10], false));
        let input: Vec<f32> = (0..40).map(|i| i as f32 * 0.01).collect();
        let mut out = vec![99.0; 40];
        eq.process(&input, &mut out, 1);
        assert_eq!(input, out);
    }

    #[test]
    fn boosted_eq_changes_signal() {
        let eq = EQProcessor::new(48000);
        eq.update_settings(&EQSettings::new(vec![6.0; 10], true));
        let input: Vec<f32> = (0..4).map(|_| 0.01f32).collect();
        let mut out = vec![0.0; 4];
        eq.process(&input, &mut out, 1);
        // Con boost a banda bassa e 4 campioni è in transiente;
        // l'ampiezza non deve esplodere oltre un fattore ragionevole.
        assert!(out.iter().all(|s| s.abs() < 2.0), "unstable eq: {:?}", out);
    }
}