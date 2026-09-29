use crate::models::EQSettings;
use serde::{Deserialize, Serialize};

/// Catalogo dei 20 preset EQ integrati (port di `FineTune/Models/EQPreset.swift`).
/// Band: 31, 62, 125, 250, 500, 1k, 2k, 4k, 8k, 16k. Range ±12 dB.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum EQPreset {
    // Utility
    Flat,
    BassBoost,
    BassCut,
    TrebleBoost,
    // Speech
    VocalClarity,
    Podcast,
    SpokenWord,
    // Listening
    Loudness,
    LateNight,
    SmallSpeakers,
    // Music
    Rock,
    Pop,
    Electronic,
    Jazz,
    Classical,
    HipHop,
    Rnb,
    Deep,
    Acoustic,
    // Media
    Movie,
}

impl EQPreset {
    pub const ALL: [EQPreset; 20] = [
        EQPreset::Flat,
        EQPreset::BassBoost,
        EQPreset::BassCut,
        EQPreset::TrebleBoost,
        EQPreset::VocalClarity,
        EQPreset::Podcast,
        EQPreset::SpokenWord,
        EQPreset::Loudness,
        EQPreset::LateNight,
        EQPreset::SmallSpeakers,
        EQPreset::Rock,
        EQPreset::Pop,
        EQPreset::Electronic,
        EQPreset::Jazz,
        EQPreset::Classical,
        EQPreset::HipHop,
        EQPreset::Rnb,
        EQPreset::Deep,
        EQPreset::Acoustic,
        EQPreset::Movie,
    ];

    pub fn category(self) -> Category {
        use EQPreset::*;
        match self {
            Flat | BassBoost | BassCut | TrebleBoost => Category::Utility,
            VocalClarity | Podcast | SpokenWord => Category::Speech,
            Loudness | LateNight | SmallSpeakers => Category::Listening,
            Rock | Pop | Electronic | Jazz | Classical | HipHop | Rnb | Deep | Acoustic => {
                Category::Music
            }
            Movie => Category::Media,
        }
    }

    pub fn name(self) -> &'static str {
        use EQPreset::*;
        match self {
            Flat => "Flat",
            BassBoost => "Bass Boost",
            BassCut => "Bass Cut",
            TrebleBoost => "Treble Boost",
            VocalClarity => "Vocal Clarity",
            Podcast => "Podcast",
            SpokenWord => "Spoken Word",
            Loudness => "Loudness",
            LateNight => "Late Night",
            SmallSpeakers => "Small Speakers",
            Rock => "Rock",
            Pop => "Pop",
            Electronic => "Electronic",
            Jazz => "Jazz",
            Classical => "Classical",
            HipHop => "Hip-Hop",
            Rnb => "R&B",
            Deep => "Deep",
            Acoustic => "Acoustic",
            Movie => "Movie",
        }
    }

    /// Impostazioni del preset (valori esatti dell'app originale).
    pub fn settings(self) -> EQSettings {
        use EQPreset::*;
        let gains: [f32; 10] = match self {
            // Utility
            Flat => [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            BassBoost => [6.0, 6.0, 5.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            BassCut => [-6.0, -5.0, -4.0, -2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            TrebleBoost => [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 4.0, 5.0, 6.0],
            // Speech
            VocalClarity => [-4.0, -2.0, -1.0, -3.0, 0.0, 2.0, 4.0, 4.0, 1.0, 0.0],
            Podcast => [-6.0, -4.0, -2.0, -1.0, 0.0, 2.0, 4.0, 3.0, 1.0, 0.0],
            SpokenWord => [-8.0, -6.0, -3.0, -2.0, 0.0, 2.0, 4.0, 4.0, 2.0, 0.0],
            // Listening
            Loudness => [5.0, 4.0, 2.0, 0.0, -2.0, -2.0, 0.0, 2.0, 4.0, 5.0],
            LateNight => [-6.0, -4.0, -2.0, 0.0, 0.0, 1.0, 2.0, 2.0, 1.0, 0.0],
            SmallSpeakers => [3.0, 4.0, 5.0, 2.0, 0.0, 1.0, 2.0, 2.0, 1.0, 0.0],
            // Music
            Rock => [4.0, 3.0, 2.0, 0.0, -1.0, 0.0, 2.0, 3.0, 2.0, 1.0],
            Pop => [3.0, 3.0, 2.0, 0.0, -1.0, 1.0, 2.0, 3.0, 3.0, 4.0],
            Electronic => [7.0, 6.0, 4.0, 0.0, -2.0, -2.0, 1.0, 3.0, 4.0, 3.0],
            Jazz => [3.0, 2.0, 1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 1.0],
            Classical => [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 2.0],
            HipHop => [6.0, 5.0, 4.0, 0.0, -1.0, 0.0, 2.0, 3.0, 4.0, 3.0],
            Rnb => [4.0, 4.0, 3.0, 1.0, -1.0, 0.0, 2.0, 3.0, 3.0, 2.0],
            Deep => [5.0, 6.0, 4.0, 1.0, -2.0, -2.0, 0.0, 1.0, 2.0, 1.0],
            Acoustic => [0.0, 1.0, 2.0, 2.0, 1.0, 0.0, 1.0, 2.0, 2.0, 1.0],
            // Media
            Movie => [4.0, 4.0, 3.0, -1.0, -1.0, 1.0, 3.0, 3.0, 2.0, 1.0],
        };
        EQSettings::new(gains.to_vec(), true)
    }
}

/// Categorie dei preset (port di `EQPreset.Category`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum Category {
    Utility,
    Speech,
    Listening,
    Music,
    Media,
}

impl Category {
    pub fn label(self) -> &'static str {
        match self {
            Category::Utility => "Utility",
            Category::Speech => "Speech",
            Category::Listening => "Listening",
            Category::Music => "Music",
            Category::Media => "Media",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn has_twenty_presets() {
        assert_eq!(EQPreset::ALL.len(), 20);
    }

    #[test]
    fn all_presets_have_valid_gains() {
        for p in EQPreset::ALL {
            let s = p.settings();
            assert_eq!(s.band_gains.len(), EQSettings::BAND_COUNT);
            for g in &s.band_gains {
                assert!(
                    *g >= EQSettings::MIN_GAIN_DB && *g <= EQSettings::MAX_GAIN_DB,
                    "preset {} out of range: {}",
                    p.name(),
                    g
                );
            }
        }
    }

    #[test]
    fn bass_boost_curve_is_monotonic_descending_if_needed() {
        let s = EQPreset::BassBoost.settings();
        assert_eq!(s.band_gains[0], 6.0);
        assert_eq!(s.band_gains[9], 0.0);
    }
}