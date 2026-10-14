"""AutoEQ headphone correction models (port of AutoEQProfile.swift)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MAX_FILTERS = 10

PEAKING = "peaking"
LOW_SHELF = "lowShelf"
HIGH_SHELF = "highShelf"

SOURCE_BUNDLED = "bundled"
SOURCE_IMPORTED = "imported"
SOURCE_FETCHED = "fetched"


@dataclass(frozen=True)
class AutoEQFilter:
    type: str
    frequency: float
    gain_db: float
    q: float

    def to_json(self):
        return {"type": self.type, "frequency": self.frequency, "gainDB": self.gain_db, "q": self.q}

    @classmethod
    def from_json(cls, d):
        return cls(str(d["type"]), float(d["frequency"]), float(d["gainDB"]), float(d["q"]))

    @property
    def engine_code(self) -> str:
        return {PEAKING: "P", LOW_SHELF: "L", HIGH_SHELF: "H"}.get(self.type, "P")


@dataclass
class AutoEQProfile:
    id: str
    name: str
    source: str
    preamp_db: float
    filters: list[AutoEQFilter]
    measured_by: str | None = None
    optimized_sample_rate: float = 48000.0

    def validated(self) -> "AutoEQProfile":
        valid = [f for f in self.filters if f.frequency > 0 and f.q > 0 and abs(f.gain_db) <= 30
                 and all(math.isfinite(v) for v in (f.frequency, f.q, f.gain_db))]
        return AutoEQProfile(self.id, self.name, self.source, max(-30.0, min(30.0, self.preamp_db)),
                             valid[:MAX_FILTERS], self.measured_by, self.optimized_sample_rate)

    def to_json(self):
        return {
            "id": self.id, "name": self.name, "source": self.source, "preampDB": self.preamp_db,
            "filters": [f.to_json() for f in self.filters], "measuredBy": self.measured_by,
            "optimizedSampleRate": self.optimized_sample_rate,
        }

    @classmethod
    def from_json(cls, d):
        return cls(
            id=str(d["id"]), name=str(d["name"]), source=str(d.get("source", SOURCE_IMPORTED)),
            preamp_db=float(d.get("preampDB", 0)),
            filters=[AutoEQFilter.from_json(f) for f in d.get("filters", [])],
            measured_by=d.get("measuredBy"),
            optimized_sample_rate=float(d.get("optimizedSampleRate", 48000)),
        )


@dataclass(frozen=True)
class AutoEQCatalogEntry:
    id: str
    name: str
    measured_by: str
    relative_path: str

    def to_json(self):
        return {"id": self.id, "name": self.name, "measuredBy": self.measured_by, "relativePath": self.relative_path}

    @classmethod
    def from_json(cls, d):
        return cls(str(d["id"]), str(d["name"]), str(d["measuredBy"]), str(d["relativePath"]))


@dataclass
class AutoEQSelection:
    profile_id: str
    is_enabled: bool = True

    def to_json(self):
        return {"profileID": self.profile_id, "isEnabled": self.is_enabled}

    @classmethod
    def from_json(cls, d):
        if not isinstance(d, dict) or "profileID" not in d:
            return None
        return cls(str(d["profileID"]), bool(d.get("isEnabled", True)))


def slugify(name: str) -> str:
    s = name.lower().replace(" ", "-")
    return "".join(ch for ch in s if ch.isalnum() or ch == "-")


@dataclass
class SearchResult:
    entries: list[AutoEQCatalogEntry] = field(default_factory=list)
    total_count: int = 0
