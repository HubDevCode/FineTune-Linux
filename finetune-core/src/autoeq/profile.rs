use serde::{Deserialize, Serialize};
use crate::dsp::FilterType as DSPFilterType;

/// Tipo di filtro biquad AutoEQ (port di `AutoEQFilter.FilterType`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum FilterType {
    Peaking,
    LowShelf,
    HighShelf,
}

impl FilterType {
    pub fn parse(s: &str) -> Option<Self> {
        match s.to_uppercase().as_str() {
            "PK" | "PEQ" => Some(FilterType::Peaking),
            "LS" | "LSC" => Some(FilterType::LowShelf),
            "HS" | "HSC" => Some(FilterType::HighShelf),
            _ => None,
        }
    }
}

impl From<FilterType> for DSPFilterType {
    fn from(ft: FilterType) -> Self {
        match ft {
            FilterType::Peaking => DSPFilterType::Peaking,
            FilterType::LowShelf => DSPFilterType::LowShelf,
            FilterType::HighShelf => DSPFilterType::HighShelf,
        }
    }
}

/// Singolo filtro biquad in un profilo AutoEQ.
/// Port fedele di `FineTune/Models/AutoEQProfile.swift` → `AutoEQFilter`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct AutoEQFilter {
    pub filter_type: FilterType,
    /// Frequenza in Hz.
    pub frequency: f64,
    /// Guadagno in dB (±30).
    pub gain_db: f32,
    /// Quality factor.
    pub q: f64,
}

/// Provenienza del profilo (port di `AutoEQSource`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AutoEQSource {
    Bundled,
    Imported,
    Fetched,
}

/// Profilo di correzione cuffie/speaker (port di `AutoEQProfile`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AutoEQProfile {
    pub id: String,
    pub name: String,
    pub source: AutoEQSource,
    /// Preamp negativo per prevenire clipping (dB, ±30).
    pub preamp_db: f32,
    pub filters: Vec<AutoEQFilter>,
    /// Sorgente di misurazione (es. "oratory1990", "crinacle"). None per profili importati.
    pub measured_by: Option<String>,
    /// Sample rate per cui i parametri sono ottimizzati (Hz, default 48000).
    pub optimized_sample_rate: f64,
}

pub const MAX_FILTERS: usize = 10;

/// Slug URL-safe del nome (lowercase, spazi → trattini).
/// Port di `AutoEQProfileManager.slugify`.
pub(crate) fn slugify(name: &str) -> String {
    name.to_lowercase()
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else if c == '-' { '-' } else { ' ' })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join("-")
}

impl AutoEQProfile {
    pub const MAX_FILTERS: usize = MAX_FILTERS;

    /// Valida i filtri (stesse regole del parser): frequency > 0, q > 0, |gain| ≤ 30,
    /// max 10 filtri, preamp clamped a ±30.
    pub fn validated(&self) -> Self {
        let valid_filters: Vec<_> = self
            .filters
            .iter()
            .filter(|f| f.frequency > 0.0 && f.q > 0.0 && f.gain_db.abs() <= 30.0)
            .cloned()
            .take(Self::MAX_FILTERS)
            .collect();
        let clamped_preamp = self.preamp_db.clamp(-30.0, 30.0);
        AutoEQProfile {
            id: self.id.clone(),
            name: self.name.clone(),
            source: self.source,
            preamp_db: clamped_preamp,
            filters: valid_filters,
            measured_by: self.measured_by.clone(),
            optimized_sample_rate: self.optimized_sample_rate,
        }
    }
}

/// Voce di catalogo leggera (senza filtri), dal file INDEX.md di AutoEQ.
/// Port di `AutoEQCatalogEntry`.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct AutoEQCatalogEntry {
    /// Slug del nome (lowercase, trattini al posto di spazi).
    pub id: String,
    pub name: String,
    pub measured_by: String,
    pub relative_path: String,
}

/// Selezione AutoEQ per un dispositivo (persistita in settings).
/// Port di `AutoEQSelection`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AutoEQSelection {
    pub profile_id: String,
    pub is_enabled: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_fails_on_invalid_frequency() {
        let p = AutoEQProfile {
            id: "test".into(),
            name: "Test".into(),
            source: AutoEQSource::Imported,
            preamp_db: -5.0,
            filters: vec![AutoEQFilter {
                filter_type: FilterType::Peaking,
                frequency: 0.0,
                gain_db: 6.0,
                q: 0.7,
            }],
            measured_by: None,
            optimized_sample_rate: 48000.0,
        };
        assert_eq!(p.validated().filters.len(), 0);
    }

    #[test]
    fn validate_clamps_preamp() {
        let mut p = AutoEQProfile {
            id: "test".into(),
            name: "Test".into(),
            source: AutoEQSource::Imported,
            preamp_db: -50.0,
            filters: vec![],
            measured_by: None,
            optimized_sample_rate: 48000.0,
        };
        p.filters.push(AutoEQFilter {
            filter_type: FilterType::Peaking,
            frequency: 1000.0,
            gain_db: 6.0,
            q: 0.7,
        });
        let v = p.validated();
        assert_eq!(v.preamp_db, -30.0);
    }
}