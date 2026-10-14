"""The menu-bar popup (port of MenuBarPopupView + MenuBarPopupController + PopupKeyboardNavModel).

On wlroots/Hyprland/KDE it is a layer-shell surface anchored to the top-right
corner (where the tray usually lives); elsewhere it is a borderless window.
It closes on Escape and when it loses focus (click outside).
"""
from __future__ import annotations

import logging

from gi.repository import Adw, Gdk, GLib, Gtk

from finetune import DONATE_URL
from finetune.settings import POPUP_DIMENSIONS
from finetune.ui import layer
from finetune.ui.rows import (
    AppEditTile, AppRow, DeviceEditRow, DeviceRow, InputDeviceRow, PairedDeviceRow,
)
from finetune.ui.widgets import clear_box, icon_button, open_uri
from finetune.util import feedback

log = logging.getLogger("finetune.popup")



class PopupWindow(Gtk.ApplicationWindow):
    def __init__(self, app, engine):
        super().__init__(application=app, title="FineTune")
        self.app = app
        self.engine = engine
        self.add_css_class("ft-popup")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_hide_on_close(True)
        self.is_layer = layer.setup_popup(self)
        self._pointer_inside = False
        self._armed = False          # the pointer has been over the popup since it opened
        self._fade = None

        self.tab = "output"
        self.edit_mode = False
        self.expanded: str | None = None
        self.inspected: str | None = None
        self._signature = None
        self._rows: list = []
        self._nav_rows: list = []
        self._focus_index = -1
        self._refresh_id = 0
        self._hide_check_id = 0

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.panel.add_css_class("ft-panel")
        self.set_child(self.panel)
        self._build_header()
        self.banner = Gtk.Box(spacing=8)
        self.banner.add_css_class("ft-banner")
        self.banner.set_visible(False)
        self.panel.append(self.banner)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.scroller.set_child(self.content)
        self.panel.append(self.scroller)
        self.panel.append(Gtk.Separator())
        self._build_footer()
        self._apply_size()

        key = Gtk.EventControllerKey.new()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)
        self.connect("notify::is-active", self._on_active_changed)
        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", self._on_pointer_enter)
        motion.connect("leave", self._on_pointer_leave)
        self.add_controller(motion)

        engine.connect_changed(self._on_engine_changed)
        engine.connect_levels(self._on_levels)
        engine.settings.connect_app_settings_changed(lambda o, n: (self._apply_size(), self.rebuild()))

    # ---------------------------------------------------------------- layout

    def _apply_size(self):
        dims = POPUP_DIMENSIONS[self.engine.settings.app_settings.popup_size]
        self.panel.set_size_request(dims["width"], -1)
        for m in ("top", "bottom", "start", "end"):
            getattr(self.panel, f"set_margin_{m}")(8)
        pad = dims["padding"]
        self.content.set_margin_start(pad - 6)
        self.content.set_margin_end(pad - 6)
        self.header.set_margin_start(pad)
        self.header.set_margin_end(pad)
        self.header.set_margin_top(pad)
        self.footer.set_margin_start(pad)
        self.footer.set_margin_end(pad)
        self.footer.set_margin_bottom(pad - 4)
        self.scroller.set_max_content_height(dims["max_height"])

    def _build_header(self):
        self.header = Gtk.CenterBox()
        tabs = Gtk.Box()
        tabs.add_css_class("ft-tabs")
        self.out_tab = Gtk.ToggleButton(icon_name="audio-volume-high-symbolic", active=True,
                                        tooltip_text="Output Devices")
        self.in_tab = Gtk.ToggleButton(icon_name="audio-input-microphone-symbolic", group=self.out_tab,
                                       tooltip_text="Input Devices")
        self.out_tab.connect("toggled", lambda b: b.get_active() and self.set_tab("output"))
        self.in_tab.connect("toggled", lambda b: b.get_active() and self.set_tab("input"))
        tabs.append(self.out_tab)
        tabs.append(self.in_tab)
        self.header.set_start_widget(tabs)
        self.title = Gtk.Label(ellipsize=3, max_width_chars=40)
        self.title.add_css_class("ft-header-title")
        self.header.set_center_widget(self.title)
        end = Gtk.Box(spacing=2)
        self.edit_btn = icon_button("document-edit-symbolic", "Edit devices and apps")
        self.edit_btn.connect("clicked", lambda *_: self.toggle_edit())
        end.append(self.edit_btn)
        gear = icon_button("emblem-system-symbolic", "Settings")
        gear.connect("clicked", lambda *_: self.app.show_settings())
        end.append(gear)
        self.header.set_end_widget(end)
        self.panel.append(self.header)

    def _build_footer(self):
        self.footer = Gtk.Box(spacing=8)
        donate = Gtk.Button()
        donate.add_css_class("flat")
        donate.add_css_class("ft-link")
        db = Gtk.Box(spacing=6)
        db.append(Gtk.Image.new_from_icon_name("emote-love-symbolic"))
        db.append(Gtk.Label(label="Donate"))
        donate.set_child(db)
        donate.set_tooltip_text("Donate to FineTune")
        donate.connect("clicked", lambda *_: open_uri(DONATE_URL))
        self.footer.append(donate)
        self.footer.append(Gtk.Box(hexpand=True))
        self.status_label = Gtk.Label()
        self.status_label.add_css_class("ft-subtitle")
        self.footer.append(self.status_label)
        quit_btn = Gtk.Button()
        quit_btn.add_css_class("flat")
        quit_btn.add_css_class("ft-footer-button")
        qb = Gtk.Box(spacing=8)
        qb.append(Gtk.Label(label="Quit"))
        k = Gtk.Label(label="Ctrl+Q")
        k.add_css_class("dim-label")
        qb.append(k)
        quit_btn.set_child(qb)
        quit_btn.set_tooltip_text("Quit FineTune (Ctrl+Q)")
        quit_btn.connect("clicked", lambda *_: self.app.quit_app())
        self.footer.append(quit_btn)
        self.panel.append(self.footer)

    # ---------------------------------------------------------------- show/hide

    def toggle(self):
        if self.get_visible():
            self.close_popup(animated=True)
        else:
            self.open_popup()

    def open_popup(self):
        log.debug("open popup")
        self.engine.desktop.reload()
        self.rebuild()
        self._pointer_inside = False
        self._armed = False
        self._cancel_hide_check()
        self._animate(0.0, 1.0, 140)
        self.present()
        if self.tab == "input":
            self._start_meters()

    def close_popup(self, animated=False):
        """Hide the popup; animated=True fades it out first (used for click/hover-away)."""
        self._cancel_hide_check()
        if animated and self.get_visible():
            self._animate(self.get_opacity(), 0.0, 220, done=self._hide_now)
            return
        self._hide_now()

    def _hide_now(self):
        self._set_focus_index(-1)
        self.engine.stop_all_input_meters()
        if self.edit_mode:
            self.toggle_edit()
        self.set_visible(False)
        self.set_opacity(1.0)

    def _animate(self, start, end, ms, done=None):
        if self._fade is not None:
            self._fade.pause()
        self.set_opacity(start)
        target = Adw.CallbackAnimationTarget.new(lambda v: self.set_opacity(v))
        anim = Adw.TimedAnimation.new(self, start, end, ms, target)
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        if done:
            anim.connect("done", lambda *_: done())
        self._fade = anim
        anim.play()

    # Closing on outside interaction. A full-screen "click catcher" doesn't receive
    # input on every compositor, and an exclusive keyboard grab would lock the desktop,
    # so the popup fades away when the pointer leaves it and it loses focus
    # (a click elsewhere, or focus-follows-mouse moving focus to another window).

    def _on_pointer_enter(self, *_):
        self._pointer_inside = True
        self._armed = True
        self._cancel_hide_check()

    def _on_pointer_leave(self, *_):
        self._pointer_inside = False
        if self._armed:
            self._schedule_hide_check(700)

    def _on_active_changed(self, *_):
        if self.is_active():
            self._armed = True
            return
        if self.get_visible() and self._armed:
            self._schedule_hide_check(250)

    def _cancel_hide_check(self):
        if self._hide_check_id:
            GLib.source_remove(self._hide_check_id)
            self._hide_check_id = 0

    def _schedule_hide_check(self, ms):
        self._cancel_hide_check()

        def check():
            self._hide_check_id = 0
            if not self.get_visible() or self._pointer_inside or self._has_open_popover():
                return False
            if not self.is_active():
                self.close_popup(animated=True)
            return False

        self._hide_check_id = GLib.timeout_add(ms, check)

    def _has_open_popover(self) -> bool:
        stack = [self.panel]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.MenuButton) and w.get_active():
                return True
            if isinstance(w, Gtk.Popover) and w.get_visible():
                return True
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        return False

    # ---------------------------------------------------------------- state

    def set_tab(self, tab):
        if tab == self.tab:
            return
        self.tab = tab
        self.out_tab.set_active(tab == "output")
        self.in_tab.set_active(tab == "input")
        if tab == "input":
            self._start_meters()
        else:
            self.engine.stop_all_input_meters()
        self.rebuild()

    def toggle_edit(self):
        self.edit_mode = not self.edit_mode
        self.inspected = None
        self.edit_btn.set_icon_name("object-select-symbolic" if self.edit_mode else "document-edit-symbolic")
        self.edit_btn.set_tooltip_text("Done" if self.edit_mode else "Edit devices and apps")
        self.rebuild()

    def toggle_expanded(self, ident):
        self.expanded = None if self.expanded == ident else ident
        self.rebuild()

    def toggle_inspector(self, uid):
        self.inspected = None if self.inspected == uid else uid
        self.rebuild()

    def refresh_soon(self):
        if not self._refresh_id:
            self._refresh_id = GLib.timeout_add(60, self._refresh)

    def _refresh(self):
        self._refresh_id = 0
        self._on_engine_changed()
        return False

    def on_volume_feedback(self, dev):
        if self.engine.settings.app_settings.volume_feedback_enabled:
            feedback.play(dev.uid)

    def connected_priority_order(self, is_input: bool) -> list[str]:
        s = self.engine.settings
        devs = self.engine.inputs if is_input else self.engine.outputs
        prio = s.input_device_priority if is_input else s.output_device_priority
        uids = [d.uid for d in devs]
        ordered = [u for u in prio if u in uids]
        return ordered + [u for u in uids if u not in ordered]

    def _start_meters(self):
        for d in self.engine.inputs:
            if d.uid not in self.engine.settings.hidden_input_device_uids:
                self.engine.start_input_meter(d.uid)

    # ---------------------------------------------------------------- render

    def _sorted_devices(self, is_input):
        order = self.connected_priority_order(is_input)
        devs = {d.uid: d for d in (self.engine.inputs if is_input else self.engine.outputs)}
        return [devs[u] for u in order if u in devs]

    def _compute_signature(self):
        e = self.engine
        s = e.settings
        return (
            self.tab, self.edit_mode, self.expanded, self.inspected,
            tuple(d.uid for d in self._sorted_devices(self.tab == "input")),
            tuple(sorted(s.hidden_output_device_uids if self.tab == "output" else s.hidden_input_device_uids)),
            tuple((k, getattr(v, "identifier", None) or v.get("persistenceIdentifier")) for k, v in e.displayable_apps()),
            tuple(sorted(s.ignored_apps)), tuple(sorted(s.pinned_apps)),
            tuple((b.path, b.connecting, b.error) for b in e.bluetooth.paired_disconnected()) if self.edit_mode else (),
            tuple(sorted(s.device_icon_overrides.items())),
            s.app_settings.popup_size,
            e.default_output_uid() if self.edit_mode else None,
            e.pulse.default_source if self.edit_mode else None,
        )

    def _on_engine_changed(self):
        if not self.get_visible():
            self._update_title()
            return
        sig = self._compute_signature()
        if sig != self._signature:
            self.rebuild()
        else:
            for r in self._rows:
                if hasattr(r, "update"):
                    r.update()
            self._update_title()

    def _on_levels(self, levels):
        if not self.get_visible():
            return
        for r in self._rows:
            if hasattr(r, "update_level"):
                r.update_level(levels)

    def _update_title(self):
        e = self.engine
        if self.edit_mode:
            self.title.set_text("Drag or type a number to set priority")
        else:
            out = e.default_output()
            inp = e.default_input()
            parts = []
            if out:
                parts.append(f"🔊 {out.name}")
            if inp:
                parts.append(f"🎙 {inp.name}")
            self.title.set_text(" · ".join(parts))
        # engine/backend status banner
        clear_box(self.banner)
        msg = None
        if not e.pulse.available:
            msg = "pactl not found — install pipewire-pulse to let FineTune see your audio devices."
        elif e.client.last_error:
            msg = e.client.last_error + " — per-app processing is offline; build engine/ with `make`."
        elif e.client.failures and not e.client.ready:
            msg = "Audio engine restarting…"
        if msg:
            self.banner.append(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
            l = Gtk.Label(label=msg, wrap=True, xalign=0, hexpand=True)
            self.banner.append(l)
        self.banner.set_visible(bool(msg))

    def _section_title(self, text):
        l = Gtk.Label(label=text, xalign=0)
        l.add_css_class("ft-section-title")
        l.set_margin_start(6)
        return l

    def rebuild(self):
        self._signature = self._compute_signature()
        focused_key = None
        if 0 <= self._focus_index < len(self._nav_rows):
            r = self._nav_rows[self._focus_index]
            focused_key = getattr(r, "uid", None) or getattr(r, "ident", None)
        clear_box(self.content)
        self._rows, self._nav_rows = [], []
        e = self.engine
        s = e.settings
        is_input = self.tab == "input"
        hidden = s.hidden_input_device_uids if is_input else s.hidden_output_device_uids
        devices = self._sorted_devices(is_input)

        if self.edit_mode:
            for pos, d in enumerate(devices, 1):
                row = DeviceEditRow(self, d, pos, is_input, d.uid in hidden)
                self.content.append(row)
            paired = e.bluetooth.paired_disconnected()
            if paired and not is_input:
                self.content.append(self._section_title("PAIRED"))
                for bt in paired:
                    self.content.append(PairedDeviceRow(self, bt))
        else:
            visible = [d for d in devices if d.uid not in hidden]
            for d in visible:
                row = InputDeviceRow(self, d) if is_input else DeviceRow(self, d)
                self.content.append(row)
                self._rows.append(row)
                self._nav_rows.append(row)
            if not visible:
                l = Gtk.Label(label="No input devices" if is_input else "No output devices")
                l.add_css_class("dim-label")
                self.content.append(l)

        sep = Gtk.Separator()
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        self.content.append(sep)
        self.content.append(self._section_title("APPS"))

        if self.edit_mode:
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=2,
                               min_children_per_line=2, homogeneous=True, column_spacing=6, row_spacing=6)
            seen = set()
            for kind, obj in e.displayable_apps():
                if kind == "active":
                    flow.append(AppEditTile(self, obj.identifier, obj.name, obj.icon, obj.bundle_id, False))
                    seen.add(obj.identifier)
                else:
                    ident = obj["persistenceIdentifier"]
                    flow.append(AppEditTile(self, ident, obj.get("displayName", ident),
                                            e.app_icon(ident, obj.get("bundleID")), obj.get("bundleID"), False))
                    seen.add(ident)
            for info in s.get_ignored_app_info():
                ident = info["persistenceIdentifier"]
                if ident in seen:
                    continue
                app = e.apps.get(ident)
                flow.append(AppEditTile(self, ident, info.get("displayName", ident),
                                        app.icon if app else e.app_icon(ident, info.get("bundleID")),
                                        info.get("bundleID"), True))
            self.content.append(flow)
        else:
            apps = e.displayable_apps()
            for kind, obj in apps:
                if kind == "active":
                    row = AppRow(self, obj.identifier, obj.name, obj.icon, obj.bundle_id, active=True)
                else:
                    ident = obj["persistenceIdentifier"]
                    row = AppRow(self, ident, obj.get("displayName", ident), e.app_icon(ident, obj.get("bundleID")),
                                 obj.get("bundleID"), active=False)
                self.content.append(row)
                self._rows.append(row)
                self._nav_rows.append(row)
            if not apps:
                l = Gtk.Label(label="No apps playing audio")
                l.add_css_class("dim-label")
                l.set_margin_top(6)
                l.set_margin_bottom(6)
                self.content.append(l)
            ignored = len(s.ignored_apps)
            if ignored:
                l = Gtk.Label(label=f"{ignored} ignored · edit to manage", xalign=0)
                l.add_css_class("ft-subtitle")
                l.set_margin_start(6)
                self.content.append(l)

        self._update_title()
        # restore keyboard focus
        self._focus_index = -1
        if focused_key is not None:
            for i, r in enumerate(self._nav_rows):
                if (getattr(r, "uid", None) or getattr(r, "ident", None)) == focused_key:
                    self._set_focus_index(i)
                    break

    # ----------------------------------------------------------- keyboard

    def _set_focus_index(self, idx):
        if 0 <= self._focus_index < len(self._nav_rows):
            self._nav_rows[self._focus_index].set_focused(False)
        self._focus_index = idx
        if 0 <= idx < len(self._nav_rows):
            row = self._nav_rows[idx]
            row.set_focused(True)
            # autoscroll the focused row toward the centre
            ok, rect = row.compute_bounds(self.content)
            if ok:
                adj = self.scroller.get_vadjustment()
                target = rect.get_y() + rect.get_height() / 2 - adj.get_page_size() / 2
                adj.set_value(max(adj.get_lower(), min(adj.get_upper() - adj.get_page_size(), target)))

    def _on_key(self, _ctrl, keyval, _code, state):
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Text, Gtk.Entry)) or (focus and isinstance(focus.get_parent(), Gtk.Entry)):
            if keyval == Gdk.KEY_Escape:
                self.set_focus(None)
                return True
            return False
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        if keyval == Gdk.KEY_Escape:
            if self.expanded:
                self.expanded = None
                self.rebuild()
            else:
                self.close_popup()
            return True
        if ctrl and keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self.app.quit_app()
            return True
        if ctrl and keyval in (Gdk.KEY_e, Gdk.KEY_E):
            self.toggle_edit()
            return True
        if ctrl and keyval == Gdk.KEY_comma:
            self.app.show_settings()
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            self.set_tab("input" if self.tab == "output" else "output")
            return True
        n = len(self._nav_rows)
        if keyval == Gdk.KEY_Down and n:
            self._set_focus_index(min(n - 1, self._focus_index + 1))
            return True
        if keyval == Gdk.KEY_Up and n:
            self._set_focus_index(max(0, self._focus_index - 1) if self._focus_index >= 0 else 0)
            return True
        if 0 <= self._focus_index < n:
            row = self._nav_rows[self._focus_index]
            mult = 2.0 if shift else 1.0
            if keyval == Gdk.KEY_Right:
                row.nav_step(+1, mult)
                return True
            if keyval == Gdk.KEY_Left:
                row.nav_step(-1, mult)
                return True
            if keyval in (Gdk.KEY_m, Gdk.KEY_M):
                row.nav_mute()
                return True
            if keyval in (Gdk.KEY_d, Gdk.KEY_D) and hasattr(row, "device_btn"):
                row.device_btn.popup()
                return True
            if keyval in (Gdk.KEY_a, Gdk.KEY_A) and hasattr(row, "wand") and row.wand.get_visible():
                row.wand.popup()
                return True
            if keyval in (Gdk.KEY_b, Gdk.KEY_B) and hasattr(row, "boost"):
                row._cycle_boost()
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
                row.nav_activate()
                return True
        return False
