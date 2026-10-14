"""PipeWire session access through the PulseAudio protocol (pipewire-pulse).

This replaces the CoreAudio HAL property listeners of the macOS app
(AudioDeviceMonitor, AudioProcessMonitor, DeviceVolumeMonitor): a long-running
`pactl subscribe` delivers change events and `pactl -f json list …` provides
snapshots. All calls are asynchronous (Gio.Subprocess) so the UI never blocks.
"""
from __future__ import annotations

import json
import logging
import os
import re

from gi.repository import Gio, GLib

log = logging.getLogger("finetune.pulse")

_EVENT_RE = re.compile(r"Event '(\w+)' on ([\w-]+) #(\d+)")
_KIND_MAP = {
    "sink": "sinks", "source": "sources", "sink-input": "sink-inputs", "source-output": None,
    "server": "info", "card": "cards", "client": None, "module": None, "sample-cache": None,
}


def _env():
    env = dict(os.environ)
    env["LC_MESSAGES"] = "C"
    return env


class PulseBackend:
    """Snapshots of sinks/sources/sink-inputs/cards/server info + change notifications."""

    def __init__(self):
        self.sinks: list[dict] = []
        self.sources: list[dict] = []
        self.sink_inputs: list[dict] = []
        self.cards: list[dict] = []
        self.info: dict = {}
        self._listeners = []
        self._pending: set[str] = set()
        self._inflight: set[str] = set()
        self._dirty_again: set[str] = set()
        self._debounce_id = 0
        self._sub_proc = None
        self._sub_stream = None
        self._launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        for k, v in _env().items():
            self._launcher.setenv(k, v, True)
        self._quiet = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
        for k, v in _env().items():
            self._quiet.setenv(k, v, True)
        self.available = GLib.find_program_in_path("pactl") is not None
        self._ready_kinds: set[str] = set()

    # ------------------------------------------------------------ lifecycle

    def connect_changed(self, cb):
        """cb(kinds: set[str]) — called after snapshots were refreshed."""
        self._listeners.append(cb)

    def start(self):
        if not self.available:
            log.error("pactl not found — install pipewire-pulse / libpulse")
            return
        self._start_subscribe()
        self.refresh({"info", "sinks", "sources", "sink-inputs", "cards"})

    def stop(self):
        if self._sub_proc:
            try:
                self._sub_proc.force_exit()
            except GLib.Error:
                pass
            self._sub_proc = None

    def is_ready(self) -> bool:
        return {"info", "sinks", "sources", "sink-inputs"} <= self._ready_kinds

    def _start_subscribe(self):
        try:
            self._sub_proc = self._launcher.spawnv(["pactl", "subscribe"])
        except GLib.Error as e:
            log.error("Cannot start pactl subscribe: %s", e.message)
            GLib.timeout_add_seconds(2, self._restart_subscribe)
            return
        self._sub_stream = Gio.DataInputStream.new(self._sub_proc.get_stdout_pipe())
        self._sub_stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_sub_line)

    def _restart_subscribe(self):
        self._start_subscribe()
        self.refresh({"info", "sinks", "sources", "sink-inputs", "cards"})
        return False

    def _on_sub_line(self, stream, res):
        try:
            line, _ = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:
            log.warning("pactl subscribe ended; restarting")
            self._sub_proc = None
            GLib.timeout_add_seconds(1, self._restart_subscribe)
            return
        m = _EVENT_RE.search(line)
        if m:
            kind = _KIND_MAP.get(m.group(2))
            if kind:
                self.refresh({kind} | ({"info"} if kind in ("sinks", "sources") else set()))
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_sub_line)

    # ------------------------------------------------------------ snapshots

    def refresh(self, kinds):
        self._pending |= set(kinds)
        if not self._debounce_id:
            self._debounce_id = GLib.timeout_add(40, self._flush_refresh)

    def _flush_refresh(self):
        self._debounce_id = 0
        kinds, self._pending = self._pending, set()
        for kind in kinds:
            if kind in self._inflight:
                self._dirty_again.add(kind)
                continue
            self._inflight.add(kind)
            argv = ["pactl", "-f", "json", "info"] if kind == "info" else ["pactl", "-f", "json", "list", kind]
            try:
                proc = self._launcher.spawnv(argv)
            except GLib.Error as e:
                log.error("pactl failed: %s", e.message)
                self._inflight.discard(kind)
                continue
            proc.communicate_utf8_async(None, None, self._on_list_done, kind)
        return False

    def _on_list_done(self, proc, res, kind):
        self._inflight.discard(kind)
        try:
            _ok, out, _err = proc.communicate_utf8_finish(res)
            data = json.loads(out or "null")
        except (GLib.Error, ValueError) as e:
            log.debug("pactl %s parse failed: %s", kind, e)
            data = None
        if data is not None:
            if kind == "info":
                self.info = data if isinstance(data, dict) else {}
            elif kind == "sinks":
                self.sinks = data
            elif kind == "sources":
                self.sources = data
            elif kind == "sink-inputs":
                self.sink_inputs = data
            elif kind == "cards":
                self.cards = data
            self._ready_kinds.add(kind)
            for cb in list(self._listeners):
                try:
                    cb({kind})
                except Exception:  # noqa: BLE001
                    log.exception("pulse listener failed")
        if kind in self._dirty_again:
            self._dirty_again.discard(kind)
            self.refresh({kind})

    # -------------------------------------------------------------- helpers

    @property
    def default_sink(self) -> str | None:
        return self.info.get("default_sink_name")

    @property
    def default_source(self) -> str | None:
        return self.info.get("default_source_name")

    def card_for(self, card_index):
        for c in self.cards:
            if str(c.get("index")) == str(card_index):
                return c
        return None

    # -------------------------------------------------------------- actions

    def run(self, *args, callback=None):
        argv = ["pactl", *[str(a) for a in args]]
        try:
            proc = self._quiet.spawnv(argv)
        except GLib.Error as e:
            log.error("pactl %s failed: %s", args, e.message)
            return

        def done(p, res):
            try:
                p.wait_finish(res)
                ok = p.get_successful()
            except GLib.Error:
                ok = False
            if not ok:
                log.debug("pactl %s returned failure", " ".join(argv[1:]))
            if callback:
                callback(ok)

        proc.wait_async(None, done)

    def set_sink_volume(self, name: str, fraction: float):
        self.run("set-sink-volume", name, f"{max(0.0, fraction) * 100:.2f}%")

    def set_sink_mute(self, name: str, muted: bool):
        self.run("set-sink-mute", name, "1" if muted else "0")

    def set_source_volume(self, name: str, fraction: float):
        self.run("set-source-volume", name, f"{max(0.0, fraction) * 100:.2f}%")

    def set_source_mute(self, name: str, muted: bool):
        self.run("set-source-mute", name, "1" if muted else "0")

    def set_default_sink(self, name: str):
        self.run("set-default-sink", name)

    def set_default_source(self, name: str):
        self.run("set-default-source", name)

    def move_sink_input(self, index, sink: str, callback=None):
        self.run("move-sink-input", index, sink, callback=callback)

    def set_sink_input_volume(self, index, fraction: float):
        self.run("set-sink-input-volume", index, f"{max(0.0, fraction) * 100:.2f}%")

    def set_sink_port(self, sink: str, port: str):
        self.run("set-sink-port", sink, port)

    def set_source_port(self, source: str, port: str):
        self.run("set-source-port", source, port)

    def set_card_profile(self, card: str, profile: str):
        self.run("set-card-profile", card, profile)


def volume_fraction(entry: dict) -> float:
    """Average channel volume of a pactl JSON object as 0..1+ fraction."""
    vol = entry.get("volume") or {}
    vals = [v.get("value", 0) for v in vol.values() if isinstance(v, dict)]
    if not vals:
        return 0.0
    return max(vals) / 65536.0
