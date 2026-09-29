use serde::{Deserialize, Serialize};

/// Livello di boost del volume per-app (moltiplicatore di gain PCM).
/// Port di `FineTune/Models/BoostLevel.swift`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BoostLevel {
    X1,
    X2,
    X3,
    X4,
}

impl Default for BoostLevel {
    fn default() -> Self {
        BoostLevel::X1
    }
}

impl BoostLevel {
    pub fn factor(self) -> f32 {
        match self {
            BoostLevel::X1 => 1.0,
            BoostLevel::X2 => 2.0,
            BoostLevel::X3 => 3.0,
            BoostLevel::X4 => 4.0,
        }
    }

    pub fn from_factor(f: f32) -> Self {
        if f >= 4.0 {
            BoostLevel::X4
        } else if f >= 3.0 {
            BoostLevel::X3
        } else if f >= 2.0 {
            BoostLevel::X2
        } else {
            BoostLevel::X1
        }
    }

    /// Valore persistito (1.0, 2.0, 3.0, 4.0). Port di `BoostLevel.rawValue`.
    pub fn raw_value(self) -> f32 {
        self.factor()
    }

    /// Costruisce da un raw value persistito (1x..4x). None per valori fuori range.
    pub fn from_raw_value(raw: f32) -> Option<Self> {
        let level = Self::from_factor(raw);
        if level.factor() == raw {
            Some(level)
        } else {
            None
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            BoostLevel::X1 => "1x",
            BoostLevel::X2 => "2x",
            BoostLevel::X3 => "3x",
            BoostLevel::X4 => "4x",
        }
    }

    /// Livello successivo (ciclo: 1x → 2x → 3x → 4x → 1x).
    pub fn next(self) -> Self {
        match self {
            BoostLevel::X1 => BoostLevel::X2,
            BoostLevel::X2 => BoostLevel::X3,
            BoostLevel::X3 => BoostLevel::X4,
            BoostLevel::X4 => BoostLevel::X1,
        }
    }

    pub fn is_boosted(self) -> bool {
        self != BoostLevel::X1
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cycles_through_levels() {
        let mut b = BoostLevel::X1;
        assert_eq!(b.next(), BoostLevel::X2);
        b = b.next();
        assert_eq!(b.next(), BoostLevel::X3);
        b = b.next();
        assert_eq!(b.next(), BoostLevel::X4);
        b = b.next();
        assert_eq!(b.next(), BoostLevel::X1);
    }
}