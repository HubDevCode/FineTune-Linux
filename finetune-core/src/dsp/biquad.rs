use arc_swap::ArcSwap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

/// Stato di una sezione biquad in forma transposed direct-form II.
/// RT-safe: nessuna allocazione nei metodi `process`.
#[derive(Debug, Clone)]
pub struct BiquadState {
    pub delay1: f64,
    pub delay2: f64,
}

impl Default for BiquadState {
    fn default() -> Self {
        BiquadState {
            delay1: 0.0,
            delay2: 0.0,
        }
    }
}

/// Cascata di sezioni biquad (coefficienti normalizzati per a0).
/// RT-safe `process`, configurazione scambiata atomicamente.
#[derive(Debug, Clone)]
pub struct BiquadCascade {
    /// Coefficienti flat: `[b0,b1,b2,a1,a2] * sezioni`.
    coefficients: Vec<[f64; 5]>,
}

impl BiquadCascade {
    /// Costruisce una cascata da coefficienti flat (5 valori per sezione).
    pub fn from_flat(flat_coeffs: &[f64]) -> Self {
        debug_assert_eq!(flat_coeffs.len() % 5, 0);
        let coefficients = flat_coeffs
            .chunks_exact(5)
            .map(|c| [c[0], c[1], c[2], c[3], c[4]])
            .collect();
        BiquadCascade { coefficients }
    }

    pub fn is_empty(&self) -> bool {
        self.coefficients.is_empty()
    }

    pub fn section_count(&self) -> usize {
        self.coefficients.len()
    }

    /// Elabora un buffer interleaved stereo o mono.
    /// Se `input` e `output` puntano allo stesso buffer, l'elaborazione è in-place.
    /// `frames` = numero di frame; `channels` = 1 o 2.
    ///
    /// Difference equation (direct form I):
    ///   y[n] = b0·x[n] + b1·x[n-1] + b2·x[n-2] − a1·y[n-1] − a2·y[n-2]
    #[inline]
    #[allow(clippy::too_many_arguments)]
    pub fn process(
        &self,
        input: &[f32],
        output: &mut [f32],
        channels: usize,
    ) {
        debug_assert_eq!(input.len(), output.len());
        let frames = input.len() / channels;
        if frames == 0 {
            return;
        }
        let sections = self.coefficients.len();
        if sections == 0 {
            output.copy_from_slice(input);
            return;
        }

        if std::ptr::eq(input.as_ptr(), output.as_ptr()) {
            self.process_in_place(output, channels);
        } else {
            output.copy_from_slice(input);
            self.process_in_place(output, channels);
        }
    }

    /// Elabora un buffer in-place (DF-II trasposta, nessuna allocazione).
    #[inline]
    pub fn process_in_place(&self, buffer: &mut [f32], channels: usize) {
        let frames = buffer.len() / channels;
        if frames == 0 {
            return;
        }
        let sections = self.coefficients.len();
        if sections == 0 {
            return;
        }
        // EQ 10 bande → 10 sezioni; AutoEQ → max 10. Stato a dimensione fissa su stack.
        assert!(sections <= 16, "cascade too deep for stack state");

        for ch in 0..channels {
            let mut s1 = [0.0f64; 16];
            let mut s2 = [0.0f64; 16];
            for i in 0..frames {
                let mut x = f64::from(buffer[i * channels + ch]);
                for (sec, &c) in self.coefficients.iter().enumerate().take(sections) {
                    // biquad DF-II transposed: y = b0*x + s1; s1 = b1*x - a1*y + s2; s2 = b2*x - a2*y
                    let y = c[0] * x + s1[sec];
                    s1[sec] = c[1] * x - c[3] * y + s2[sec];
                    s2[sec] = c[2] * x - c[4] * y;
                    x = y;
                }
                buffer[i * channels + ch] = x as f32;
            }
        }
    }
}

/// Wrapper RT-safe attorno a `BiquadCascade`: il setup viene scambiato
/// atomicamente (controllo/process), la disattivazione è atomica.
/// Port del pattern `BiquadProcessor` dell'app macOS.
#[derive(Debug)]
pub struct BiquadProcessor {
    enabled: AtomicBool,
    setup: ArcSwap<BiquadCascade>,
    sample_rate: AtomicU32,
}

impl Default for BiquadProcessor {
    fn default() -> Self {
        BiquadProcessor::passthrough()
    }
}

impl BiquadProcessor {
    pub fn new(sample_rate: u32) -> Self {
        BiquadProcessor {
            enabled: AtomicBool::new(true),
            setup: ArcSwap::new(Arc::new(BiquadCascade::from_flat(&[]))),
            sample_rate: AtomicU32::new(sample_rate),
        }
    }

    pub fn passthrough() -> Self {
        BiquadProcessor::new(48000)
    }

    pub fn sample_rate(&self) -> u32 {
        self.sample_rate.load(Ordering::Relaxed)
    }

    pub fn is_enabled(&self) -> bool {
        self.enabled.load(Ordering::Acquire)
    }

    pub fn set_enabled(&self, enabled: bool) {
        self.enabled.store(enabled, Ordering::Release);
    }

    /// Swap atomico del setup. La vecchia istanza viene rilasciata in background
    /// (equivalente della distruzione differita a 500 ms dell'app macOS); dato
    /// che la reader è basata su `arc-swap`, non c'è use-after-free.
    pub fn swap_setup(&self, cascade: BiquadCascade) {
        let _old = self.setup.swap(Arc::new(cascade));
    }

    pub fn current_setup(&self) -> Arc<BiquadCascade> {
        self.setup.load_full()
    }

    /// Elabora il buffer (interleaved stereo o mono). Bypass con copia se
    /// disabilitato o setup vuoto.
    pub fn process(&self, input: &[f32], output: &mut [f32], channels: usize) {
        if !self.is_enabled() {
            output.copy_from_slice(input);
            return;
        }
        let setup = self.setup.load();
        setup.process(input, output, channels);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dsp::BiquadMath;

    fn nearly(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn passthrough_is_identity() {
        let bp = BiquadProcessor::new(48000);
        let input: Vec<f32> = (0..64).map(|i| (i as f32 * 0.1).sin()).collect();
        let mut out = vec![0.0; 64];
        bp.process(&input, &mut out, 2);
        for (i, o) in input.iter().zip(out.iter()) {
            assert_eq!(i, o);
        }
    }

    #[test]
    fn disabled_processor_bypasses() {
        let bp = BiquadProcessor::new(48000);
        bp.set_enabled(false);
        let input: Vec<f32> = (0..64).map(|i| i as f32).collect();
        let mut out = vec![99.0; 64];
        bp.process(&input, &mut out, 1);
        assert_eq!(input, out);
    }

    #[test]
    fn unity_gain_eq_is_identity() {
        let bp = BiquadProcessor::new(48000);
        let coeffs = BiquadMath::coefficients_for_all_bands(&[0.0; 10], 48000.0);
        let cascade = BiquadCascade::from_flat(&coeffs);
        bp.swap_setup(cascade);
        let input: Vec<f32> = (0..480).map(|i| ((i as f32) * 0.1).sin()).collect();
        let mut out = vec![0.0; 480];
        bp.process(&input, &mut out, 2);
        for (i, o) in input.iter().zip(out.iter()) {
            assert!(
                nearly(*i, *o) || i.abs() < 1e-5,
                "mismatch at {}: in={} out={}",
                0,
                i,
                o
            );
        }
    }

    #[test]
    fn cascade_processes_mono_and_stereo_without_panic() {
        let c = BiquadCascade::from_flat(&[1.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0]);
        let input = vec![0.1f32; 10];
        let mut out = vec![0.0; 10];
        c.process(&input, &mut out, 1);
        let input_s = vec![0.2f32; 20];
        let mut out_s = vec![0.0; 20];
        c.process(&input_s, &mut out_s, 2);
    }
}