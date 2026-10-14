"""Which app should "App Volume Up/Down/Mute" act on? (port of TargetAppResolver)

The target is whichever app is currently making sound. When several apps are
audible, the focused one wins, then the last one targeted. When nothing is
audible we fall back to the focused window. The focused window is read from
the compositor (Hyprland, Sway, KDE via kdotool, X11 via xdotool).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

OWN_IDS = {"finetune", "io.github.finetune.finetune"}


def _run(argv, timeout=0.5):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def frontmost_window() -> dict:
    """Returns {"class": str, "pid": int|None} of the focused window, best effort."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        try:
            d = json.loads(_run(["hyprctl", "activewindow", "-j"]) or "{}")
            return {"class": d.get("class") or d.get("initialClass") or "", "pid": d.get("pid")}
        except ValueError:
            pass
    if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
        try:
            tree = json.loads(_run(["swaymsg", "-t", "get_tree"]) or "{}")
            stack = [tree]
            while stack:
                n = stack.pop()
                if n.get("focused"):
                    return {"class": n.get("app_id") or (n.get("window_properties") or {}).get("class") or "",
                            "pid": n.get("pid")}
                stack.extend(n.get("nodes", []) + n.get("floating_nodes", []))
        except ValueError:
            pass
    if shutil.which("kdotool") and os.environ.get("KDE_SESSION_VERSION"):
        wid = _run(["kdotool", "getactivewindow"]).strip()
        if wid:
            return {"class": _run(["kdotool", "getwindowclassname", wid]).strip(), "pid": None}
    if os.environ.get("DISPLAY") and shutil.which("xdotool"):
        pid = _run(["xdotool", "getactivewindow", "getwindowpid"]).strip()
        cls = _run(["xdotool", "getactivewindow", "getwindowclassname"]).strip()
        return {"class": cls, "pid": int(pid) if pid.isdigit() else None}
    return {"class": "", "pid": None}


class TargetAppResolver:
    def __init__(self, frontmost=frontmost_window):
        self._frontmost = frontmost
        self.last_targeted: str | None = None
        self.last_frontmost: str | None = None

    def _frontmost_app(self, engine):
        w = self._frontmost()
        cls = (w.get("class") or "").lower()
        pid = str(w.get("pid")) if w.get("pid") else None
        if cls in OWN_IDS:
            return self.last_frontmost
        for app in engine.apps.values():
            names = {x.lower() for x in (app.identifier, app.bundle_id, app.binary, app.name) if x}
            names |= {n.split(".")[-1] for n in names}
            if cls and (cls in names or cls.split(".")[-1] in names) or (pid and pid in app.pids):
                self.last_frontmost = app.identifier
                return app.identifier
        return None

    def resolve(self, engine) -> str | None:
        """Returns the persistence identifier of the target app, or None."""
        audible = [i for i, a in engine.apps.items()
                   if not engine.settings.is_ignored(i) and engine.is_audible(i)]
        if not audible:
            audible_streams = [i for i, a in engine.apps.items()
                               if not engine.settings.is_ignored(i) and a.is_playing]
            audible = audible_streams
        front = self._frontmost_app(engine)
        if not audible:
            return front
        if front and front in audible:
            self.last_targeted = front
            return front
        if self.last_targeted in audible:
            return self.last_targeted
        self.last_targeted = audible[0]
        return audible[0]
