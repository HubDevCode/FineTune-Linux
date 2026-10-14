"""Supervisor and line-protocol client for the C audio engine.

Equivalent of the macOS CrashGuard + OrphanedTapCleanup: if the engine dies
PipeWire removes every node it owned, so audio falls back to the normal route
immediately; the supervisor restarts the engine with exponential backoff and
asks the AudioEngine to rebuild its chains.
"""
from __future__ import annotations

import logging

from gi.repository import Gio, GLib

from finetune import paths

log = logging.getLogger("finetune.engine")


class EngineClient:
    def __init__(self):
        self._proc = None
        self._stdin = None
        self._stdout = None
        self.ready = False
        self.levels: dict[str, float] = {}
        self.rates: dict[str, int] = {}
        self._on_ready = []
        self._on_levels = []
        self._on_state = []
        self._backoff = 1
        self._stopping = False
        self._restart_id = 0
        self.failures = 0
        self.last_error: str | None = None

    def connect_ready(self, cb):
        self._on_ready.append(cb)

    def connect_levels(self, cb):
        self._on_levels.append(cb)

    def connect_state(self, cb):
        self._on_state.append(cb)

    # ------------------------------------------------------------- process

    def start(self):
        self._stopping = False
        binary = paths.engine_binary()
        try:
            self._proc = Gio.Subprocess.new(
                [binary], Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE)
        except GLib.Error as e:
            self.last_error = f"Cannot start audio engine ({binary}): {e.message}"
            log.error(self.last_error)
            self._schedule_restart()
            return
        self._stdin = self._proc.get_stdin_pipe()
        self._stdout = Gio.DataInputStream.new(self._proc.get_stdout_pipe())
        self._stdout.read_line_async(GLib.PRIORITY_HIGH, None, self._on_line)
        self._proc.wait_async(None, self._on_exit)

    def stop(self):
        self._stopping = True
        if self._restart_id:
            GLib.source_remove(self._restart_id)
            self._restart_id = 0
        if self._proc:
            self.send("quit")
            proc = self._proc
            self._proc = None
            # give it a moment to tear down cleanly, then make sure it's gone
            GLib.timeout_add(800, lambda: (proc.force_exit(), False)[1])
        self.ready = False

    def _on_exit(self, proc, res):
        try:
            proc.wait_finish(res)
        except GLib.Error:
            pass
        if proc is not self._proc and self._proc is not None:
            return
        self.ready = False
        self._proc = None
        if self._stopping:
            return
        self.failures += 1
        log.warning("Audio engine exited (status %s); restarting in %ss",
                    proc.get_exit_status() if proc.get_if_exited() else "signal", self._backoff)
        self._schedule_restart()

    def _schedule_restart(self):
        if self._stopping or self._restart_id:
            return

        def restart():
            self._restart_id = 0
            self.start()
            return False

        self._restart_id = GLib.timeout_add_seconds(self._backoff, restart)
        self._backoff = min(self._backoff * 2, 30)

    # ------------------------------------------------------------ protocol

    def send(self, line: str):
        if not self._stdin:
            return
        try:
            self._stdin.write_all((line.replace("\n", " ") + "\n").encode("utf-8"), None)
        except GLib.Error as e:
            log.debug("engine write failed: %s", e.message)

    def _on_line(self, stream, res):
        try:
            line, _ = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:
            return
        self._handle(line)
        stream.read_line_async(GLib.PRIORITY_HIGH, None, self._on_line)

    def _handle(self, line: str):
        parts = line.split(" ")
        kind = parts[0]
        if kind == "lv":
            for p in parts[1:]:
                k, _, v = p.partition("=")
                try:
                    self.levels[k] = float(v)
                except ValueError:
                    pass
            for cb in self._on_levels:
                cb(self.levels)
        elif kind == "ready":
            self.ready = True
            self._backoff = 1
            self.last_error = None
            log.info("Audio engine ready")
            for cb in self._on_ready:
                cb()
        elif kind == "rate" and len(parts) >= 3:
            try:
                self.rates[parts[1]] = int(parts[2])
            except ValueError:
                pass
        elif kind == "state":
            for cb in self._on_state:
                cb(parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "",
                   " ".join(parts[3:]))
        elif kind == "err":
            log.warning("engine: %s", line[4:])
        else:
            log.debug("engine: %s", line)
