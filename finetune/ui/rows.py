"""Popup rows (port of Views/Rows/*): devices, inputs, apps, edit-mode rows and inspector."""
from __future__ import annotations

from gi.repository import Gdk, GObject, Gtk

from finetune.audio.devices import COMMON_RATES
from finetune.models.volume import (
    VolumeTier, gain_to_slider, next_boost, slider_to_gain,
)
from finetune.settings import DEVICE_SELECTION_MULTI
from finetune.ui.eq_panel import EQPanel
from finetune.ui.pickers import AutoEQPopover, DevicePickerPopover, IconPickerPopover
from finetune.ui.widgets import (
    BoostButton, DeviceIconButton, EditablePercentage, VolumeSlider, VUMeter,
    copy_to_clipboard, gicon_image, icon_button, mic_icon, speaker_icon,
)


class NavRow(Gtk.Box):
    """Base row: hover styling + keyboard navigation hooks used by the popup."""

    def __init__(self, ctx, orientation=Gtk.Orientation.HORIZONTAL, spacing=8):
        super().__init__(orientation=orientation, spacing=spacing)
        self.ctx = ctx
        self.add_css_class("ft-row")

    @property
    def engine(self):
        return self.ctx.engine

    def step(self) -> float:
        return self.engine.settings.app_settings.volume_hotkey_step.slider_delta

    def set_focused(self, focused: bool):
        if focused:
            self.add_css_class("ft-focused")
        else:
            self.remove_css_class("ft-focused")

    def nav_step(self, direction: int, multiplier: float = 1.0):
        pass

    def nav_mute(self):
        pass

    def nav_activate(self):
        pass

    def update(self):
        pass


def _name_block(name: str, subtitle: str | None = None, bold=False):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    title = Gtk.Label(label=name, xalign=0, ellipsize=3)
    title.add_css_class("ft-name")
    if bold:
        title.add_css_class("ft-default")
    box.append(title)
    sub = Gtk.Label(xalign=0, ellipsize=3)
    sub.add_css_class("ft-subtitle")
    box.append(sub)
    if subtitle:
        sub.set_text(subtitle)
    else:
        sub.set_visible(False)
    return box, title, sub


# --------------------------------------------------------------- devices


class DeviceRow(NavRow):
    """Output device: default selector, AutoEQ wand, mute, volume slider, percentage."""

    def __init__(self, ctx, dev):
        super().__init__(ctx)
        self.uid = dev.uid
        self.icon = DeviceIconButton(on_click=self._make_default)
        self.append(self.icon)
        block, self.title, self.subtitle = _name_block(dev.name)
        block.set_hexpand(True)
        block.set_size_request(150, -1)
        self.append(block)
        self.wand = Gtk.MenuButton()
        self.wand.add_css_class("flat")
        self.wand.add_css_class("ft-icon-button")
        self.wand.set_valign(Gtk.Align.CENTER)
        self.wand.set_icon_name("ft-wand-symbolic")
        self.wand.set_tooltip_text("Headphone correction (AutoEQ)")
        self.wand.set_popover(AutoEQPopover(self.engine, dev.uid))
        self.append(self.wand)
        self.mute = icon_button("audio-volume-high-symbolic", "Mute")
        self.mute.connect("clicked", lambda *_: self.nav_mute())
        self.append(self.mute)
        self.slider = VolumeSlider(self._on_slider, self.step, width=160)
        self.append(self.slider)
        self.percent = EditablePercentage(lambda v: self._on_slider(v / 100))
        self.append(self.percent)
        self.update()

    @property
    def dev(self):
        return self.engine.output_by_uid(self.uid)

    def _make_default(self):
        self.engine.set_default_output(self.uid)
        self.ctx.refresh_soon()

    def _on_slider(self, v):
        dev = self.dev
        if dev:
            self.engine.set_device_volume(dev, v)
            self.ctx.on_volume_feedback(dev)
            self._update_value_widgets(v, self.engine.device_muted(dev))

    def _update_value_widgets(self, frac, muted):
        self.percent.set_percent(int(round(frac * 100)))
        self.mute.set_icon_name(speaker_icon(frac, muted))
        if muted:
            self.mute.add_css_class("ft-muted")
        else:
            self.mute.remove_css_class("ft-muted")
        self.slider.set_muted_style(muted)

    def update(self):
        dev = self.dev
        if not dev:
            return
        e = self.engine
        is_default = dev.uid == e.default_output_uid()
        tier = e.volume_tier(dev)
        self.icon.update(e.settings.get_device_icon_override(dev.uid) or dev.icon_name, is_default,
                         "Default output" if is_default else "Make default output")
        self.title.set_text(dev.name)
        if is_default:
            self.title.add_css_class("ft-default")
        else:
            self.title.remove_css_class("ft-default")
        profile = e.auto_eq_profile_name(dev.uid)
        sel = e.get_auto_eq_selection(dev.uid)
        self.subtitle.set_text(profile or "")
        self.subtitle.set_visible(bool(profile))
        self.wand.set_visible(dev.supports_auto_eq)
        if sel and sel.is_enabled:
            self.wand.remove_css_class("ft-wand-inactive")
            self.wand.add_css_class("ft-active")
        else:
            self.wand.remove_css_class("ft-active")
            self.wand.add_css_class("ft-wand-inactive")
        frac = e.device_volume(dev)
        muted = e.device_muted(dev)
        self.slider.set_value_silently(frac)
        self._update_value_widgets(frac, muted)
        self.slider.set_tooltip_text(f"{tier.label} volume")

    def nav_step(self, direction, multiplier=1.0):
        dev = self.dev
        if dev:
            self.engine.step_device_volume(dev, direction, self.step() * multiplier)
            self.update()

    def nav_mute(self):
        dev = self.dev
        if dev:
            self.engine.toggle_device_mute(dev)
            self.update()

    def nav_activate(self):
        self._make_default()


class InputDeviceRow(NavRow):
    def __init__(self, ctx, dev):
        super().__init__(ctx)
        self.uid = dev.uid
        self.vu = VUMeter(height=28)
        self.append(self.vu)
        self.icon = DeviceIconButton(on_click=self._make_default)
        self.append(self.icon)
        block, self.title, self.subtitle = _name_block(dev.name)
        block.set_hexpand(True)
        block.set_size_request(150, -1)
        self.append(block)
        self.mute = icon_button("audio-input-microphone-symbolic", "Mute")
        self.mute.connect("clicked", lambda *_: self.nav_mute())
        self.append(self.mute)
        self.slider = VolumeSlider(self._on_slider, self.step, width=160)
        self.append(self.slider)
        self.percent = EditablePercentage(lambda v: self._on_slider(v / 100))
        self.append(self.percent)
        self.update()

    @property
    def dev(self):
        return self.engine.input_by_uid(self.uid)

    def _make_default(self):
        self.engine.set_default_input(self.uid)
        self.ctx.refresh_soon()

    def _on_slider(self, v):
        dev = self.dev
        if dev:
            self.engine.set_device_volume(dev, v)
            self.percent.set_percent(int(round(v * 100)))

    def update(self):
        dev = self.dev
        if not dev:
            return
        is_default = dev.uid == self.engine.pulse.default_source
        self.icon.update(self.engine.settings.get_device_icon_override(dev.uid) or dev.icon_name, is_default,
                         "Default input" if is_default else "Make default input")
        self.title.set_text(dev.name)
        if is_default:
            self.title.add_css_class("ft-default")
        else:
            self.title.remove_css_class("ft-default")
        frac = min(dev.volume, 1.0)
        self.slider.set_value_silently(frac)
        self.percent.set_percent(int(round(frac * 100)))
        self.mute.set_icon_name(mic_icon(dev.muted))
        if dev.muted:
            self.mute.add_css_class("ft-muted")
        else:
            self.mute.remove_css_class("ft-muted")
        self.slider.set_muted_style(dev.muted)

    def update_level(self, levels):
        dev = self.dev
        self.vu.set_level(levels.get(self.uid, 0.0), dev.muted if dev else False)

    def nav_step(self, direction, multiplier=1.0):
        dev = self.dev
        if dev:
            if direction > 0 and dev.muted:
                self.engine.set_device_mute(dev, False)
            self.engine.set_device_volume(dev, max(0.0, min(1.0, dev.volume + direction * self.step() * multiplier)))
            self.update()

    def nav_mute(self):
        dev = self.dev
        if dev:
            self.engine.set_device_mute(dev, not dev.muted)
            self.update()

    def nav_activate(self):
        self._make_default()


# ------------------------------------------------------------------ apps


class AppRow(NavRow):
    """App: VU meter, icon, name/route, mute, slider, %, boost, device picker, EQ toggle + panel."""

    def __init__(self, ctx, ident: str, name: str, icon, bundle_id=None, active=True):
        super().__init__(ctx, orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.ident = ident
        self.name = name
        self.bundle_id = bundle_id
        self.active = active
        line = Gtk.Box(spacing=8)
        self.append(line)
        self.vu = VUMeter(height=30)
        line.append(self.vu)
        line.append(gicon_image(icon, 28))
        block, self.title, self.subtitle = _name_block(name)
        block.set_hexpand(True)
        block.set_size_request(120, -1)
        line.append(block)
        self.mute = icon_button("audio-volume-high-symbolic", "Mute")
        self.mute.connect("clicked", lambda *_: self.nav_mute())
        line.append(self.mute)
        self.slider = VolumeSlider(self._on_slider, self.step, width=130)
        line.append(self.slider)
        self.percent = EditablePercentage(lambda v: self._on_slider(v / 100))
        line.append(self.percent)
        self.boost = BoostButton(self._cycle_boost)
        line.append(self.boost)
        self.device_btn = Gtk.MenuButton()
        self.device_btn.add_css_class("flat")
        self.device_btn.add_css_class("ft-icon-button")
        self.device_btn.set_valign(Gtk.Align.CENTER)
        self.device_overlay = Gtk.Overlay()
        self.device_img = Gtk.Image.new_from_icon_name("ft-globe-symbolic")
        self.device_overlay.set_child(self.device_img)
        self.badge = Gtk.Label()
        self.badge.add_css_class("ft-count-badge")
        self.badge.set_halign(Gtk.Align.END)
        self.badge.set_valign(Gtk.Align.END)
        self.badge.set_margin_end(-8)
        self.badge.set_margin_bottom(-8)
        self.device_overlay.add_overlay(self.badge)
        self.device_btn.set_child(self.device_overlay)
        self.device_btn.set_popover(DevicePickerPopover(self.engine, ident, on_changed=self.ctx.refresh_soon))
        line.append(self.device_btn)
        self.eq_btn = icon_button("ft-eq-symbolic", "Equalizer")
        self.eq_btn.connect("clicked", lambda *_: self.ctx.toggle_expanded(self.ident))
        line.append(self.eq_btn)
        self.eq_panel = None
        if ctx.expanded == ident:
            self.add_css_class("ft-expanded")
            self.eq_btn.set_icon_name("window-close-symbolic")
            self.eq_panel = EQPanel(self.engine, ident)
            self.append(self.eq_panel)
        if not active:
            self.vu.set_opacity(0.35)
            line.add_css_class("ft-dimmed-soft")
        self.update()

    def _on_slider(self, v):
        self.engine.set_volume(self.ident, slider_to_gain(v))
        if v > 0 and self.engine.get_mute(self.ident):
            self.engine.set_mute(self.ident, False)
        self._update_value_widgets()

    def _cycle_boost(self):
        self.engine.set_boost(self.ident, next_boost(self.engine.get_boost(self.ident)))
        self.update()

    def _update_value_widgets(self):
        e = self.engine
        slider = gain_to_slider(e.get_volume(self.ident))
        muted = e.get_mute(self.ident)
        pct = int(round(slider * 100))
        self.percent.set_percent(pct)
        self.mute.set_icon_name(speaker_icon(slider, muted or pct == 0))
        if muted:
            self.mute.add_css_class("ft-muted")
        else:
            self.mute.remove_css_class("ft-muted")
        self.slider.set_muted_style(muted)
        return slider

    def update(self):
        e = self.engine
        slider = self._update_value_widgets()
        self.slider.set_value_silently(slider)
        self.boost.set_boost(e.get_boost(self.ident))
        mode = e.get_device_selection_mode(self.ident)
        outs = e.current_outputs(self.ident) if self.active else e.resolve_outputs(self.ident)
        following = e.is_following_default(self.ident)
        sub = ""
        if mode == DEVICE_SELECTION_MULTI:
            n = len(outs)
            sub = f"Multi · {n} device{'s' if n != 1 else ''}"
            first = e.output_by_uid(outs[0]) if outs else None
            icon = (e.settings.get_device_icon_override(first.uid) or first.icon_name) if first else "ft-globe-symbolic"
            self.badge.set_text(str(n))
            self.badge.set_visible(n > 1)
            self.device_btn.set_tooltip_text(sub)
        else:
            self.badge.set_visible(False)
            if following:
                icon = "ft-globe-symbolic"
                d = e.default_output()
                self.device_btn.set_tooltip_text(f"System Audio · {d.name}" if d else "System Audio")
                routed = e.settings.get_device_routing(self.ident)
                if routed and not e.output_by_uid(routed):
                    sub = "Device disconnected · using default"
            else:
                d = e.output_by_uid(outs[0]) if outs else None
                icon = (e.settings.get_device_icon_override(d.uid) or d.icon_name) if d else "ft-globe-symbolic"
                sub = d.name if d else ""
                self.device_btn.set_tooltip_text(sub)
        self.device_img.set_from_icon_name(icon)
        if not self.active:
            sub = (sub + " · " if sub else "") + "Not playing"
        self.subtitle.set_text(sub)
        self.subtitle.set_visible(bool(sub))

    def update_level(self, levels):
        self.vu.set_level(levels.get(self.ident, 0.0), self.engine.get_mute(self.ident))

    def nav_step(self, direction, multiplier=1.0):
        self.engine.step_app_volume(self.ident, direction, self.step() * multiplier)
        self.update()

    def nav_mute(self):
        self.engine.toggle_mute(self.ident)
        self.update()

    def nav_activate(self):
        self.ctx.toggle_expanded(self.ident)


# ------------------------------------------------------------- edit mode


class DeviceEditRow(NavRow):
    """Edit mode: drag handle, priority number, icon picker, name, DEFAULT badge, hide, inspector."""

    def __init__(self, ctx, dev, position: int, is_input: bool, hidden: bool):
        super().__init__(ctx, orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.uid = dev.uid
        self.is_input = is_input
        self.add_css_class("ft-card")
        line = Gtk.Box(spacing=8)
        self.append(line)
        handle = Gtk.Image.new_from_icon_name("ft-drag-symbolic")
        handle.set_opacity(0.5)
        handle.set_tooltip_text("Drag to reorder")
        line.append(handle)

        self.prio = Gtk.Entry(text=str(position), width_chars=2, max_width_chars=2, xalign=0.5)
        self.prio.add_css_class("ft-priority")
        self.prio.add_css_class("flat")
        self.prio.set_valign(Gtk.Align.CENTER)
        self.prio.set_tooltip_text("Type a number to set priority")
        self.prio.connect("activate", self._on_prio)
        line.append(self.prio)

        icon_btn = Gtk.MenuButton()
        icon_btn.add_css_class("flat")
        icon_btn.add_css_class("ft-icon-button")
        icon_btn.set_icon_name(ctx.engine.settings.get_device_icon_override(dev.uid) or dev.icon_name)
        icon_btn.set_tooltip_text("Change icon")
        icon_btn.set_popover(IconPickerPopover(ctx.engine, dev.uid, on_changed=ctx.rebuild))
        line.append(icon_btn)

        is_default = dev.uid == (ctx.engine.pulse.default_source if is_input else ctx.engine.default_output_uid())
        name = Gtk.Label(label=dev.name, xalign=0, ellipsize=3)
        name.add_css_class("ft-name")
        if is_default:
            name.add_css_class("ft-default")
        line.append(name)
        if is_default:
            badge = Gtk.Label(label="DEFAULT")
            badge.add_css_class("ft-badge")
            badge.set_valign(Gtk.Align.CENTER)
            line.append(badge)
        line.append(Gtk.Box(hexpand=True))
        if hidden:
            name.add_css_class("ft-dimmed")
        eye = icon_button("view-conceal-symbolic" if hidden else "view-reveal-symbolic",
                          "Show device" if hidden else "Hide device")
        if hidden:
            eye.add_css_class("ft-muted")
        eye.connect("clicked", lambda *_: self._toggle_hidden())
        line.append(eye)
        expanded = ctx.inspected == dev.uid
        info = icon_button("window-close-symbolic" if expanded else "help-about-symbolic",
                           "Close" if expanded else "Device info")
        info.connect("clicked", lambda *_: ctx.toggle_inspector(dev.uid))
        line.append(info)
        if expanded:
            self.append(DeviceInspector(ctx, dev))

        # drag & drop reordering
        src = Gtk.DragSource.new()
        src.set_actions(Gdk.DragAction.MOVE)
        src.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(self.uid))
        handle_box = line
        handle_box.add_controller(src)
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

    def _order(self):
        return self.ctx.connected_priority_order(self.is_input)

    def _apply(self, order):
        s = self.engine.settings
        if self.is_input:
            s.merge_input_device_priority_order(order)
        else:
            s.merge_device_priority_order(order)
        self.ctx.rebuild()

    def _on_prio(self, entry):
        order = self._order()
        try:
            pos = int(entry.get_text()) - 1
        except ValueError:
            return
        if self.uid not in order:
            return
        order.remove(self.uid)
        order.insert(max(0, min(len(order), pos)), self.uid)
        self._apply(order)

    def _on_drop(self, _t, value, _x, y):
        order = self._order()
        if value not in order or value == self.uid:
            return False
        order.remove(value)
        idx = order.index(self.uid)
        if y > self.get_height() / 2:
            idx += 1
        order.insert(idx, value)
        self._apply(order)
        return True

    def _toggle_hidden(self):
        s = self.engine.settings
        if self.is_input:
            s.toggle_input_device_hidden(self.uid)
        else:
            s.toggle_output_device_hidden(self.uid)
        self.ctx.rebuild()


class DeviceInspector(Gtk.Box):
    """Transport, sample rate (graph rate picker), format, port, UID copy, software-volume override."""

    def __init__(self, ctx, dev):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("ft-inner-card")
        e = ctx.engine
        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        r = 0

        def row(label, widget):
            nonlocal r
            l = Gtk.Label(label=label, xalign=0, hexpand=True)
            l.add_css_class("dim-label")
            grid.attach(l, 0, r, 1, 1)
            widget.set_halign(Gtk.Align.END)
            grid.attach(widget, 1, r, 1, 1)
            r += 1

        row("Transport", Gtk.Label(label=dev.transport))
        rates = sorted(set(COMMON_RATES) | ({dev.rate} if dev.rate else set()))
        labels = ["Automatic"] + [f"{x / 1000:g} kHz" for x in rates]
        dd = Gtk.DropDown.new_from_strings(labels)
        dd.set_tooltip_text("PipeWire graph sample rate (clock.force-rate). Current device rate: "
                            f"{dev.rate / 1000:g} kHz" if dev.rate else "PipeWire graph sample rate")
        dd.set_selected(0)
        dd.connect("notify::selected", lambda d, *_: e.set_sample_rate(0 if d.get_selected() == 0
                                                                       else rates[d.get_selected() - 1]))
        rate_box = Gtk.Box(spacing=6)
        cur = Gtk.Label(label=f"{dev.rate / 1000:g} kHz" if dev.rate else "—")
        rate_box.append(cur)
        rate_box.append(dd)
        row("Sample rate", rate_box)
        row("Format", Gtk.Label(label=f"{dev.format_label} · {dev.channels} ch"))
        if dev.ports and len(dev.ports) > 1:
            names = [p.get("name") for p in dev.ports]
            descs = [p.get("description") or p.get("name") for p in dev.ports]
            pd = Gtk.DropDown.new_from_strings(descs)
            if dev.active_port in names:
                pd.set_selected(names.index(dev.active_port))
            pd.connect("notify::selected", lambda d, *_: e.set_device_port(dev, names[d.get_selected()]))
            row("Port", pd)
        uid_box = Gtk.Box(spacing=4)
        ul = Gtk.Label(label=dev.uid, ellipsize=1, max_width_chars=28, selectable=True)
        ul.add_css_class("ft-mono")
        uid_box.append(ul)
        cp = icon_button("edit-copy-symbolic", "Copy device UID")
        cp.connect("clicked", lambda b: copy_to_clipboard(b, dev.uid))
        uid_box.append(cp)
        row("Device ID", uid_box)
        self.append(grid)

        if not dev.is_input:
            self.append(Gtk.Separator())
            auto = e.auto_volume_tier(dev)
            ov = e.settings.get_device_volume_tier_override(dev.uid)
            if auto != VolumeTier.SOFTWARE:
                line = Gtk.Box(spacing=8)
                badge = Gtk.Label(label=f"Auto: {auto.label}")
                badge.add_css_class("ft-badge")
                badge.set_valign(Gtk.Align.CENTER)
                line.append(badge)
                line.append(Gtk.Label(label="Use FineTune's software volume", xalign=0, hexpand=True))
                sw = Gtk.Switch(active=ov == VolumeTier.SOFTWARE, valign=Gtk.Align.CENTER)
                sw.connect("notify::active", lambda w, *_: (
                    e.set_volume_tier_override(dev, VolumeTier.SOFTWARE if w.get_active() else None),
                    ctx.refresh_soon()))
                line.append(sw)
                self.append(line)
                note = Gtk.Label(label="Turn on only if the volume slider doesn't work. FineTune remembers this for each device.",
                                 xalign=0, wrap=True)
                note.add_css_class("ft-subtitle")
                self.append(note)


class PairedDeviceRow(Gtk.Box):
    def __init__(self, ctx, bt):
        super().__init__(spacing=8)
        self.add_css_class("ft-card")
        self.add_css_class("ft-row")
        icon = Gtk.Image.new_from_icon_name(bt.icon_name)
        icon.set_pixel_size(20)
        icon.set_margin_start(26)
        self.append(icon)
        t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        nl = Gtk.Label(label=bt.name, xalign=0)
        nl.add_css_class("ft-name")
        t.append(nl)
        if bt.error:
            el = Gtk.Label(label=bt.error, xalign=0)
            el.add_css_class("ft-error-text")
            t.append(el)
        self.append(t)
        if bt.connecting:
            sp = Gtk.Spinner(spinning=True)
            self.append(sp)
        else:
            b = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda *_: ctx.engine.bluetooth.connect_device(bt))
            self.append(b)


class AppEditTile(Gtk.Box):
    """Edit mode app tile: pin (keep visible when idle) and eye (ignore app entirely)."""

    def __init__(self, ctx, ident, name, icon, bundle_id, ignored: bool):
        super().__init__(spacing=8)
        self.add_css_class("ft-card")
        self.set_hexpand(True)
        img = gicon_image(icon, 24)
        self.append(img)
        lbl = Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=3)
        lbl.add_css_class("ft-name")
        if ignored:
            lbl.add_css_class("ft-dimmed")
            img.add_css_class("ft-dimmed")
        self.append(lbl)
        e = ctx.engine
        pinned = e.settings.is_pinned(ident)
        pin = icon_button("ft-pin-symbolic" if pinned else "ft-pin-off-symbolic",
                          "Unpin" if pinned else "Pin (keep visible when not playing)")
        if pinned:
            pin.add_css_class("ft-active")
        pin.set_sensitive(not ignored)
        pin.connect("clicked", lambda *_: (e.unpin_app(ident) if pinned else e.pin_app(ident, name, bundle_id),
                                           ctx.rebuild()))
        self.append(pin)
        eye = icon_button("view-conceal-symbolic" if ignored else "view-reveal-symbolic",
                          "Stop ignoring" if ignored else "Ignore app (FineTune won't touch its audio)")
        if ignored:
            eye.add_css_class("ft-muted")
        eye.connect("clicked", lambda *_: (e.unignore_app(ident) if ignored else e.ignore_app(ident, name, bundle_id),
                                           ctx.rebuild()))
        self.append(eye)

