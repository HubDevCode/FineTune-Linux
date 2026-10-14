"""10-band graphic EQ model and the 20 built-in presets (port of EQSettings/EQPreset)."""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field

BAND_COUNT = 10
MAX_GAIN_DB = 12.0
MIN_GAIN_DB = -12.0
FREQUENCIES = [31.25, 62.5, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
GRAPHIC_EQ_Q = 1.4


def _normalize(gains) -> list[float]:
    gains = [float(g) for g in (gains or [])]
    if len(gains) >= BAND_COUNT:
        return gains[:BAND_COUNT]
    return gains + [0.0] * (BAND_COUNT - len(gains))


@dataclass
class EQSettings:
    band_gains: list[float] = field(default_factory=lambda: [0.0] * BAND_COUNT)
    is_enabled: bool = True

    def __post_init__(self):
        self.band_gains = _normalize(self.band_gains)

    @property
    def clamped_gains(self) -> list[float]:
        return [max(MIN_GAIN_DB, min(MAX_GAIN_DB, g)) if math.isfinite(g) else 0.0 for g in self.band_gains]

    def to_json(self) -> dict:
        return {"bandGains": list(self.band_gains), "isEnabled": self.is_enabled}

    @classmethod
    def from_json(cls, d) -> "EQSettings":
        if not isinstance(d, dict):
            return cls()
        return cls(_normalize(d.get("bandGains")), bool(d.get("isEnabled", True)))

    def copy(self) -> "EQSettings":
        return EQSettings(list(self.band_gains), self.is_enabled)

    def is_flat(self) -> bool:
        return all(abs(g) < 1e-6 for g in self.band_gains)


FLAT = EQSettings()


@dataclass(frozen=True)
class EQPreset:
    key: str
    name: str
    category: str
    gains: tuple


# Bands: 31, 62, 125, 250, 500, 1k, 2k, 4k, 8k, 16k
PRESETS: list[EQPreset] = [
    # Utility
    EQPreset("flat", "Flat", "Utility", (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
    EQPreset("bassBoost", "Bass Boost", "Utility", (6, 6, 5, -1, 0, 0, 0, 0, 0, 0)),
    EQPreset("bassCut", "Bass Cut", "Utility", (-6, -5, -4, -2, 0, 0, 0, 0, 0, 0)),
    EQPreset("trebleBoost", "Treble Boost", "Utility", (0, 0, 0, 0, 0, 0, 2, 4, 5, 6)),
    # Speech
    EQPreset("vocalClarity", "Vocal Clarity", "Speech", (-4, -2, -1, -3, 0, 2, 4, 4, 1, 0)),
    EQPreset("podcast", "Podcast", "Speech", (-6, -4, -2, -1, 0, 2, 4, 3, 1, 0)),
    EQPreset("spokenWord", "Spoken Word", "Speech", (-8, -6, -3, -2, 0, 2, 4, 4, 2, 0)),
    # Listening
    EQPreset("loudness", "Loudness", "Listening", (5, 4, 2, 0, -2, -2, 0, 2, 4, 5)),
    EQPreset("lateNight", "Late Night", "Listening", (-6, -4, -2, 0, 0, 1, 2, 2, 1, 0)),
    EQPreset("smallSpeakers", "Small Speakers", "Listening", (3, 4, 5, 2, 0, 1, 2, 2, 1, 0)),
    # Music
    EQPreset("rock", "Rock", "Music", (4, 3, 2, 0, -1, 0, 2, 3, 2, 1)),
    EQPreset("pop", "Pop", "Music", (3, 3, 2, 0, -1, 1, 2, 3, 3, 4)),
    EQPreset("electronic", "Electronic", "Music", (7, 6, 4, 0, -2, -2, 1, 3, 4, 3)),
    EQPreset("jazz", "Jazz", "Music", (3, 2, 1, 0, 0, 0, 1, 2, 2, 1)),
    EQPreset("classical", "Classical", "Music", (0, 0, 0, 0, 0, 0, 1, 2, 2, 2)),
    EQPreset("hipHop", "Hip-Hop", "Music", (6, 5, 4, 0, -1, 0, 2, 3, 4, 3)),
    EQPreset("rnb", "R&B", "Music", (4, 4, 3, 1, -1, 0, 2, 3, 3, 2)),
    EQPreset("deep", "Deep", "Music", (5, 6, 4, 1, -2, -2, 0, 1, 2, 1)),
    EQPreset("acoustic", "Acoustic", "Music", (0, 1, 2, 2, 1, 0, 1, 2, 2, 1)),
    # Media
    EQPreset("movie", "Movie", "Media", (4, 4, 3, -1, -1, 1, 3, 3, 2, 1)),
]

CATEGORIES = ["Utility", "Speech", "Listening", "Music", "Media"]


def presets_for(category: str) -> list[EQPreset]:
    return [p for p in PRESETS if p.category == category]


def preset_by_key(key: str) -> EQPreset | None:
    return next((p for p in PRESETS if p.key == key), None)


def matching_preset(gains) -> EQPreset | None:
    g = _normalize(gains)
    for p in PRESETS:
        if all(abs(a - b) < 0.05 for a, b in zip(g, p.gains)):
            return p
    return None


@dataclass
class UserEQPreset:
    """A named user EQ curve. Only band gains are applied; isEnabled is per-app state."""
    name: str
    settings: EQSettings
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    created_at: float = field(default_factory=time.time)

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "settings": self.settings.to_json(), "createdAt": self.created_at}

    @classmethod
    def from_json(cls, d) -> "UserEQPreset | None":
        try:
            return cls(
                name=str(d["name"]),
                settings=EQSettings.from_json(d.get("settings")),
                id=str(d.get("id") or uuid.uuid4()),
                created_at=float(d.get("createdAt", time.time())),
            )
        except (KeyError, TypeError, ValueError):
            return None


def format_frequency(f: float) -> str:
    if f >= 1000:
        k = f / 1000
        return f"{k:g}k"
    return f"{round(f):d}"
