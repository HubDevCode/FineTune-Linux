use std::fs;
use std::path::PathBuf;
use std::time::{Duration, SystemTime};

use super::parser::AutoEQParser;
use super::profile::{slugify, AutoEQCatalogEntry, AutoEQProfile};

/// Stato di fetch del catalogo.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FetchState {
    Idle,
    Loading,
    Loaded,
    Error(String),
}

#[derive(Debug)]
pub enum FetchError {
    InvalidUrl,
    ProfileNotFound(String),
    InvalidData,
    ParseFailed(String),
    Network(String),
}

impl std::fmt::Display for FetchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FetchError::InvalidUrl => write!(f, "Invalid profile URL"),
            FetchError::ProfileNotFound(name) => write!(f, "Profile not found: {name}"),
            FetchError::InvalidData => write!(f, "Invalid profile data"),
            FetchError::ParseFailed(name) => write!(f, "Failed to parse profile: {name}"),
            FetchError::Network(e) => write!(f, "Network error: {e}"),
        }
    }
}

/// Priorità sorgenti per deduplicazione (indice minore = preferita).
const SOURCE_PRIORITY: &[&str] = &[
    "oratory1990",
    "crinacle",
    "Rtings",
    "Innerfidelity",
    "Super Review",
    "Headphone.com Legacy",
];

const INDEX_URL: &str = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results/INDEX.md";
const PROFILE_BASE_URL: &str = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results/";

/// TTL cache del catalogo: 7 giorni.
const CATALOG_TTL: Duration = Duration::from_secs(7 * 24 * 3600);

fn cache_dir() -> PathBuf {
    let base = std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share"))
        })
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("finetune")
}

fn catalog_cache_file() -> PathBuf {
    cache_dir().join("autoeq-catalog.json")
}

fn fetched_profiles_dir() -> PathBuf {
    cache_dir().join("AutoEQ").join("fetched")
}

/// Fetch dei profili AutoEQ dal repository GitHub upstream, con cache su disco.
/// Port fedele di `AutoEQFetcher.swift`.
pub struct AutoEQFetcher {
    catalog: Vec<AutoEQCatalogEntry>,
    catalog_state: FetchState,
    transport: Box<dyn HttpGet>,
}

/// Astrae l'HTTP per renderlo testabile.
pub(crate) trait HttpGet {
    fn get_text(&self, url: &str) -> Result<String, String>;
}

struct UreqTransport;

impl HttpGet for UreqTransport {
    fn get_text(&self, url: &str) -> Result<String, String> {
        ureq::get(url)
            .call()
            .map_err(|e| e.to_string())?
            .into_string()
            .map_err(|e| e.to_string())
    }
}

impl AutoEQFetcher {
    pub fn new() -> Self {
        Self::new_with_transport(Box::new(UreqTransport))
    }

    pub(crate) fn new_with_transport(transport: Box<dyn HttpGet>) -> Self {
        Self {
            catalog: vec![],
            catalog_state: FetchState::Idle,
            transport,
        }
    }

    fn catalog_cache_stale(file: &PathBuf) -> bool {
        let modified = fs::metadata(file).and_then(|m| m.modified()).ok();
        match modified {
            Some(t) => SystemTime::now().duration_since(t).map(|d| d > CATALOG_TTL).unwrap_or(true),
            None => true,
        }
    }

    /// Carica catalogo da cache, poi aggiorna da GitHub se stale.
    pub fn load_catalog(&mut self) {
        if let Some(cached) = Self::load_catalog_from_cache() {
            self.catalog = cached;
            self.catalog_state = FetchState::Loaded;
            log::info!("Loaded {} catalog entries from cache", self.catalog.len());
            if Self::catalog_cache_stale(&catalog_cache_file()) {
                self.refresh_catalog_from_github();
            }
            return;
        }
        self.refresh_catalog_from_github();
    }

    /// Fetch del catalogo dal INDEX.md di GitHub e cache su disco.
    pub fn refresh_catalog_from_github(&mut self) {
        self.catalog_state = FetchState::Loading;
        match self.transport.get_text(INDEX_URL) {
            Ok(text) => {
                let entries = Self::parse_index_markdown(&text);
                self.catalog = entries.clone();
                self.catalog_state = FetchState::Loaded;
                log::info!("Fetched {} catalog entries from GitHub", entries.len());
                Self::save_catalog_to_cache(&entries);
            }
            Err(e) => {
                if self.catalog.is_empty() {
                    self.catalog_state = FetchState::Error(format!("Network error: {e}"));
                }
                log::error!("Catalog fetch failed: {e}");
            }
        }
    }

    /// Fetch di un singolo profilo. Cache locale prima, poi GitHub.
    pub fn fetch_profile(&mut self, entry: &AutoEQCatalogEntry) -> Result<AutoEQProfile, FetchError> {
        if let Some(cached) = self.load_cached_profile(entry) {
            return Ok(cached);
        }

        // l'url della pagina del processo: nome file = `{componente} ParametricEQ.txt`.
        let last_component = entry.relative_path.split('/').last().unwrap_or(&entry.name);
        let file_name = format!("{last_component} ParametricEQ.txt");
        let url = format!(
            "{PROFILE_BASE_URL}{}/{file_name}",
            percent_encode_path(&entry.relative_path)
        );

        let text = self.transport.get_text(&url).map_err(FetchError::Network)?;
        let profile = AutoEQParser::parse(&text, &entry.name, super::profile::AutoEQSource::Fetched, Some(&entry.id))
            .ok_or_else(|| FetchError::ParseFailed(entry.name.clone()))?;

        // Arricchisci con il source di misurazione dal catalogo.
        let profile = AutoEQProfile {
            measured_by: Some(entry.measured_by.clone()),
            ..profile
        };

        self.cache_profile_text(&text, &entry.id);
        Ok(profile)
    }

    pub fn catalog(&self) -> &[AutoEQCatalogEntry] {
        &self.catalog
    }

    pub fn catalog_state(&self) -> &FetchState {
        &self.catalog_state
    }

    fn catalog_entry(&self, id: &str) -> Option<&AutoEQCatalogEntry> {
        self.catalog.iter().find(|e| e.id == id)
    }

    /// Risolve un profilo per ID: memoria → cache → rete.
    pub fn resolve_profile(&mut self, id: &str) -> Result<Option<AutoEQProfile>, FetchError> {
        if let Some(entry) = self.catalog_entry(id).cloned() {
            self.fetch_profile(&entry).map(Some)
        } else {
            Ok(None)
        }
    }

    pub fn has_cached_profile(id: &str) -> bool {
        fetched_profiles_dir().join(format!("{id}.txt")).is_file()
    }

    /// Parsa INDEX.md in voci catalogo con deduplicazione (priorità src).
    pub fn parse_index_markdown(text: &str) -> Vec<AutoEQCatalogEntry> {
        use std::collections::HashMap;

        let mut best_by_name: HashMap<String, (AutoEQCatalogEntry, usize)> = HashMap::new();

        for line in text.lines() {
            if let Some(entry) = Self::parse_catalog_line(line) {
                let normalized_name = entry.name.to_lowercase();
                let priority = Self::source_priority_index(&entry.measured_by);

                match best_by_name.get(&normalized_name) {
                    Some((_, existing_priority)) if priority >= *existing_priority => {}
                    _ => {
                        best_by_name.insert(normalized_name, (entry, priority));
                    }
                }
            }
        }

        let mut entries: Vec<AutoEQCatalogEntry> = best_by_name.into_values().map(|(e, _)| e).collect();
        entries.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
        entries
    }

    /// Parsa una singola riga INDEX.md.
    pub fn parse_catalog_line(line: &str) -> Option<AutoEQCatalogEntry> {
        let trimmed = line.trim();
        if !trimmed.starts_with("- [") {
            return None;
        }

        // Confine "](" tra nome e URL.
        let link_boundary = trimmed.find("](")?;

        let name_start = 3; // salta "- ["
        let name = &trimmed[name_start..link_boundary];
        if name.is_empty() {
            return None;
        }

        // " by " più a destra: l'URL può contenere "()" nel percorso.
        let url_end = trimmed.rfind(") by ")?;

        let mut raw_path = &trimmed[link_boundary + 2..url_end];

        if let Some(stripped) = raw_path.strip_prefix("./") {
            raw_path = stripped;
        }

        // URL-decode: gli spazi sono %20 nel INDEX.md
        let relative_path = percent_decode(raw_path);

        let source_and_rig = &trimmed[url_end + 5..];
        let measured_by = match source_and_rig.find(" on ") {
            Some(i) => &source_and_rig[..i],
            None => source_and_rig.trim(),
        };
        if measured_by.is_empty() {
            return None;
        }

        Some(AutoEQCatalogEntry {
            id: slugify(name),
            name: name.to_string(),
            measured_by: measured_by.to_string(),
            relative_path,
        })
    }

    fn source_priority_index(source: &str) -> usize {
        SOURCE_PRIORITY.iter().position(|s| *s == source).unwrap_or(SOURCE_PRIORITY.len())
    }

    pub fn load_catalog_from_cache() -> Option<Vec<AutoEQCatalogEntry>> {
        let file = catalog_cache_file();
        if !file.is_file() {
            return None;
        }
        match fs::read_to_string(&file) {
            Ok(text) => serde_json::from_str(&text).ok(),
            Err(e) => {
                log::warn!("Failed to load catalog cache: {e}");
                None
            }
        }
    }

    fn save_catalog_to_cache(entries: &[AutoEQCatalogEntry]) {
        let file = catalog_cache_file();
        let dir = file.parent().map(|d| d.to_path_buf()).unwrap_or_default();
        if let Err(e) = fs::create_dir_all(&dir) {
            log::warn!("Failed to create cache dir: {e}");
            return;
        }
        match serde_json::to_vec(entries) {
            Ok(data) => {
                if let Err(e) = fs::write(&file, data) {
                    log::warn!("Failed to save catalog cache: {e}");
                }
            }
            Err(e) => log::warn!("Failed to serialize catalog cache: {e}"),
        }
    }

    fn cache_profile_text(&self, text: &str, id: &str) {
        let dir = fetched_profiles_dir();
        if let Err(e) = fs::create_dir_all(&dir) {
            log::warn!("Failed to create profile cache dir: {e}");
            return;
        }
        if let Err(e) = fs::write(dir.join(format!("{id}.txt")), text) {
            log::warn!("Failed to cache profile {id}: {e}");
        }
    }

    fn load_cached_profile(&self, entry: &AutoEQCatalogEntry) -> Option<AutoEQProfile> {
        let file = fetched_profiles_dir().join(format!("{}.txt", entry.id));
        let text = fs::read_to_string(&file).ok()?;
        let profile =
            AutoEQParser::parse(&text, &entry.name, super::profile::AutoEQSource::Fetched, Some(&entry.id))?;
        Some(AutoEQProfile {
            measured_by: Some(entry.measured_by.clone()),
            ..profile
        })
    }
}

fn percent_encode_path(path: &str) -> String {
    path.split('/')
        .map(|component| component.bytes().map(percent_encode_byte).collect::<String>())
        .collect::<Vec<_>>()
        .join("/")
}

fn percent_encode_byte(b: u8) -> String {
    if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.' | b'_' | b'~') {
        (b as char).to_string()
    } else {
        format!("%{b:02X}")
    }
}

fn percent_decode(s: &str) -> String {
    let mut out = Vec::with_capacity(s.len());
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hi = (bytes[i + 1] as char).to_digit(16);
            let lo = (bytes[i + 2] as char).to_digit(16);
            if let (Some(hi), Some(lo)) = (hi, lo) {
                out.push((hi * 16 + lo) as u8);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(name: &str, measured_by: &str) -> AutoEQCatalogEntry {
        AutoEQCatalogEntry {
            id: slugify(name),
            name: name.to_string(),
            measured_by: measured_by.to_string(),
            relative_path: format!("{measured_by}/over-ear/{name}"),
        }
    }

    const SAMPLE_INDEX: &str = "\
# Results

- [AKG K240 Studio](./oratory1990/over-ear/AKG%20K240%20Studio) by oratory1990
- [Sennheiser HD 600](./oratory1990/over-ear/Sennheiser%20HD%20600) by oratory1990
- [Focal Utopia](./focal/over-ear/Focal%20Utopia) by Focal
- [64 Audio A12t (m15 Apex module)](./crinacle/in-ear/64%20Audio%20A12t%20(m15%20Apex%20module)) by crinacle
- [AKG K240 Studio](./crinacle/over-ear/AKG%20K240%20Studio) by crinacle
- [Sony WH-1000XM4](./Rtings/over-ear/Sony%20WH-1000XM4) by Rtings on Headphone Stand
";

    #[test]
    fn parses_index_with_dedup_and_priority() {
        let entries = AutoEQFetcher::parse_index_markdown(SAMPLE_INDEX);
        // AKG duplicate: oratory1990 (priority 0) wins over crinacle (priority 1)
        let akg = entries.iter().find(|e| e.name == "AKG K240 Studio").unwrap();
        assert_eq!(akg.measured_by, "oratory1990");

        // Nome con parentesi — il path decodificato mantiene le parentesi
        assert!(entries.iter().any(|e| e.name == "64 Audio A12t (m15 Apex module)"));

        // " on Rig" non fa parte del misuratore
        let sony = entries.iter().find(|e| e.name == "Sony WH-1000XM4").unwrap();
        assert_eq!(sony.measured_by, "Rtings");
    }

    #[test]
    fn percent_encoding_roundtrip() {
        let decoded = "oratory1990/over-ear/AKG K240 Studio (sample)";
        let encoded = percent_encode_path(decoded);
        assert_eq!(percent_decode(&encoded), decoded);
    }

    #[test]
    fn cache_roundtrip() {
        let entries = vec![entry("HD 600", "oratory1990")];
        let file = std::env::temp_dir().join(format!("finetune-catalog-cache-{}.json", std::process::id()));

        // Salva nel file temporaneo (la cache reale scala via XDG_DATA_HOME; qui test di codifica)
        let data = serde_json::to_vec(&entries).unwrap();
        fs::write(&file, &data).unwrap();
        let loaded: Vec<AutoEQCatalogEntry> = serde_json::from_slice(&fs::read(&file).unwrap()).unwrap();
        assert_eq!(loaded, entries);
        let _ = fs::remove_file(&file);
    }
}