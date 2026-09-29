/// Limiter soft-knee a compressione asintotica (port di `FineTune/Audio/Engine/SoftLimiter.swift`).
/// Previene il clipping quando l'audio è boostato sopra l'unità.
///
/// **RT-safety**: pura aritmetica, nessuna allocazione/lock/I/O.
///
/// - Sotto 0.95: passa inalterato (trasparente)
/// - Sopra 0.95: compressione dolce che approccia 1.0 asintoticamente
/// - Non supera mai ±1.0 per input finiti
#[derive(Debug, Clone, Copy)]
pub struct SoftLimiter;

impl SoftLimiter {
    pub const THRESHOLD: f32 = 0.95;
    pub const CEILING: f32 = 1.0;

    #[inline(always)]
    pub fn headroom() -> f32 {
        Self::CEILING - Self::THRESHOLD
    }

    /// Applica il soft-knee a un singolo campione.
    #[inline(always)]
    pub fn apply(sample: f32) -> f32 {
        let abs_sample = sample.abs();
        if abs_sample <= Self::THRESHOLD {
            return sample;
        }
        let overshoot = abs_sample - Self::THRESHOLD;
        let compressed = Self::THRESHOLD + Self::headroom() * (overshoot / (overshoot + Self::headroom()));
        if sample >= 0.0 {
            compressed
        } else {
            -compressed
        }
    }

    /// Applica il limiter a un intero buffer di campioni (in-place).
    /// Fast-path: se il picco del buffer è ≤ soglia, lo skippa interamente.
    #[inline(always)]
    pub fn process_buffer(buffer: &mut [f32]) {
        let peak = buffer.iter().fold(0.0f32, |a, &s| a.max(s.abs()));
        if peak <= Self::THRESHOLD {
            return;
        }
        for s in buffer.iter_mut() {
            *s = Self::apply(*s);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn below_threshold_passes_unchanged() {
        assert_eq!(SoftLimiter::apply(0.5), 0.5);
        assert_eq!(SoftLimiter::apply(-0.5), -0.5);
        assert_eq!(SoftLimiter::apply(SoftLimiter::THRESHOLD), SoftLimiter::THRESHOLD);
    }

    #[test]
    fn asymptotically_approaches_ceiling() {
        let limited = SoftLimiter::apply(1000.0);
        assert!(limited < SoftLimiter::CEILING);
        assert!(limited > SoftLimiter::THRESHOLD);
        // per input finiti → output ±<1.0
        assert!(limited <= SoftLimiter::CEILING);
    }

    #[test]
    fn preserves_sign() {
        assert!(SoftLimiter::apply(-30.0) < 0.0);
        assert!(SoftLimiter::apply(30.0) > 0.0);
    }

    #[test]
    fn process_buffer_skips_quiet_audio() {
        let mut buf = vec![0.1f32; 64];
        let copy = buf.clone();
        SoftLimiter::process_buffer(&mut buf);
        assert_eq!(buf, copy);
    }

    #[test]
    fn process_buffer_limits_boosted_audio() {
        let mut buf = vec![5.0f32; 64];
        SoftLimiter::process_buffer(&mut buf);
        for s in &buf {
            assert!(*s <= SoftLimiter::CEILING);
        }
    }

    #[test]
    fn asymptote_violation_property() {
        // Proprietà "mai oltre ±ceiling per qualsiasi input finito".
        for i in 0..1_000_000 {
            let x = ((i as f64) * 13.37).sin() as f32 * 100.0;
            let y = SoftLimiter::apply(x);
            assert!(y.abs() <= SoftLimiter::CEILING + 1e-7);
        }
    }
}