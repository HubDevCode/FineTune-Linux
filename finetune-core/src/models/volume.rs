/// Mappatura percettiva del volume per il gain software per-app.
/// Port fedele di `FineTune/Models/VolumeMapping.swift` (curva x²).
///
/// **Importante**: è solo per il gain software per-app (PCM lineare). Non va
/// usata per i volumi hardware dei dispositivi (già taperizzati dal hardware).

/// Livello di controllo volume di un dispositivo.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum VolumeControlTier {
    Hardware,
    Ddc,
    Software,
}

/// Posizione slider (0..1) → gain PCM lineare. Curva x². Slider 50% → 0.25 (−12 dB).
pub fn slider_to_gain(slider: f64) -> f32 {
    if slider <= 0.0 {
        return 0.0;
    }
    let t = slider.min(1.0);
    (t * t) as f32
}

/// Gain PCM lineare → posizione slider (0..1). Inversa (sqrt). Gain 0.25 → 50%.
pub fn gain_to_slider(gain: f32) -> f64 {
    if gain <= 0.0 {
        return 0.0;
    }
    (gain.min(1.0) as f64).sqrt()
}

/// Posizione slider per il gain di sistema, in base al tier del controllo volume.
pub fn slider_fraction_for_gain(gain: f32, tier: VolumeControlTier) -> f64 {
    match tier {
        VolumeControlTier::Software => gain_to_slider(gain),
        VolumeControlTier::Hardware | VolumeControlTier::Ddc => {
            gain.clamp(0.0, 1.0) as f64
        }
    }
}

/// Gain di sistema per la posizione slider, in base al tier del controllo volume.
pub fn system_gain_for_slider(fraction: f64, tier: VolumeControlTier) -> f32 {
    match tier {
        VolumeControlTier::Software => slider_to_gain(fraction),
        VolumeControlTier::Hardware | VolumeControlTier::Ddc => {
            fraction.clamp(0.0, 1.0) as f32
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn nearly(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-6
    }

    #[test]
    fn slider_to_gain_square_law() {
        assert_eq!(slider_to_gain(0.0), 0.0);
        assert_eq!(slider_to_gain(0.25), 0.0625);
        assert!(nearly(slider_to_gain(0.5) as f64, 0.25));
        assert_eq!(slider_to_gain(1.0), 1.0);
        assert_eq!(slider_to_gain(1.5), 1.0);
    }

    #[test]
    fn roundtrip() {
        for slider in [0.0, 0.1, 0.25, 0.5, 0.75, 1.0] {
            let gain = slider_to_gain(slider);
            let back = gain_to_slider(gain);
            assert!(nearly(back, slider), "roundtrip failed for {slider}");
        }
    }

    #[test]
    fn software_tier_uses_square_law_system_delta() {
        let f = system_gain_for_slider(0.5, VolumeControlTier::Software);
        assert!(nearly(f as f64, 0.25));
    }

    #[test]
    fn hardware_tier_is_linear() {
        let f = system_gain_for_slider(0.5, VolumeControlTier::Hardware);
        assert!(nearly(f as f64, 0.5));
    }
}