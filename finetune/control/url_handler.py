"""finetune:// URL scheme (port of URLHandler.swift).

    finetune://set-volumes?app=ID&volume=PERCENT[&app=ID&volume=PERCENT…]
    finetune://step-volume?app=ID&direction=up|down
    finetune://set-mute?app=ID&muted=true|false[&app=…]
    finetune://toggle-mute?app=ID[&app=…]
    finetune://set-device?app=ID&device=DEVICE_UID
    finetune://reset[?app=ID…]

Registered through the .desktop file (x-scheme-handler/finetune), so
`xdg-open "finetune://…"` works from scripts, launchers and keybindings.
`ID` matches the persistence identifier, the desktop id, the binary name or
the displayed app name (case-insensitive).
"""
from __future__ import annotations

import logging
import urllib.parse

from finetune.models.volume import gain_to_slider, slider_to_gain

log = logging.getLogger("finetune.url")

STEP_AMOUNT = 0.05


def parse_bool(v: str | None):
    if v is None:
        return None
    v = v.lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return None


def parse_url(url: str):
    """Returns (action, [(key, value), …]) or (None, []) for foreign schemes."""
    u = urllib.parse.urlsplit(url)
    if u.scheme.lower() != "finetune":
        return None, []
    action = (u.netloc or u.path.lstrip("/")).lower()
    items = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    return action, items


def paired(items, value_key: str):
    """Collect app/value pairs in order: app=… must precede its value."""
    pairs, current = [], None
    for k, v in items:
        k = k.lower()
        if k == "app":
            current = v
        elif k == value_key:
            if current is None:
                log.warning("%s without preceding app", value_key)
                continue
            pairs.append((current, v))
            current = None
    return pairs


class URLHandler:
    def __init__(self, engine):
        self.engine = engine

    def _resolve(self, ident: str) -> str:
        app = self.engine.find_app(ident)
        return app.identifier if app else ident

    def handle(self, url: str) -> bool:
        log.info("Received URL: %s", url)
        action, items = parse_url(url)
        if action is None:
            log.warning("Unknown URL scheme: %s", url)
            return False
        handler = {
            "set-volumes": self._set_volumes, "set-volume": self._set_volumes,
            "step-volume": self._step_volume, "set-mute": self._set_mute,
            "toggle-mute": self._toggle_mute, "set-device": self._set_device, "reset": self._reset,
        }.get(action)
        if not handler:
            log.warning("Unknown URL action: %s", action)
            return False
        return handler(items)

    def _set_volumes(self, items) -> bool:
        ok = False
        for ident, raw in paired(items, "volume"):
            try:
                pct = int(raw)
            except ValueError:
                log.warning("set-volumes: invalid volume %r for %s", raw, ident)
                continue
            if not 0 <= pct <= 400:
                log.warning("set-volumes: volume %d out of range for %s", pct, ident)
                continue
            target = self._resolve(ident)
            if pct > 100:
                # Above 100% maps onto the boost levels (2x/3x/4x), like the slider.
                boost = min(4.0, max(1.0, float(-(-pct // 100))))
                self.engine.set_boost(target, boost)
                self.engine.set_volume(target, pct / 100 / boost)
            else:
                if self.engine.get_boost(target) > 1.0:
                    self.engine.set_boost(target, 1.0)
                self.engine.set_volume(target, pct / 100)
            ok = True
        return ok

    def _step_volume(self, items) -> bool:
        d = dict((k.lower(), v) for k, v in items)
        ident, direction = d.get("app"), (d.get("direction") or "").lower()
        if not ident:
            log.error("step-volume: missing app parameter")
            return False
        app = self.engine.find_app(ident)
        if app is None:
            log.warning("step-volume: app not found %r", ident)
            return False
        if direction in ("up", "+"):
            sign = 1
        elif direction in ("down", "-"):
            sign = -1
        else:
            log.error("step-volume: invalid direction %r", direction)
            return False
        slider = gain_to_slider(self.engine.get_volume(app.identifier))
        slider = max(0.0, min(1.0, slider + sign * STEP_AMOUNT))
        self.engine.set_volume(app.identifier, slider_to_gain(slider))
        return True

    def _set_mute(self, items) -> bool:
        ok = False
        for ident, raw in paired(items, "muted"):
            m = parse_bool(raw)
            if m is None:
                log.warning("set-mute: invalid muted value %r for %s", raw, ident)
                continue
            self.engine.set_mute(self._resolve(ident), m)
            ok = True
        return ok

    def _toggle_mute(self, items) -> bool:
        idents = [v for k, v in items if k.lower() == "app" and v]
        for ident in idents:
            self.engine.toggle_mute(self._resolve(ident))
        return bool(idents)

    def _set_device(self, items) -> bool:
        d = dict((k.lower(), v) for k, v in items)
        ident, uid = d.get("app"), d.get("device")
        if not ident or not uid:
            log.error("set-device: missing app or device parameter")
            return False
        app = self.engine.find_app(ident)
        target = app.identifier if app else ident
        if uid.lower() in ("default", "system"):
            self.engine.set_device(target, None)
        else:
            self.engine.set_device(target, uid)
        return True

    def _reset(self, items) -> bool:
        idents = [v for k, v in items if k.lower() == "app" and v]
        if not idents:
            self.engine.reset_all_apps()
            return True
        for ident in idents:
            t = self._resolve(ident)
            self.engine.set_volume(t, 1.0)
            self.engine.set_mute(t, False)
        return True
