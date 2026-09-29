use super::profile::AutoEQCatalogEntry;

/// Risultato di `search()`.
/// Port di `AutoEQSearchResult`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AutoEQSearchResult {
    pub entries: Vec<AutoEQCatalogEntry>,
    pub total_count: usize,
}

/// Motore di ricerca fuzzy sul catalogo AutoEQ.
/// Port fedele di `AutoEQProfileManager.search()/matchScore()/bestTokenMatch()/editDistance()/normalize()`.
pub struct AutoEQSearch {
    /// Voci di catalogo pre-ordinate (per nome, case-insensitive).
    sorted_entries: Vec<AutoEQCatalogEntry>,
    /// Nomi normalizzati, in parallelo a `sorted_entries`.
    normalized_names: Vec<String>,
}

impl AutoEQSearch {
    pub fn new(catalog: Vec<AutoEQCatalogEntry>) -> Self {
        let mut sorted_entries = catalog;
        sorted_entries.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
        let normalized_names = sorted_entries.iter().map(|e| Self::normalize(&e.name)).collect();
        Self { sorted_entries, normalized_names }
    }

    pub fn entries(&self) -> &[AutoEQCatalogEntry] {
        &self.sorted_entries
    }

    /// [AutoEQCatalogEntry] → JSON (per cache/trasporto).
    pub fn catalog_to_json(&self) -> String {
        serde_json::to_string(&self.sorted_entries).unwrap_or_else(|_| "[]".to_string())
    }

    /// Ricerca fuzzy con ranking di rilevanza.
    pub fn search(&self, query: &str, limit: usize) -> AutoEQSearchResult {
        if query.trim().is_empty() {
            return AutoEQSearchResult { entries: vec![], total_count: 0 };
        }

        let lowered_query = query.to_lowercase();
        let normalized_query = Self::normalize(query);

        let mut scored: Vec<(usize, i64)> = Vec::with_capacity(self.sorted_entries.len().min(200));

        for (i, entry) in self.sorted_entries.iter().enumerate() {
            let lowered_name = entry.name.to_lowercase();
            let normalized_name = &self.normalized_names[i];

            let score = Self::match_score(
                &lowered_query,
                &normalized_query,
                &lowered_name,
                &normalized_name,
            );

            if score > 0 {
                scored.push((i, score));
            }
        }

        scored.sort_by(|a, b| {
            b.1.cmp(&a.1).then_with(|| {
                self.sorted_entries[a.0].name.cmp(&self.sorted_entries[b.0].name)
            })
        });

        let total_count = scored.len();
        let entries = scored
            .into_iter()
            .take(limit)
            .map(|(i, _)| self.sorted_entries[i].clone())
            .collect();

        AutoEQSearchResult { entries, total_count }
    }

    fn match_score(
        lowered_query: &str,
        normalized_query: &str,
        lowered_name: &str,
        normalized_name: &str,
    ) -> i64 {
        // Tier 1: sottostringa esatta nel nome originale (case-insensitive)
        if lowered_name.contains(lowered_query) {
            let mut score: i64 = 100;
            if lowered_name.starts_with(lowered_query) {
                score += 50;
            }
            if lowered_name == lowered_query {
                score += 100;
            }
            score += (50 - lowered_name.len() as i64).max(0);
            return score;
        }

        // Tier 2: sottostringa normalizzata (tolleranza spazi/punteggiatura)
        if !normalized_query.is_empty() && normalized_name.contains(normalized_query) {
            let mut score: i64 = 50;
            if normalized_name.starts_with(normalized_query) {
                score += 25;
            }
            score += (25 - normalized_name.len() as i64).max(0);
            return score;
        }

        // Tier 3: match fuzzy per token
        let query_tokens: Vec<&str> = lowered_query.split_whitespace().collect();
        if query_tokens.is_empty() {
            return 0;
        }

        let mut total_token_score: i64 = 0;
        for token in &query_tokens {
            let token_score = Self::best_token_match(token, lowered_name);
            if token_score == 0 {
                return 0;
            }
            total_token_score += token_score;
        }

        (49).min(total_token_score / query_tokens.len() as i64)
    }

    fn best_token_match(token: &str, name: &str) -> i64 {
        if name.contains(token) {
            return 40;
        }

        let name_tokens: Vec<&str> = name.split(|c: char| c.is_whitespace() || c == '-').collect();
        let max_allowed_distance = if token.len() <= 4 { 1 } else { 2 };

        let mut best_score: i64 = 0;
        for name_token in name_tokens {
            let distance = Self::edit_distance(token, &name_token.to_lowercase());
            if distance <= max_allowed_distance {
                let score = (30 - distance * 10).max(1);
                best_score = best_score.max(score as i64);
            }
        }
        best_score
    }

    fn edit_distance(a: &str, b: &str) -> usize {
        let a_chars: Vec<char> = a.chars().collect();
        let b_chars: Vec<char> = b.chars().collect();
        let m = a_chars.len();
        let n = b_chars.len();

        if m == 0 {
            return n;
        }
        if n == 0 {
            return m;
        }
        if m.abs_diff(n) > 2 {
            return m.max(n);
        }

        let mut prev: Vec<usize> = (0..=n).collect();
        let mut curr: Vec<usize> = vec![0; n + 1];

        for i in 1..=m {
            curr[0] = i;
            for j in 1..=n {
                curr[j] = if a_chars[i - 1] == b_chars[j - 1] {
                    prev[j - 1]
                } else {
                    1 + prev[j - 1].min(prev[j].min(curr[j - 1]))
                };
            }
            std::mem::swap(&mut prev, &mut curr);
        }
        prev[n]
    }

    /// Rimuove tutto ciò che non è alfanumerico e abbassa il case.
    fn normalize(string: &str) -> String {
        string
            .chars()
            .filter(|c| c.is_alphanumeric())
            .collect::<String>()
            .to_lowercase()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(name: &str, measured_by: &str) -> AutoEQCatalogEntry {
        let id = name.to_lowercase().replace(' ', "-");
        AutoEQCatalogEntry {
            id: id.clone(),
            name: name.to_string(),
            measured_by: measured_by.to_string(),
            relative_path: format!("source/{name}"),
        }
    }

    fn sample_catalog() -> AutoEQSearch {
        AutoEQSearch::new(vec![
            entry("Sennheiser HD 600", "oratory1990"),
            entry("Sennheiser HD 650", "oratory1990"),
            entry("AKG K240 Studio", "oratory1990"),
            entry("Beyerdynamic DT 1990 Pro (balanced)", "crinacle"),
            entry("Sony WH-1000XM4", "Rtings"),
        ])
    }

    #[test]
    fn exact_substring_ranks_first() {
        let idx = sample_catalog();
        let r = idx.search("hd 600", 50);
        assert_eq!(r.total_count, 2);
        assert!(r.entries[0].name.contains("HD 600") || r.entries[0].name == "Sennheiser HD 600");
    }

    #[test]
    fn empty_query_returns_nothing() {
        let idx = sample_catalog();
        let r = idx.search("", 50);
        assert_eq!(r.total_count, 0);
        assert!(r.entries.is_empty());
    }

    #[test]
    fn normalized_matching_ignores_punctuation() {
        let idx = sample_catalog();
        let r = idx.search("a k g 2 4 0", 50);
        assert_eq!(r.total_count, 1, "spaces should be ignored in tier-2 matching");
        assert_eq!(r.entries[0].name, "AKG K240 Studio");
    }

    #[test]
    fn token_fuzzy_typo() {
        let idx = sample_catalog();
        // "hd 600" vs "Sennheiser HD 600" → tier 1; "hd 650" similar
        let r = idx.search("senheiser", 50);
        assert_eq!(r.total_count, 2, "typo in 'sennheiser' should fuzzy-match both");
    }

    #[test]
    fn limit_respected() {
        let idx = sample_catalog();
        let r = idx.search("h", 1);
        assert_eq!(r.entries.len(), 1);
    }

    #[test]
    fn edit_distance_basic() {
        assert_eq!(AutoEQSearch::edit_distance("kitten", "kitten"), 0);
        assert_eq!(AutoEQSearch::edit_distance("sitting", "kitten"), 3);
        assert_eq!(AutoEQSearch::edit_distance("", "abc"), 3);
    }

    #[test]
    fn sort_is_case_insensitive() {
        let idx = AutoEQSearch::new(vec![entry("apple", "a"), entry("Zebra", "z"), entry("Banana", "b")]);
        let names: Vec<&str> = idx.entries().iter().map(|e| e.name.as_str()).collect();
        assert_eq!(names, vec!["apple", "Banana", "Zebra"]);
    }
}