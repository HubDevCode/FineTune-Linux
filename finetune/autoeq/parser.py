"""EqualizerAPO ParametricEQ.txt parser and AutoEQ INDEX.md parser."""
from __future__ import annotations

import urllib.parse
import uuid

from finetune.models.autoeq import (
    HIGH_SHELF, LOW_SHELF, MAX_FILTERS, PEAKING, SOURCE_IMPORTED,
    AutoEQCatalogEntry, AutoEQFilter, AutoEQProfile, slugify,
)

_TYPES = {"PK": PEAKING, "PEQ": PEAKING, "LS": LOW_SHELF, "LSC": LOW_SHELF, "HS": HIGH_SHELF, "HSC": HIGH_SHELF}

SOURCE_PRIORITY = ["oratory1990", "crinacle", "Rtings", "Innerfidelity", "Super Review", "Headphone.com Legacy"]


def _float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_preamp(line: str) -> float:
    parts = line.split(":", 1)
    if len(parts) < 2:
        return 0.0
    tokens = parts[1].split()
    v = _float(tokens[0]) if tokens else None
    if v is None:
        return 0.0
    return max(-30.0, min(30.0, v))


def _value_after(keyword: str, tokens: list[str]):
    for i, t in enumerate(tokens):
        if t.lower() == keyword.lower() and i + 1 < len(tokens):
            return _float(tokens[i + 1])
    return None


def _parse_filter(line: str):
    tokens = line.split()
    if not any(t.upper() == "ON" for t in tokens):
        return None
    ftype = next((_TYPES[t.upper()] for t in tokens if t.upper() in _TYPES), None)
    if ftype is None:
        return None
    fc, gain, q = _value_after("Fc", tokens), _value_after("Gain", tokens), _value_after("Q", tokens)
    if fc is None or gain is None or q is None:
        return None
    if not (fc > 0 and q > 0 and abs(gain) <= 30):
        return None
    return AutoEQFilter(ftype, fc, gain, q)


def parse(text: str, name: str, source: str, id: str | None = None) -> AutoEQProfile | None:
    preamp = 0.0
    filters = []
    for line in text.splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        low = t.lower()
        if low.startswith("preamp:"):
            preamp = _parse_preamp(t)
        elif low.startswith("filter"):
            f = _parse_filter(t)
            if f:
                filters.append(f)
    filters = filters[:MAX_FILTERS]
    if not filters:
        return None
    if id is None:
        id = str(uuid.uuid4()).upper() if source == SOURCE_IMPORTED else slugify(name)
    return AutoEQProfile(id=id, name=name, source=source, preamp_db=preamp, filters=filters)


def _parse_catalog_line(line: str):
    t = line.strip()
    if not t.startswith("- ["):
        return None
    lb = t.find("](")
    if lb < 0:
        return None
    name = t[3:lb]
    if not name:
        return None
    end = t.rfind(") by ")
    if end < 0 or end < lb:
        return None
    raw = t[lb + 2:end]
    if raw.startswith("./"):
        raw = raw[2:]
    rel = urllib.parse.unquote(raw)
    src_rig = t[end + 5:]
    on = src_rig.find(" on ")
    measured = (src_rig[:on] if on >= 0 else src_rig).strip()
    if not measured:
        return None
    return AutoEQCatalogEntry(slugify(name), name, measured, rel)


def parse_index_markdown(text: str) -> list[AutoEQCatalogEntry]:
    best: dict[str, tuple] = {}
    for line in text.splitlines():
        e = _parse_catalog_line(line)
        if not e:
            continue
        key = e.name.lower()
        prio = SOURCE_PRIORITY.index(e.measured_by) if e.measured_by in SOURCE_PRIORITY else len(SOURCE_PRIORITY)
        if key not in best or prio < best[key][1]:
            best[key] = (e, prio)
    return sorted((v[0] for v in best.values()), key=lambda e: e.name.lower())
