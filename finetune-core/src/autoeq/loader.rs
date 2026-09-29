use std::fs;
use std::path::PathBuf;

use super::parser::AutoEQParser;
use super::profile::{AutoEQProfile, AutoEQSource};

/// I/O su disco per i profili AutoEQ importati.
/// Port fedele di `AutoEQProfileLoader.swift`.
pub struct AutoEQProfileLoader {
    import_directory: PathBuf,
}

fn data_dir() -> PathBuf {
    let base = std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME")
                .map(|h| PathBuf::from(h).join(".local/share"))
        })
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("finetune").join("AutoEQ")
}

impl AutoEQProfileLoader {
    pub fn new() -> Self {
        Self { import_directory: data_dir() }
    }

    /// Carica i profili importati (sincrono). `import_directory` deve esistere.
    pub fn load_imported_profiles(&self) -> Vec<AutoEQProfile> {
        let dir = &self.import_directory;
        if !dir.is_dir() {
            return vec![];
        }

        let mut profiles = vec![];
        let entries = match fs::read_dir(dir) {
            Ok(e) => e,
            Err(e) => {
                log::error!("Failed to read import dir {:?}: {e}", dir);
                return vec![];
            }
        };

        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("txt") {
                continue;
            }
            let text = match fs::read_to_string(&path) {
                Ok(t) => t,
                Err(e) => {
                    log::error!("Failed to read {:?}: {e}", path);
                    continue;
                }
            };
            let stable_id = path
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();

            let name_file = dir.join(format!("{stable_id}.name"));
            let display_name = fs::read_to_string(&name_file)
                .map(|s| s.trim().to_string())
                .unwrap_or_else(|_| stable_id.clone());

            if let Some(profile) =
                AutoEQParser::parse(&text, &display_name, AutoEQSource::Imported, Some(&stable_id))
            {
                profiles.push(profile);
            }
        }
        profiles
    }

    /// Importa un file `ParametricEQ.txt`, copiandolo nella directory di importazione.
    pub fn import_profile(&self, text: &str, name: &str) -> Option<AutoEQProfile> {
        let profile = AutoEQParser::parse(text, name, super::profile::AutoEQSource::Imported, None)?;

        let dir = &self.import_directory;
        if let Err(e) = fs::create_dir_all(dir) {
            log::error!("Failed to create import dir {:?}: {e}", dir);
            return None;
        }
        let dest = dir.join(format!("{}.txt", profile.id));
        let name_file = dir.join(format!("{}.name", profile.id));
        if let Err(e) = fs::write(&dest, text) {
            log::error!("Failed to write {:?}: {e}", dest);
            return None;
        }
        if let Err(e) = fs::write(&name_file, name) {
            log::error!("Failed to write {:?}: {e}", name_file);
            return None;
        }
        Some(profile)
    }

    /// Elimina i file di un profilo importato.
    pub fn delete_profile_files(&self, id: &str) {
        let dir = &self.import_directory;
        let _ = fs::remove_file(dir.join(format!("{id}.txt")));
        let _ = fs::remove_file(dir.join(format!("{id}.name")));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn scratch() -> PathBuf {
        let d = std::env::temp_dir().join(format!("finetune-test-autoeq-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn roundtrip_import_load_delete() {
        let dir = scratch();
        let loader = AutoEQProfileLoader { import_directory: dir.clone() };

        let text = "Preamp: -3.0 dB\nFilter 1: ON PK Fc 100 Hz Gain 2.0 dB Q 1.0\n";
        let p = loader.import_profile(text, "My Headset").unwrap();
        assert_eq!(p.source, super::super::profile::AutoEQSource::Imported);
        assert!(dir.join(p.id.clone() + ".txt").exists());
        assert!(dir.join(p.id.clone() + ".name").exists());

        let loaded = loader.load_imported_profiles();
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].name, "My Headset");
        assert_eq!(loaded[0].preamp_db, -3.0);

        loader.delete_profile_files(&p.id);
        assert!(loader.load_imported_profiles().is_empty());
    }

    #[test]
    fn missing_dir_returns_empty() {
        let loader = AutoEQProfileLoader { import_directory: scratch().join("nope") };
        assert!(loader.load_imported_profiles().is_empty());
    }

    #[test]
    fn import_rejects_invalid_file() {
        let dir = scratch();
        let loader = AutoEQProfileLoader { import_directory: dir.clone() };
        assert!(loader.import_profile("# nothing here", "broken").is_none());
    }
}