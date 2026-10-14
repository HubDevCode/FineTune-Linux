"""gtk4-layer-shell integration (optional).

The library must be loaded before libwayland-client, so the launcher
(finetune/app.py:ensure_layer_shell_preload) re-executes the process with
LD_PRELOAD when it's available. Without it the popup and HUD fall back to
regular undecorated windows.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("finetune.layer")

_LS = None
_checked = False


def _ls():
    global _LS, _checked
    if _checked:
        return _LS
    _checked = True
    if os.environ.get("FINETUNE_NO_LAYER_SHELL"):
        return None
    try:
        import gi
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LS
        if LS.is_supported():
            _LS = LS
    except (ImportError, ValueError) as e:
        log.info("gtk4-layer-shell unavailable: %s", e)
    return _LS


def available() -> bool:
    return _ls() is not None


def setup_popup(win) -> bool:
    LS = _ls()
    if LS is None:
        return False
    LS.init_for_window(win)
    LS.set_namespace(win, "finetune-popup")
    LS.set_layer(win, LS.Layer.OVERLAY)
    LS.set_anchor(win, LS.Edge.TOP, True)
    LS.set_anchor(win, LS.Edge.RIGHT, True)
    LS.set_margin(win, LS.Edge.TOP, 4)
    LS.set_margin(win, LS.Edge.RIGHT, 8)
    LS.set_keyboard_mode(win, LS.KeyboardMode.ON_DEMAND)
    LS.set_exclusive_zone(win, 0)
    return True


def setup_hud(win, style: str) -> bool:
    LS = _ls()
    if LS is None:
        return False
    LS.init_for_window(win)
    LS.set_namespace(win, "finetune-hud")
    LS.set_layer(win, LS.Layer.OVERLAY)
    LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
    LS.set_exclusive_zone(win, -1)
    position_hud(win, style)
    return True


def position_hud(win, style: str):
    LS = _ls()
    if LS is None:
        return
    for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
        LS.set_anchor(win, e, False)
    if style == "classic":
        LS.set_anchor(win, LS.Edge.BOTTOM, True)
        LS.set_margin(win, LS.Edge.BOTTOM, 120)
    else:
        LS.set_anchor(win, LS.Edge.TOP, True)
        LS.set_anchor(win, LS.Edge.RIGHT, True)
        LS.set_margin(win, LS.Edge.TOP, 8)
        LS.set_margin(win, LS.Edge.RIGHT, 8)

