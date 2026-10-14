"""On-screen volume HUD (port of HUDWindowController, TahoeStyleHUD, ClassicStyleHUD).

Tahoe: a pill in the top-right corner with icon, device/app name, bar and %.
Classic: a translucent square near the bottom centre with a big glyph and 16 segments.
Scrolling over the HUD adjusts the volume it shows and keeps it on screen.
"""
from __future__ import annotations

import math

from gi.repository import Gtk, GLib

from finetune.ui import layer
from finetune.ui.widgets import clear_box, gicon_image, speaker_icon

HIDE_AFTER_MS = 1500
SEGMENTS = 16


class HUDWindow(Gtk.Window):
    def __init__(self, app, engine):
        super().__init__(application=app, title="FineTune HUD")
        self.engine = engine
        self.add_css_class("ft-hud")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_can_focus(False)
        self.style = engine.settings.app_settings.hud_style
        self.is_layer = layer.setup_hud(self, self.style)
        self.box = Gtk.Box()
        self.set_child(self.box)
        self._hide_id = 0
        self._scroll_target = None
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)
        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", lambda *_: self._cancel_hide())
        motion.connect("leave", lambda *_: self._schedule_hide())
        self.add_controller(motion)

    # ------------------------------------------------------------- content

    def _render(self, icon, title: str, fraction: float, muted: bool, subtitle: str | None = None):
        style = self.engine.settings.app_settings.hud_style
        if style != self.style:
            self.style = style
            layer.position_hud(self, style)
        clear_box(self.box)
        pct = int(round(max(0.0, min(1.0, fraction)) * 100))
        glyph = icon or speaker_icon(fraction, muted)
        if style == "classic":
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            card.add_css_class("ft-hud-classic")
            img = gicon_image(glyph if icon else speaker_icon(fraction, muted), 96)
            img.set_vexpand(True)
            card.append(img)
            lbl = Gtk.Label(label=title, ellipsize=3, max_width_chars=22)
            lbl.add_css_class("ft-hud-title")
            card.append(lbl)
            segs = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER)
            lit = 0 if muted else int(math.ceil(fraction * SEGMENTS - 1e-6))
            for i in range(SEGMENTS):
                s = Gtk.Box()
                s.add_css_class("ft-hud-segment")
                if i < lit:
                    s.add_css_class("ft-on")
                segs.append(s)
            card.append(segs)
            self.box.append(card)
        else:
            pill = Gtk.Box(spacing=12)
            pill.add_css_class("ft-hud-tahoe")
            pill.append(gicon_image(glyph, 22))
            mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            lbl = Gtk.Label(label=title, xalign=0, ellipsize=3, max_width_chars=26)
            lbl.add_css_class("ft-hud-title")
            mid.append(lbl)
            if subtitle:
                sl = Gtk.Label(label=subtitle, xalign=0)
                sl.add_css_class("ft-subtitle")
                mid.append(sl)
            bar = Gtk.ProgressBar(fraction=0 if muted else fraction)
            bar.add_css_class("ft-hud-bar")
            mid.append(bar)
            pill.append(mid)
            pl = Gtk.Label(label="Muted" if muted else f"{pct}%")
            pl.add_css_class("ft-percent")
            pill.append(pl)
            if muted:
                pill.append(gicon_image("audio-volume-muted-symbolic", 16))
            self.box.append(pill)
        self.present()
        self._schedule_hide()

    def show_device(self, dev, fraction: float, muted: bool):
        if not self.engine.settings.app_settings.hud_enabled:
            return
        self._scroll_target = ("device", dev.uid)
        icon = self.engine.settings.get_device_icon_override(dev.uid) or dev.icon_name
        self._render(speaker_icon(fraction, muted) if self.style == "classic" else icon, dev.name, fraction, muted)

    def show_app(self, ident: str, name: str, icon, slider: float, muted: bool):
        if not self.engine.settings.app_settings.hud_enabled:
            return
        self._scroll_target = ("app", ident)
        self._render(icon, name, slider, muted)

    def show_not_controlled(self, name: str | None):
        if not self.engine.settings.app_settings.hud_enabled:
            return
        self._scroll_target = None
        self._render("dialog-information-symbolic", name or "No app playing audio", 0.0, False,
                     subtitle="Not controlled by FineTune")

    # ------------------------------------------------------------- hiding

    def _cancel_hide(self):
        if self._hide_id:
            GLib.source_remove(self._hide_id)
            self._hide_id = 0

    def _schedule_hide(self):
        self._cancel_hide()

        def hide():
            self._hide_id = 0
            self.set_visible(False)
            return False

        self._hide_id = GLib.timeout_add(HIDE_AFTER_MS, hide)

    def _on_scroll(self, _c, _dx, dy):
        if not self._scroll_target or dy == 0:
            return False
        kind, key = self._scroll_target
        step = self.engine.settings.app_settings.volume_hotkey_step.slider_delta
        direction = -1 if dy > 0 else 1
        if kind == "device":
            dev = self.engine.output_by_uid(key)
            if dev:
                v = self.engine.step_device_volume(dev, direction, step)
                self.show_device(dev, v, self.engine.device_muted(dev))
        else:
            from finetune.models.volume import gain_to_slider
            g = self.engine.step_app_volume(key, direction, step)
            app = self.engine.apps.get(key)
            self.show_app(key, app.name if app else key, app.icon if app else None, gain_to_slider(g),
                          self.engine.get_mute(key))
        return True
