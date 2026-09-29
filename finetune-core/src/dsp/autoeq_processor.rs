use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use arc_swap::ArcSwap;

use crate::autoeq::AutoEQProfile;
use crate::dsp::{BiquadCascade, BiquadMath};

/// Processore RT-safe dedicato alla correzione AutoEQ.
/// Port fedele di `AutoEQProcessor.swift`.
#[derive(Debug)]
pub struct AutoEQProcessor {
    enabled: AtomicBool,
    setup: ArcSwap<BiquadCascade>,
    sample_rate: AtomicU32,
    /// Guadagno di preamp in scala lineare (dB → lineare). 1.0 = bypass.
    preamp_gain: AtomicU32,
    /// Preamp attivo (false = dipendere dal limiter a valle).
    preamp_enabled: AtomicBool,
    filter_count: AtomicU32,
}

impl Default for AutoEQProcessor {
    fn default() -> Self {
        Self::new(48000)
    }
}

impl AutoEQProcessor {
    pub fn new(sample_rate: u32) -> Self {
        Self {
            enabled: AtomicBool::new(false),
            setup: ArcSwap::new(Arc::new(BiquadCascade::from_flat(&[]))),
            sample_rate: AtomicU32::new(sample_rate),
            preamp_gain: AtomicU32::new(1.0f32.to_bits()),
            preamp_enabled: AtomicBool::new(true),
            filter_count: AtomicU32::new(0),
        }
    }

    pub fn sample_rate(&self) -> u32 {
        self.sample_rate.load(Ordering::Relaxed)
    }

    pub fn is_enabled(&self) -> bool {
        self.enabled.load(Ordering::Acquire)
    }

    pub fn filter_count(&self) -> u32 {
        self.filter_count.load(Ordering::Relaxed)
    }

    pub fn is_preamp_enabled(&self) -> bool {
        self.preamp_enabled.load(Ordering::Acquire)
    }

    /// Aggiorna il profilo di correzione. `None` = disabilita.
    /// Se la validazione fallisce (nessun filtro valido), disattiva mantenendo invariato il setup.
    pub fn update_profile(&self, profile: Option<&AutoEQProfile>) {
        let validated = profile.map(|p| p.validated());
        match validated {
            Some(p) if !p.filters.is_empty() => {
                let coefficients = BiquadMath::coefficients_for_autoeq_filters(
                    &p.filters,
                    f64::from(self.sample_rate()),
                    p.optimized_sample_rate,
                );
                let cascade = BiquadCascade::from_flat(&coefficients);

                let preamp_linear = if self.preamp_enabled.load(Ordering::Acquire) {
                    10f32.powf(p.preamp_db / 20.0)
                } else {
                    1.0
                };
                self.preamp_gain.store(preamp_linear.to_bits(), Ordering::Release);
                self.filter_count.store(p.filters.len() as u32, Ordering::Release);
                self.setup.swap(Arc::new(cascade));
                self.enabled.store(true, Ordering::Release);
            }
            _ => {
                // Disabilita
                self.enabled.store(false, Ordering::Release);
                self.filter_count.store(0, Ordering::Release);
                self.preamp_gain.store(1.0f32.to_bits(), Ordering::Release);
                self.setup.swap(Arc::new(BiquadCascade::from_flat(&[])));
            }
        }
    }

    /// Alterna il preamp del profilo. Quando disattivo, gestione dei picchi
    /// affidata al limiter a valle. Riapplica il profilo corrente.
    /// Nota: non mantiene il riferimento al profilo (equivalente: richiama `update_profile`).
    pub fn set_preamp_enabled(&self, enabled: bool, current_profile: Option<&AutoEQProfile>) {
        if enabled == self.preamp_enabled.load(Ordering::Acquire) {
            return;
        }
        self.preamp_enabled.store(enabled, Ordering::Release);
        if current_profile.is_some() {
            self.update_profile(current_profile);
        }
    }

    /// Elabora un buffer interleaved (mono/stereo) applicando preamp → cascata biquad.
    #[inline]
    pub fn process(&self, input: &[f32], output: &mut [f32], channels: usize) {
        if !self.enabled.load(Ordering::Acquire) {
            output.copy_from_slice(input);
            return;
        }
        let preamp =
            f32::from_bits(self.preamp_gain.load(Ordering::Relaxed));
        if preamp == 1.0 {
            let setup = self.setup.load();
            setup.process(input, output, channels);
        } else {
            // Scala in-place (equivalente di vDSP_vsmul) senza allocazioni;
            // la cascata legge e scrive lo stesso buffer in modo sicuro.
            for v in output.iter_mut().take(input.len()) {
                *v *= preamp;
            }
            let setup = self.setup.load();
            setup.process_in_place(output, channels);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_profile(n_filters: usize) -> AutoEQProfile {
        let filters = (0..n_filters)
            .map(|i| crate::autoeq::AutoEQFilter {
                filter_type: crate::autoeq::FilterType::Peaking,
                frequency: 200.0 * (i as f64 + 1.0),
                gain_db: 3.0,
                q: 1.0,
            })
            .collect();
        AutoEQProfile {
            id: "test".into(),
            name: "Test".into(),
            source: crate::autoeq::AutoEQSource::Imported,
            preamp_db: -6.0,
            filters,
            measured_by: None,
            optimized_sample_rate: 48000.0,
        }
    }

    #[test]
    fn disabled_by_default() {
        let p = AutoEQProcessor::new(48000);
        assert!(!p.is_enabled());
        let input = vec![0.1f32; 8];
        let mut out = vec![9.0; 8];
        p.process(&input, &mut out, 2);
        assert_eq!(input, out);
    }

    #[test]
    fn applying_profile_enables_and_counts_filters() {
        let p = AutoEQProcessor::new(48000);
        let profile = sample_profile(3);
        p.update_profile(Some(&profile));
        assert!(p.is_enabled());
        assert_eq!(p.filter_count(), 3);
    }

    #[test]
    fn nil_profile_disables() {
        let p = AutoEQProcessor::new(48000);
        let profile = sample_profile(3);
        p.update_profile(Some(&profile));
        assert!(p.is_enabled());
        p.update_profile(None);
        assert!(!p.is_enabled());
    }

    #[test]
    fn preamp_applied_when_enabled() {
        let p = AutoEQProcessor::new(48000);
        let profile = sample_profile(1);
        p.update_profile(Some(&profile));
        // Preamp -6 dB ≈ 0.5 linear; con un filtro peaking 3dB il guadagno DC
        // resta vicino a 1 ma il preamp è applicato in linear scale prima della cascata.
        let input = vec![0.8f32; 96];
        let mut out = vec![0.0; 96];
        p.process(&input, &mut out, 2);
        for (i, o) in input.iter().zip(out.iter()) {
            assert!(
                o.abs() < i.abs(),
                "preamp + EQ should not amplify above input magnitude here: in={i} out={o}"
            );
        }
    }

    #[test]
    fn set_preamp_enabled_reapplies() {
        let p = AutoEQProcessor::new(48000);
        let profile = sample_profile(1);
        p.update_profile(Some(&profile));
        p.set_preamp_enabled(false, Some(&profile));
        assert!(!p.is_preamp_enabled());
        assert_eq!(f32::from_bits(p.preamp_gain.load(Ordering::Relaxed)), 1.0);
        p.set_preamp_enabled(true, Some(&profile));
        assert!(p.is_preamp_enabled());
        let expected: f32 = 10f32.powf(profile.preamp_db / 20.0);
        assert!((f32::from_bits(p.preamp_gain.load(Ordering::Relaxed)) - expected).abs() < 1e-4);
    }
}