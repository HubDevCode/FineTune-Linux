/// Audio EQ Cookbook biquad coefficient calculations.
/// Port fedele di `FineTune/Audio/EQ/BiquadMath.swift`.
///
/// Riferimento: Robert Bristow-Johnson's Audio EQ Cookbook.
/// Tutte le funzioni restituiscono coefficienti **normalizzati per a0**
/// pronti per un cascade direct-form I: `y[n] = b0·x[n] + b1·x[n-1] + b2·x[n-2] − a1·y[n-1] − a2·y[n-2]`.
///
/// Formato: `[b0/a0, b1/a0, b2/a0, a1/a0, a2/a0]` (5 valori per sezione).
pub struct BiquadMath;

impl BiquadMath {
    /// Q standard per graphic EQ a bande sovrapposte.
    pub const GRAPHIC_EQ_Q: f64 = 1.4;

    /// Peaking EQ (bell filter).
    /// `gain_db` è il guadagno in dB (-12..+12 tipico).
    #[inline]
    pub fn peaking_eq(frequency: f64, gain_db: f32, q: f64, sample_rate: f64) -> [f64; 5] {
        let a = powf(10.0, f64::from(gain_db) / 40.0);
        let omega = 2.0 * std::f64::consts::PI * frequency / sample_rate;
        let sin_w = omega.sin();
        let cos_w = omega.cos();
        let alpha = sin_w / (2.0 * q);

        let b0 = 1.0 + alpha * a;
        let b1 = -2.0 * cos_w;
        let b2 = 1.0 - alpha * a;
        let a0 = 1.0 + alpha / a;
        let a1 = -2.0 * cos_w;
        let a2 = 1.0 - alpha / a;

        [b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0]
    }

    /// Low shelf (RBJ cookbook).
    #[inline]
    pub fn low_shelf(frequency: f64, gain_db: f32, q: f64, sample_rate: f64) -> [f64; 5] {
        let a = powf(10.0, f64::from(gain_db) / 40.0);
        let omega = 2.0 * std::f64::consts::PI * frequency / sample_rate;
        let sin_w = omega.sin();
        let cos_w = omega.cos();
        let alpha = sin_w / (2.0 * q);
        let two_sqrt_a_alpha = 2.0 * a.sqrt() * alpha;

        let b0 = a * ((a + 1.0) - (a - 1.0) * cos_w + two_sqrt_a_alpha);
        let b1 = 2.0 * a * ((a - 1.0) - (a + 1.0) * cos_w);
        let b2 = a * ((a + 1.0) - (a - 1.0) * cos_w - two_sqrt_a_alpha);
        let a0 = (a + 1.0) + (a - 1.0) * cos_w + two_sqrt_a_alpha;
        let a1 = -2.0 * ((a - 1.0) + (a + 1.0) * cos_w);
        let a2 = (a + 1.0) + (a - 1.0) * cos_w - two_sqrt_a_alpha;

        [b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0]
    }

    /// High shelf (RBJ cookbook).
    #[inline]
    pub fn high_shelf(frequency: f64, gain_db: f32, q: f64, sample_rate: f64) -> [f64; 5] {
        let a = powf(10.0, f64::from(gain_db) / 40.0);
        let omega = 2.0 * std::f64::consts::PI * frequency / sample_rate;
        let sin_w = omega.sin();
        let cos_w = omega.cos();
        let alpha = sin_w / (2.0 * q);
        let two_sqrt_a_alpha = 2.0 * a.sqrt() * alpha;

        let b0 = a * ((a + 1.0) + (a - 1.0) * cos_w + two_sqrt_a_alpha);
        let b1 = -2.0 * a * ((a - 1.0) + (a + 1.0) * cos_w);
        let b2 = a * ((a + 1.0) + (a - 1.0) * cos_w - two_sqrt_a_alpha);
        let a0 = (a + 1.0) - (a - 1.0) * cos_w + two_sqrt_a_alpha;
        let a1 = 2.0 * ((a - 1.0) - (a + 1.0) * cos_w);
        let a2 = (a + 1.0) - (a - 1.0) * cos_w - two_sqrt_a_alpha;

        [b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0]
    }

    /// High-pass 2° ordine (RBJ cookbook, Butterworth con Q dato).
    #[inline]
    pub fn high_pass(frequency: f64, q: f64, sample_rate: f64) -> [f64; 5] {
        let omega = 2.0 * std::f64::consts::PI * frequency / sample_rate;
        let sin_w = omega.sin();
        let cos_w = omega.cos();
        let alpha = sin_w / (2.0 * q);

        let b0 = (1.0 + cos_w) / 2.0;
        let b1 = -(1.0 + cos_w);
        let b2 = (1.0 + cos_w) / 2.0;
        let a0 = 1.0 + alpha;
        let a1 = -2.0 * cos_w;
        let a2 = 1.0 - alpha;

        [b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0]
    }

    /// Pre-warp bilineare inverso (transfer frequency tra due sample rate).
    /// `freq` è la frequenza nel dominio digitale `from_rate`, restituisce la
    /// frequenza equivalente nel dominio `to_rate`.
    #[inline]
    pub fn pre_warp_frequency(freq: f64, from_rate: f64, to_rate: f64) -> f64 {
        let f_analog = (from_rate / std::f64::consts::PI) * (std::f64::consts::PI * freq / from_rate).tan();
        (to_rate / std::f64::consts::PI) * (std::f64::consts::PI * f_analog / to_rate).atan()
    }

    /// Coefficienti per filtri AutoEQ (peaking, lowShelf, highShelf).
    /// Restituisce un vettore flat di `5 × N` coefficienti per la cascata biquad.
    pub fn coefficients_for_autoeq_filters(
        filters: &[crate::autoeq::AutoEQFilter],
        sample_rate: f64,
        profile_optimized_rate: f64,
    ) -> Vec<f64> {
        let needs_prewarp = (profile_optimized_rate - sample_rate).abs() > 1.0;
        let mut all_coeffs = Vec::with_capacity(filters.len() * 5);
        for f in filters {
            let mut freq = f.frequency;
            if needs_prewarp {
                freq = Self::pre_warp_frequency(freq, profile_optimized_rate, sample_rate);
            }
            // Bypass filtri invalidi o sopra Nyquist (possono derivare da pre-warp)
            if freq <= 0.0 || freq >= sample_rate / 2.0 {
                all_coeffs.extend_from_slice(&[1.0, 0.0, 0.0, 0.0, 0.0]);
                continue;
            }
            let coeffs = match f.filter_type {
                crate::autoeq::FilterType::Peaking => Self::peaking_eq(freq, f.gain_db, f.q, sample_rate),
                crate::autoeq::FilterType::LowShelf => Self::low_shelf(freq, f.gain_db, f.q, sample_rate),
                crate::autoeq::FilterType::HighShelf => Self::high_shelf(freq, f.gain_db, f.q, sample_rate),
            };
            all_coeffs.extend_from_slice(&coeffs);
        }
        all_coeffs
    }

    /// Coefficienti per le 10 bande della graphic EQ.
    /// Restituisce 50 double (10 bande × 5 coefficienti per sezione).
    /// Banda >= Nyquist → unity (passthrough).
    pub fn coefficients_for_all_bands(gains: &[f32], sample_rate: f64) -> Vec<f64> {
        use crate::models::EQSettings;
        assert_eq!(gains.len(), EQSettings::BAND_COUNT, "gains must have 10 bands");

        let mut all_coeffs = Vec::with_capacity(50);
        for (idx, &frequency) in EQSettings::FREQUENCIES.iter().enumerate() {
            if frequency >= sample_rate / 2.0 {
                all_coeffs.extend_from_slice(&[1.0, 0.0, 0.0, 0.0, 0.0]);
                continue;
            }
            let coeffs = Self::peaking_eq(frequency, gains[idx], Self::GRAPHIC_EQ_Q, sample_rate);
            all_coeffs.extend_from_slice(&coeffs);
        }
        all_coeffs
    }
}

/// Tipo di filtro AutoEQ (port di `AutoEQFilter.type`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FilterType {
    Peaking,
    LowShelf,
    HighShelf,
}

#[inline]
fn powf(base: f64, exp: f64) -> f64 {
    base.powf(exp)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flat_eq_is_unity_response() {
        let coeffs = BiquadMath::coefficients_for_all_bands(&[0.0; 10], 48000.0);
        assert_eq!(coeffs.len(), 50);
        for chunk in coeffs.chunks(5) {
            // gain 0 dB → sezioni matematicamente identità: b0/a0 == 1,
            // b1/a0 == a1/a0 (chunk[1]==chunk[3]) e b2/a0 == a2/a0 (chunk[2]==chunk[4]).
            assert!((chunk[0] - 1.0).abs() < 1e-10, "b0 not unity: {}", chunk[0]);
            assert_eq!(chunk[1], chunk[3]);
            assert_eq!(chunk[2], chunk[4]);
        }
    }

    #[test]
    fn peaking_eq_produces_valid_coeffs() {
        let coeffs = BiquadMath::peaking_eq(1000.0, 6.0, 1.4, 48000.0);
        assert_eq!(coeffs.len(), 5);
        // b0 should be > 1 for positive gain
        assert!(coeffs[0] > 1.0);
    }

    #[test]
    fn high_shelf_eq_positive_gain() {
        let coeffs = BiquadMath::high_shelf(8000.0, 6.0, 0.707, 48000.0);
        assert!(coeffs[0] > 1.0);
    }

    #[test]
    fn low_shelf_eq_positive_gain() {
        let coeffs = BiquadMath::low_shelf(100.0, 6.0, 0.707, 48000.0);
        assert!(coeffs[0] > 1.0);
    }

    #[test]
    fn pre_warp_is_neutral_at_same_rate() {
        let f = BiquadMath::pre_warp_frequency(1000.0, 48000.0, 48000.0);
        assert!((f - 1000.0).abs() < 1e-10);
    }

    #[test]
    fn high_pass_unity_q_at_38hz() {
        let coeffs = BiquadMath::high_pass(38.0, std::f64::consts::FRAC_1_SQRT_2, 48000.0);
        assert_eq!(coeffs.len(), 5);
    }
}