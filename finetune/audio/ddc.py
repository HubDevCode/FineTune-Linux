"""DDC/CI monitor speaker volume through ddcutil (port of DDCController/DDCService).

VCP feature 0x62 is the audio speaker volume. ddcutil needs access to
/dev/i2c-* (load the i2c-dev module and add your user to the i2c group).
All ddcutil invocations run on worker threads and report back on the main loop.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading

from gi.repository import GLib

log = logging.getLogger("finetune.ddc")

VCP_VOLUME = "62"


class DDCDisplay:
    def __init__(self, number: int, model: str, mfg: str, serial: str):
        self.number = number
        self.model = model
        self.mfg = mfg
        self.serial = serial
        self.supports_volume = False
        self.max_volume = 100
        self.volume = None

    @property
    def label(self) -> str:
        return self.model or f"Display {self.number}"


class DDCService:
    def __init__(self):
        self.available = shutil.which("ddcutil") is not None
        self.displays: list[DDCDisplay] = []
        self.probed = False
        self._listeners = []
        self._lock = threading.Lock()
        self._pending_set: dict[int, int] = {}
        self._setting: set[int] = set()

    def connect_changed(self, cb):
        self._listeners.append(cb)

    def _emit(self):
        for cb in list(self._listeners):
            cb()
        return False

    def probe(self):
        if not self.available:
            self.probed = True
            return

        def work():
            displays = []
            try:
                out = subprocess.run(["ddcutil", "detect", "--terse"], capture_output=True, text=True, timeout=30).stdout
            except (OSError, subprocess.TimeoutExpired) as e:
                log.info("ddcutil detect failed: %s", e)
                out = ""
            cur = None
            for line in out.splitlines():
                m = re.match(r"^Display (\d+)", line)
                if m:
                    cur = DDCDisplay(int(m.group(1)), "", "", "")
                    displays.append(cur)
                    continue
                m = re.match(r"^\s+Monitor:\s+([^:]*):([^:]*):(.*)$", line)
                if m and cur:
                    cur.mfg, cur.model, cur.serial = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            for d in displays:
                v = self._get_volume_blocking(d.number)
                if v is not None:
                    d.supports_volume = True
                    d.volume, d.max_volume = v
            self.displays = [d for d in displays if d.supports_volume]
            self.probed = True
            log.info("DDC: %d display(s) with speaker volume", len(self.displays))
            GLib.idle_add(self._emit)

        threading.Thread(target=work, name="ddc-probe", daemon=True).start()

    @staticmethod
    def _get_volume_blocking(number: int):
        try:
            out = subprocess.run(["ddcutil", "-d", str(number), "getvcp", VCP_VOLUME, "--brief"],
                                 capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"VCP\s+62\s+C\s+(\d+)\s+(\d+)", out)
        if not m:
            return None
        return int(m.group(1)), max(1, int(m.group(2)))

    def match_device(self, device) -> DDCDisplay | None:
        """Match an HDMI/DisplayPort sink to a DDC display by monitor name (ELD), or by elimination."""
        if not self.displays or device.transport not in ("HDMI", "DisplayPort"):
            return None
        hay = " ".join([device.name, device.active_port_description,
                        *[p.get("description", "") for p in device.ports]]).lower()
        for d in self.displays:
            if d.model and d.model.lower() in hay:
                return d
        if len(self.displays) == 1:
            return self.displays[0]
        return None

    def set_volume(self, display: DDCDisplay, percent: int):
        """Coalescing setter: only the latest requested value is written."""
        value = int(round(max(0, min(100, percent)) / 100 * display.max_volume))
        display.volume = value
        with self._lock:
            self._pending_set[display.number] = value
            if display.number in self._setting:
                return
            self._setting.add(display.number)

        def work():
            while True:
                with self._lock:
                    v = self._pending_set.pop(display.number, None)
                    if v is None:
                        self._setting.discard(display.number)
                        return
                try:
                    subprocess.run(["ddcutil", "-d", str(display.number), "setvcp", VCP_VOLUME, str(v), "--noverify"],
                                   capture_output=True, timeout=10)
                except (OSError, subprocess.TimeoutExpired) as e:
                    log.warning("ddcutil setvcp failed: %s", e)

        threading.Thread(target=work, name="ddc-set", daemon=True).start()

    def percent(self, display: DDCDisplay) -> int:
        if display.volume is None:
            return 50
        return int(round(display.volume * 100 / display.max_volume))
