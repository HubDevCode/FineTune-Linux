"""AutoEQ catalog fetching, profile cache, import and fuzzy search.

Port of AutoEQFetcher, AutoEQProfileLoader and AutoEQProfileManager. Network
work runs on a worker thread; results are delivered through `dispatch`
(GLib.idle_add in the app) so UI code always runs on the main loop.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from finetune import paths
from finetune.autoeq import parser
from finetune.models.autoeq import (
    SOURCE_FETCHED, SOURCE_IMPORTED, AutoEQCatalogEntry, AutoEQProfile, SearchResult,
)

log = logging.getLogger("finetune.autoeq")

INDEX_URL = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results/INDEX.md"
PROFILE_BASE_URL = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results/"
CATALOG_TTL = 7 * 24 * 3600
USER_AGENT = "FineTune-Linux"


def _http_get(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            raise OSError(f"HTTP {r.status}")
        return r.read().decode("utf-8")


def normalize(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum()).lower()


def edit_distance(a: str, b: str) -> int:
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    if abs(m - n) > 2:
        return max(m, n)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cur[j] = prev[j - 1] if a[i - 1] == b[j - 1] else 1 + min(prev[j - 1], prev[j], cur[j - 1])
        prev = cur
    return prev[n]


def _best_token_match(token: str, name: str) -> int:
    if token in name:
        return 40
    max_d = 1 if len(token) <= 4 else 2
    best = 0
    for nt in name.replace("-", " ").split():
        d = edit_distance(token, nt.lower())
        if d <= max_d:
            best = max(best, max(1, 30 - d * 10))
    return best


def match_score(lq: str, nq: str, lname: str, nname: str) -> int:
    if lq in lname:
        score = 100
        if lname.startswith(lq):
            score += 50
        if lname == lq:
            score += 100
        return score + max(0, 50 - len(lname))
    if nq and nq in nname:
        score = 50
        if nname.startswith(nq):
            score += 25
        return score + max(0, 25 - len(nname))
    tokens = lq.split()
    if not tokens:
        return 0
    total = 0
    for t in tokens:
        s = _best_token_match(t, lname)
        if s == 0:
            return 0
        total += s
    return min(49, total // len(tokens))


class AutoEQManager:
    def __init__(self, dispatch=None, base_dir: Path | None = None, network: bool = True):
        self._dispatch = dispatch or (lambda fn, *a: fn(*a))
        base = Path(base_dir) if base_dir else paths.data_dir()
        self.import_dir = base / "AutoEQ"
        self.fetched_dir = base / "AutoEQ" / "fetched"
        self.catalog_cache = base / "autoeq-catalog.json"
        self.catalog: list[AutoEQCatalogEntry] = []
        self.catalog_state = "idle"   # idle | loading | loaded | error:<msg>
        self.profiles: dict[str, AutoEQProfile] = {}
        self._sorted: list[AutoEQCatalogEntry] = []
        self._norm: list[str] = []
        self._listeners = []
        self._network = network
        for p in self._load_imported():
            self.profiles[p.id] = p
        self._rebuild_index()

    # ---------------------------------------------------------------- events

    def connect_changed(self, cb):
        self._listeners.append(cb)

    def _emit(self):
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001 - listener errors must not break the manager
                log.exception("AutoEQ listener failed")

    # --------------------------------------------------------------- catalog

    def load_catalog(self):
        cached = self._load_catalog_cache()
        if cached is not None:
            self.catalog = cached
            self.catalog_state = "loaded"
            self._rebuild_index()
            self._emit()
            if self._cache_stale():
                self.refresh_catalog()
            return
        self.refresh_catalog()

    def refresh_catalog(self):
        if not self._network:
            return
        self.catalog_state = "loading"
        self._emit()

        def work():
            try:
                entries = parser.parse_index_markdown(_http_get(INDEX_URL))
                self._save_catalog_cache(entries)
                self._dispatch(self._catalog_done, entries, None)
            except Exception as e:  # noqa: BLE001
                self._dispatch(self._catalog_done, None, str(e))

        threading.Thread(target=work, name="autoeq-catalog", daemon=True).start()

    def _catalog_done(self, entries, error):
        if entries is not None:
            self.catalog = entries
            self.catalog_state = "loaded"
            log.info("Fetched %d AutoEQ catalog entries", len(entries))
        elif not self.catalog:
            self.catalog_state = f"error:Network error: {error}"
        else:
            self.catalog_state = "loaded"
        self._rebuild_index()
        self._emit()
        return False

    def _load_catalog_cache(self):
        try:
            data = json.loads(self.catalog_cache.read_text())
            return [AutoEQCatalogEntry.from_json(d) for d in data]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save_catalog_cache(self, entries):
        try:
            self.catalog_cache.parent.mkdir(parents=True, exist_ok=True)
            self.catalog_cache.write_text(json.dumps([e.to_json() for e in entries]))
        except OSError as e:
            log.warning("Failed to save catalog cache: %s", e)

    def _cache_stale(self) -> bool:
        try:
            return time.time() - self.catalog_cache.stat().st_mtime > CATALOG_TTL
        except OSError:
            return True

    def catalog_entry(self, pid: str):
        return next((e for e in self.catalog if e.id == pid), None)

    # -------------------------------------------------------------- profiles

    def profile(self, pid: str):
        return self.profiles.get(pid)

    def resolve_profile(self, pid: str, callback):
        """Resolve memory → disk cache → network; callback(profile|None) on the main loop."""
        if pid in self.profiles:
            callback(self.profiles[pid])
            return
        entry = self.catalog_entry(pid)
        if entry is None:
            # Catalog may not be loaded yet; try the disk cache by id.
            cached = self._load_cached_profile(pid, pid, None)
            if cached:
                self.profiles[pid] = cached
            callback(cached)
            return
        self.resolve_entry(entry, callback)

    def resolve_entry(self, entry: AutoEQCatalogEntry, callback):
        if entry.id in self.profiles:
            callback(self.profiles[entry.id])
            return
        cached = self._load_cached_profile(entry.id, entry.name, entry.measured_by)
        if cached:
            self.profiles[entry.id] = cached
            callback(cached)
            return
        if not self._network:
            callback(None)
            return

        def work():
            try:
                last = entry.relative_path.split("/")[-1] or entry.name
                url = (PROFILE_BASE_URL + urllib.parse.quote(entry.relative_path) + "/"
                       + urllib.parse.quote(f"{last} ParametricEQ.txt"))
                text = _http_get(url)
                prof = parser.parse(text, entry.name, SOURCE_FETCHED, id=entry.id)
                if prof is None:
                    raise ValueError(f"Failed to parse profile: {entry.name}")
                prof.measured_by = entry.measured_by
                self._cache_profile_text(entry.id, text)
                self._dispatch(done, prof)
            except Exception as e:  # noqa: BLE001
                log.error("Failed to fetch profile %s: %s", entry.name, e)
                self._dispatch(done, None)

        def done(prof):
            if prof:
                self.profiles[prof.id] = prof
            callback(prof)
            return False

        threading.Thread(target=work, name="autoeq-fetch", daemon=True).start()

    def has_cached_profile(self, pid: str) -> bool:
        return (self.fetched_dir / f"{pid}.txt").exists()

    def _cache_profile_text(self, pid, text):
        try:
            self.fetched_dir.mkdir(parents=True, exist_ok=True)
            (self.fetched_dir / f"{pid}.txt").write_text(text)
        except OSError as e:
            log.warning("Failed to cache profile %s: %s", pid, e)

    def _load_cached_profile(self, pid, name, measured_by):
        try:
            text = (self.fetched_dir / f"{pid}.txt").read_text()
        except OSError:
            return None
        prof = parser.parse(text, name, SOURCE_FETCHED, id=pid)
        if prof:
            prof.measured_by = measured_by
        return prof

    # ---------------------------------------------------------------- import

    def _load_imported(self):
        out = []
        if not self.import_dir.is_dir():
            return out
        for f in sorted(self.import_dir.glob("*.txt")):
            sid = f.stem
            try:
                text = f.read_text()
            except OSError:
                continue
            name_file = self.import_dir / f"{sid}.name"
            try:
                name = name_file.read_text().strip() or sid
            except OSError:
                name = sid
            prof = parser.parse(text, name, SOURCE_IMPORTED, id=sid)
            if prof:
                out.append(prof)
        return out

    def import_profile(self, path: str, name: str | None = None):
        try:
            text = Path(path).read_text()
        except (OSError, UnicodeDecodeError) as e:
            log.error("Failed to import profile: %s", e)
            return None
        if not name:
            name = Path(path).stem.replace(" ParametricEQ", "").strip() or "Imported"
        prof = parser.parse(text, name, SOURCE_IMPORTED)
        if prof is None:
            return None
        try:
            self.import_dir.mkdir(parents=True, exist_ok=True)
            (self.import_dir / f"{prof.id}.txt").write_text(text)
            (self.import_dir / f"{prof.id}.name").write_text(name)
        except OSError as e:
            log.error("Failed to store imported profile: %s", e)
            return None
        self.profiles[prof.id] = prof
        self._rebuild_index()
        self._emit()
        return prof

    def delete_imported_profile(self, pid: str):
        prof = self.profiles.get(pid)
        if not prof or prof.source != SOURCE_IMPORTED:
            return
        del self.profiles[pid]
        for suffix in (".txt", ".name"):
            try:
                os.remove(self.import_dir / f"{pid}{suffix}")
            except OSError:
                pass
        self._rebuild_index()
        self._emit()

    # ---------------------------------------------------------------- search

    def _rebuild_index(self):
        entries = list(self.catalog)
        for p in self.profiles.values():
            if p.source == SOURCE_IMPORTED:
                entries.append(AutoEQCatalogEntry(p.id, p.name, "Imported", ""))
        entries.sort(key=lambda e: e.name.lower())
        self._sorted = entries
        self._norm = [normalize(e.name) for e in entries]

    def search(self, query: str, limit: int = 50) -> SearchResult:
        if not query.strip():
            return SearchResult([], 0)
        lq = query.lower().strip()
        nq = normalize(query)
        scored = []
        for i, e in enumerate(self._sorted):
            s = match_score(lq, nq, e.name.lower(), self._norm[i])
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda t: (-t[0], self._sorted[t[1]].name.lower()))
        return SearchResult([self._sorted[i] for _, i in scored[:limit]], len(scored))

    def entry_for_display(self, pid: str):
        e = self.catalog_entry(pid)
        if e:
            return e
        p = self.profiles.get(pid)
        if p:
            return AutoEQCatalogEntry(p.id, p.name, p.measured_by or ("Imported" if p.source == SOURCE_IMPORTED else ""), "")
        return None
