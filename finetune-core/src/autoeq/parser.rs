use super::profile::{slugify, AutoEQFilter, AutoEQProfile, AutoEQSource, FilterType, MAX_FILTERS};

/// Parser dei file EqualizerAPO `ParametricEQ.txt`.
/// Port fedele di `FineTune/Audio/AutoEQ/AutoEQParser.swift`.
pub struct AutoEQParser;

impl AutoEQParser {
    /// Parsa un text `ParametricEQ.txt`. Ritorna `None` se non c'è nessun filtro valido.
    /// - `id`: ID esplicito; se `None`, genera slug (per fetched/bundled) o UUID (imported).
    pub fn parse(text: &str, name: &str, source: AutoEQSource, id: Option<&str>) -> Option<AutoEQProfile> {
        let mut preamp_db: f32 = 0.0;
        let mut filters: Vec<AutoEQFilter> = Vec::new();

        for raw_line in text.lines() {
            let line = raw_line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let lower = line.to_lowercase();
            if lower.starts_with("preamp:") {
                preamp_db = Self::parse_preamp(line);
            } else if lower.starts_with("filter") {
                if let Some(f) = Self::parse_filter_line(line) {
                    filters.push(f);
                }
            }
        }

        if filters.len() > MAX_FILTERS {
            filters.truncate(MAX_FILTERS);
        }
        if filters.is_empty() {
            return None;
        }

        let resolved_id = match id {
            Some(i) => i.to_string(),
            None => match source {
                AutoEQSource::Bundled | AutoEQSource::Fetched => slugify(name),
                AutoEQSource::Imported => uuid(),
            },
        };

        Some(AutoEQProfile {
            id: resolved_id,
            name: name.to_string(),
            source,
            preamp_db,
            filters,
            measured_by: None,
            optimized_sample_rate: 48000.0,
        })
    }

    /// "Preamp: -6.2 dB" — gestisce whitespace irregolare, clamp a ±30.
    fn parse_preamp(line: &str) -> f32 {
        let value_part = line.split(':').nth(1).unwrap_or("");
        let first = value_part.split_whitespace().next().unwrap_or("");
        let value: f32 = first.parse().unwrap_or(0.0);
        value.clamp(-30.0, 30.0)
    }

    /// Filtro: "Filter 1: ON PK Fc 100 Hz Gain -2.3 dB Q 1.41"
    fn parse_filter_line(line: &str) -> Option<AutoEQFilter> {
        let tokens: Vec<&str> = line.split_whitespace().collect();

        // Deve contenere "ON" (filtri disabilitati saltati)
        if !tokens.iter().any(|t| t.eq_ignore_ascii_case("ON")) {
            return None;
        }

        let filter_type = tokens.iter().find_map(|t| FilterType::parse(t))?;

        let frequency = Self::extract_value_after("Fc", &tokens)?;
        let gain_db = Self::extract_value_after("Gain", &tokens)?;
        let q = Self::extract_value_after("Q", &tokens)?;

        if frequency <= 0.0 || q <= 0.0 || gain_db.abs() > 30.0 {
            return None;
        }

        Some(AutoEQFilter {
            filter_type,
            frequency: frequency as f64,
            gain_db,
            q: q as f64,
        })
    }

    fn extract_value_after(keyword: &str, tokens: &[&str]) -> Option<f32> {
        let index = tokens.iter().position(|t| t.eq_ignore_ascii_case(keyword))?;
        let value = *tokens.get(index + 1)?;
        value.parse().ok()
    }
}

/// UUID v4 string (senza dipendenze).
fn uuid() -> String {
    let mut rng = uuid_source();
    let mut bytes = [0u8; 16];
    rng.fill_bytes(&mut bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10xx
    let hex: Vec<String> = bytes.iter().map(|b| format!("{b:02x}")).collect();
    format!(
        "{}-{}-{}-{}-{}",
        hex[0..4].concat(),
        hex[4..6].concat(),
        hex[6..8].concat(),
        hex[8..10].concat(),
        hex[10..16].concat()
    )
}

trait RngFill {
    fn fill_bytes(&mut self, bytes: &mut [u8]);
}

/// RNG semplice basato su un seed deterministico... per ID unici usiamo
/// tempo + contatore (sufficiente per identificativi locali).
struct UuidSource(std::time::SystemTime, u64);

fn uuid_source() -> UuidSource {
    UuidSource(std::time::SystemTime::now(), 0)
}

impl RngFill for UuidSource {
    fn fill_bytes(&mut self, bytes: &mut [u8]) {
        let nanos = self.0.duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(0);
        // xorshift64
        for chunk in bytes.chunks_mut(8) {
            let mut x = nanos ^ self.1.wrapping_mul(0x9E3779B97F4A7C15).rotate_left(17);
            self.1 += 0x2545F4914F6CDD1D;
            x ^= x >> 12;
            x ^= x << 25;
            x ^= x >> 27;
            let v = x.wrapping_mul(0x2545F4914F6CDD1D).to_le_bytes();
            chunk.copy_from_slice(&v[..chunk.len()]);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = "\
# AutoEq preamp
Preamp: -5.5 dB
Filter 1: ON PK Fc 100 Hz Gain -2.3 dB Q 1.41
Filter 2: ON LSC Fc 105 Hz Gain 7.0 dB Q 0.71
Filter 3: ON HS Fc 8000 Hz Gain 3.0 dB Q 0.707
Filter 4: OFF PK Fc 200 Hz Gain 5.0 dB Q 1.0
";

    #[test]
    fn parses_valid_file() {
        let p = AutoEQParser::parse(SAMPLE, "HD 600", AutoEQSource::Fetched, Some("hd600"))
            .unwrap();
        assert_eq!(p.name, "HD 600");
        assert_eq!(p.preamp_db, -5.5);
        assert_eq!(p.filters.len(), 3, "disabled filter must be skipped");
        assert_eq!(p.filters[0].filter_type, FilterType::Peaking);
        assert_eq!(p.filters[0].frequency, 100.0);
        assert_eq!(p.filters[0].gain_db, -2.3);
        assert_eq!(p.filters[1].filter_type, FilterType::LowShelf);
        assert_eq!(p.filters[2].filter_type, FilterType::HighShelf);
    }

    #[test]
    fn returns_none_without_valid_filters() {
        assert!(AutoEQParser::parse("# comment only", "x", AutoEQSource::Imported, None).is_none());
        let only_off = "Filter 1: OFF PK Fc 100 Hz Gain -2.3 dB Q 1.41";
        assert!(AutoEQParser::parse(only_off, "x", AutoEQSource::Imported, None).is_none());
    }

    #[test]
    fn truncates_to_max_filters() {
        let mut text = String::new();
        for i in 1..=20 {
            text.push_str(&format!("Filter {i}: ON PK Fc {}.0 Hz Gain 3.0 dB Q 1.0\n", i * 100));
        }
        let p = AutoEQParser::parse(&text, "x", AutoEQSource::Fetched, Some("x")).unwrap();
        assert_eq!(p.filters.len(), MAX_FILTERS);
    }

    #[test]
    fn rejects_out_of_range() {
        let bad = "Filter 1: ON PK Fc 100 Hz Gain 40.0 dB Q 1.0";
        assert!(AutoEQParser::parse(bad, "x", AutoEQSource::Imported, None).is_none());
    }

    #[test]
    fn slugifies_name() {
        assert_eq!(super::super::profile::slugify("Sennheiser HD 600"), "sennheiser-hd-600");
    }

    #[test]
    fn clamps_preamp() {
        let p = AutoEQParser::parse("Preamp: -90 dB\nFilter 1: ON PK Fc 100 Hz Gain 1.0 dB Q 1.0", "x", AutoEQSource::Fetched, Some("x")).unwrap();
        assert_eq!(p.preamp_db, -30.0);
    }
}