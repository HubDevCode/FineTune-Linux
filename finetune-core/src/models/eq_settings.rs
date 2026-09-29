use serde::{Deserialize, Serialize};

/// Impostazioni EQ a 10 bande per-app.
/// Port di `FineTune/Models/EQSettings.swift`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EQSettings {
    /// Gain in dB per banda (-12..+12), 10 bande.
    pub band_gains: Vec<f32>,
    /// Se l'elaborazione EQ è abilitata.
    pub is_enabled: bool,
}

impl Default for EQSettings {
    fn default() -> Self {
        EQSettings {
            band_gains: vec![0.0; Self::BAND_COUNT],
            is_enabled: true,
        }
    }
}

impl EQSettings {
    pub const BAND_COUNT: usize = 10;
    pub const MAX_GAIN_DB: f32 = 12.0;
    pub const MIN_GAIN_DB: f32 = -12.0;

    /// Frequenze ISO standard 1/1-octavo per graphic EQ a 10 bande.
    pub const FREQUENCIES: [f64; 10] = [
        31.25, 62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0,
    ];

    pub fn new(band_gains: Vec<f32>, is_enabled: bool) -> Self {
        EQSettings {
            band_gains: Self::normalize_bands(band_gains),
            is_enabled,
        }
    }

    /// Normalizza il vettore gain esattamente a 10 bande (pad con 0 o tronca).
    fn normalize_bands(gains: Vec<f32>) -> Vec<f32> {
        let mut g = gains;
        if g.len() > Self::BAND_COUNT {
            g.truncate(Self::BAND_COUNT);
        } else {
            g.resize(Self::BAND_COUNT, 0.0);
        }
        g
    }

    /// Gain clampati al range valido, proteggendo da NaN/Inf.
    pub fn clamped_gains(&self) -> Vec<f32> {
        self.band_gains
            .iter()
            .map(|g| {
                if !g.is_finite() {
                    0.0
                } else {
                    g.clamp(Self::MIN_GAIN_DB, Self::MAX_GAIN_DB)
                }
            })
            .collect()
    }

    /// EQ piatto.
    pub fn flat() -> Self {
        EQSettings::default()
    }
}

/// Descrizione di una banda EQ per la UI.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EQBand {
    pub frequency: f64,
    pub gain_db: f32,
    pub enabled: bool,
}

impl EQBand {
    pub fn new(frequency: f64, gain_db: f32, enabled: bool) -> Self {
        EQBand {
            frequency,
            gain_db,
            enabled,
        }
    }
}

impl From<&EQSettings> for Vec<EQBand> {
    fn from(s: &EQSettings) -> Self {
        EQSettings::FREQUENCIES
            .iter()
            .zip(s.clamped_gains().iter())
            .map(|(&f, &g)| EQBand::new(f, g, s.is_enabled))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_short_arrays() {
        let s = EQSettings::new(vec![1.0, 2.0], true);
        assert_eq!(s.band_gains.len(), EQSettings::BAND_COUNT);
        assert_eq!(s.band_gains[0], 1.0);
        assert_eq!(s.band_gains[2], 0.0);
    }

    #[test]
    fn truncates_long_arrays() {
        let s = EQSettings::new(vec![1.0; 14], true);
        assert_eq!(s.band_gains.len(), EQSettings::BAND_COUNT);
    }

    #[test]
    fn clamps_gains() {
        let s = EQSettings::new(vec![20.0, -20.0, f32::NAN, 5.0, 0.0, -5.0, 0.0, 0.0, 0.0, 0.0], true);
        let clamped = s.clamped_gains();
        assert_eq!(clamped[0], EQSettings::MAX_GAIN_DB);
        assert_eq!(clamped[1], EQSettings::MIN_GAIN_DB);
        assert_eq!(clamped[2], 0.0);
        assert_eq!(clamped[3], 5.0);
    }
}