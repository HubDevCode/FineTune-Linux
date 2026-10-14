"""Reusable widgets: volume slider, VU meter, boost chevrons, editable percentage, icon buttons."""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

VU_BARS = 8
VU_PEAK_HOLD_S = 0.6


def icon_button(icon_name: str, tooltip: str | None = None, css=("ft-icon-button",), toggle=False):
    btn = Gtk.ToggleButton() if toggle else Gtk.Button()
    btn.set_icon_name(icon_name)
    for c in css:
        btn.add_css_class(c)
    btn.add_css_class("flat")
    btn.set_valign(Gtk.Align.CENTER)
    if tooltip:
        btn.set_tooltip_text(tooltip)
    return btn


def speaker_icon(fraction: float, muted: bool) -> str:
    if muted or fraction <= 0.004:
        return "audio-volume-muted-symbolic"
    if fraction < 0.34:
        return "audio-volume-low-symbolic"
    if fraction < 0.67:
        return "audio-volume-medium-symbolic"
    return "audio-volume-high-symbolic"


def mic_icon(muted: bool) -> str:
    return "microphone-disabled-symbolic" if muted else "audio-input-microphone-symbolic"


def gicon_image(icon, size: int = 24) -> Gtk.Image:
    if isinstance(icon, str):
        img = Gtk.Image.new_from_icon_name(icon)
    elif icon is not None:
        img = Gtk.Image.new_from_gicon(icon)
    else:
        img = Gtk.Image.new_from_icon_name("application-x-executable")
    img.set_pixel_size(size)
    return img


class VolumeSlider(Gtk.Scale):
    """Horizontal 0..1 slider; programmatic updates don't fire the user callback.

    Scroll wheel moves by the configured volume step (ScrollWheelStep port)."""

    def __init__(self, on_change, step_provider=lambda: 1 / 16, width=150):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_range(0.0, 1.0)
        self.set_increments(0.01, 0.1)
        self.set_draw_value(False)
        self.set_hexpand(True)
        self.set_size_request(width, -1)
        self.add_css_class("ft-slider")
        self._on_change = on_change
        self._step_provider = step_provider
        self._updating = False
        self._dragging = False
        self.connect("value-changed", self._changed)
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)
        click = Gtk.GestureClick.new()
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", lambda *_: setattr(self, "_dragging", True))
        click.connect("released", lambda *_: setattr(self, "_dragging", False))
        click.connect("stopped", lambda *_: setattr(self, "_dragging", False))
        self.add_controller(click)

    def _on_scroll(self, _ctrl, _dx, dy):
        if dy == 0:
            return False
        step = self._step_provider()
        self.set_value(max(0.0, min(1.0, self.get_value() - math.copysign(step, dy))))
        return True

    def _changed(self, _s):
        if not self._updating:
            self._on_change(self.get_value())

    def set_value_silently(self, v: float):
        if self._dragging:
            return
        if abs(self.get_value() - v) < 1e-4:
            return
        self._updating = True
        self.set_value(v)
        self._updating = False

    def set_muted_style(self, muted: bool):
        if muted:
            self.add_css_class("ft-muted")
        else:
            self.remove_css_class("ft-muted")


class VUMeter(Gtk.DrawingArea):
    """8-bar vertical level meter with peak hold and decay (port of VUMeter.swift)."""

    def __init__(self, height=30):
        super().__init__()
        self.set_size_request(10, height)
        self.set_valign(Gtk.Align.CENTER)
        self.level = 0.0
        self.peak = 0.0
        self.muted = False
        self._peak_time = 0.0
        self.set_draw_func(self._draw)

    def set_level(self, level: float, muted: bool = False):
        now = GLib.get_monotonic_time() / 1e6
        level = max(0.0, min(1.0, level))
        # map amplitude to a dB-ish display scale (-48 dB .. 0 dB)
        disp = 0.0 if level <= 0 else max(0.0, 1.0 + 20 * math.log10(level) / 48.0)
        if disp >= self.peak:
            self.peak = disp
            self._peak_time = now
        elif now - self._peak_time > VU_PEAK_HOLD_S:
            self.peak = max(disp, self.peak - 0.04)
        changed = abs(disp - self.level) > 0.01 or muted != self.muted
        self.level = disp
        self.muted = muted
        if changed or self.peak > disp:
            self.queue_draw()

    def _draw(self, _area, cr, w, h):
        gap = 1.5
        bar_h = (h - gap * (VU_BARS - 1)) / VU_BARS
        fg = self.get_color()
        lit = int(round(self.level * VU_BARS))
        peak_bar = int(round(self.peak * VU_BARS)) - 1
        for i in range(VU_BARS):
            y = h - (i + 1) * bar_h - i * gap
            on = i < lit or i == peak_bar
            if self.muted or not on:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.18)
            elif i >= VU_BARS - 1:
                cr.set_source_rgba(1.0, 0.23, 0.19, 1)
            elif i >= VU_BARS - 3:
                cr.set_source_rgba(1.0, 0.8, 0.0, 1)
            else:
                cr.set_source_rgba(0.2, 0.78, 0.35, 1)
            cr.rectangle(0, y, w, bar_h)
            cr.fill()


class BoostButton(Gtk.Button):
    """Three stacked chevrons; lit count = boost level − 1. Click cycles 1x→2x→3x→4x."""

    def __init__(self, on_click):
        super().__init__()
        self.add_css_class("ft-icon-button")
        self.add_css_class("flat")
        self.set_valign(Gtk.Align.CENTER)
        self.boost = 1.0
        self._area = Gtk.DrawingArea()
        self._area.set_size_request(18, 20)
        self._area.set_draw_func(self._draw)
        self.set_child(self._area)
        self.connect("clicked", lambda *_: on_click())

    def set_boost(self, boost: float):
        if boost != self.boost:
            self.boost = boost
            self._area.queue_draw()
        self.set_tooltip_text(f"Volume boost: {int(boost)}x")

    def _draw(self, area, cr, w, h):
        fg = area.get_color()
        lit = int(self.boost) - 1
        cr.set_line_width(2.0)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        for i in range(3):
            y = h - 4 - i * 5.5
            if i < lit:
                cr.set_source_rgba(0.18, 0.48, 0.96, 1)
            else:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)
            cr.move_to(w / 2 - 6, y)
            cr.line_to(w / 2, y - 4.5)
            cr.line_to(w / 2 + 6, y)
            cr.stroke()


class EditablePercentage(Gtk.Stack):
    """Percentage label; click to type an exact value (Enter commits, Esc cancels)."""

    def __init__(self, on_commit, max_value=100):
        super().__init__()
        self.set_valign(Gtk.Align.CENTER)
        self._on_commit = on_commit
        self._max = max_value
        self.label = Gtk.Label(xalign=1.0)
        self.label.add_css_class("ft-percent")
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("ft-icon-button")
        btn.set_child(self.label)
        btn.set_tooltip_text("Click to type a value")
        btn.connect("clicked", lambda *_: self._start())
        self.entry = Gtk.Entry()
        self.entry.set_width_chars(4)
        self.entry.set_max_width_chars(4)
        self.entry.add_css_class("ft-percent")
        self.entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.entry.connect("activate", lambda *_: self._commit())
        key = Gtk.EventControllerKey.new()
        key.connect("key-pressed", self._on_key)
        self.entry.add_controller(key)
        focus = Gtk.EventControllerFocus.new()
        focus.connect("leave", lambda *_: self._commit())
        self.entry.add_controller(focus)
        self.add_named(btn, "label")
        self.add_named(self.entry, "entry")
        self.set_visible_child_name("label")
        self._value = 0

    def set_percent(self, value: int):
        self._value = value
        self.label.set_text(f"{value}%")

    @property
    def editing(self) -> bool:
        return self.get_visible_child_name() == "entry"

    def _start(self):
        self.entry.set_text(str(self._value))
        self.set_visible_child_name("entry")
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def _on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.set_visible_child_name("label")
            return True
        return False

    def _commit(self):
        if not self.editing:
            return
        self.set_visible_child_name("label")
        txt = self.entry.get_text().strip().rstrip("%")
        try:
            v = int(float(txt))
        except ValueError:
            return
        if 0 <= v <= self._max:
            self._on_commit(v)


class DeviceIconButton(Gtk.Button):
    """Round device glyph; filled with the accent when it's the default device."""

    def __init__(self, on_click=None, size=34):
        super().__init__()
        self.add_css_class("ft-device-icon")
        self.add_css_class("flat")
        self.set_valign(Gtk.Align.CENTER)
        self.image = Gtk.Image()
        self.image.set_pixel_size(18)
        self.set_child(self.image)
        self.set_size_request(size, size)
        if on_click:
            self.connect("clicked", lambda *_: on_click())

    def update(self, icon_name: str, is_default: bool, tooltip: str | None = None):
        self.image.set_from_icon_name(icon_name)
        if is_default:
            self.add_css_class("ft-default")
        else:
            self.remove_css_class("ft-default")
        if tooltip:
            self.set_tooltip_text(tooltip)


def clear_box(box: Gtk.Widget):
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def copy_to_clipboard(widget: Gtk.Widget, text: str):
    widget.get_clipboard().set(text)


def open_uri(uri: str):
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error:
        pass
