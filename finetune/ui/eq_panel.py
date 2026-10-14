"""Per-app 10-band EQ panel with presets and user presets (port of EQPanelView/EQSliderView/EQPresetPicker)."""
from __future__ import annotations

import math

from gi.repository import GLib, Gtk

from finetune.models import eq as eqm


class EQPanel(Gtk.Box):
    def __init__(self, engine, ident: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("ft-inner-card")
        self.engine = engine
        self.ident = ident
        self._updating = False
        self._commit_id = 0
        settings = engine.get_eq_settings(ident)

        header = Gtk.Box(spacing=10)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=settings.is_enabled)
        self.switch.connect("notify::active", self._on_toggle)
        header.append(self.switch)
        lbl = Gtk.Label(label="EQ", xalign=0)
        lbl.add_css_class("heading")
        header.append(lbl)
        header.append(Gtk.Box(hexpand=True))
        plbl = Gtk.Label(label="Preset")
        plbl.add_css_class("dim-label")
        header.append(plbl)
        self.preset_button = Gtk.MenuButton()
        self.preset_button.add_css_class("flat")
        self.preset_button.set_popover(self._build_preset_popover())
        header.append(self.preset_button)
        self.append(header)

        bands = Gtk.Box(spacing=0, homogeneous=True)
        self.scales: list[Gtk.Scale] = []
        for i, f in enumerate(eqm.FREQUENCIES):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            sc = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL)
            sc.set_range(eqm.MIN_GAIN_DB, eqm.MAX_GAIN_DB)
            sc.set_increments(0.5, 1.0)
            sc.set_inverted(True)
            sc.set_draw_value(False)
            sc.set_size_request(-1, 120)
            sc.set_halign(Gtk.Align.CENTER)
            sc.add_css_class("ft-eq-slider")
            for m in (-12, -6, 0, 6, 12):
                sc.add_mark(m, Gtk.PositionType.LEFT, None)
            sc.set_value(settings.band_gains[i])
            sc.set_tooltip_text(f"{eqm.format_frequency(f)} Hz: {settings.band_gains[i]:+.1f} dB")
            sc.connect("value-changed", self._on_band, i)
            # double-click resets a band to 0 dB
            dbl = Gtk.GestureClick.new()
            dbl.connect("pressed", self._on_band_click, i)
            sc.add_controller(dbl)
            scroll = Gtk.EventControllerScroll.new(
                Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE)
            scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            scroll.connect("scroll", self._on_scroll, sc)
            sc.add_controller(scroll)
            self.scales.append(sc)
            col.append(sc)
            fl = Gtk.Label(label=eqm.format_frequency(f).replace("31.25", "32").replace("62.5", "64"))
            fl.add_css_class("ft-eq-label")
            col.append(fl)
            ul = Gtk.Label(label="Hz")
            ul.add_css_class("ft-eq-unit")
            col.append(ul)
            bands.append(col)
        self.append(bands)
        self._refresh_preset_label()

    # ---------------------------------------------------------------- state

    def _current(self) -> eqm.EQSettings:
        return eqm.EQSettings([round(s.get_value() * 2) / 2 for s in self.scales], self.switch.get_active())

    def _commit(self):
        self._commit_id = 0
        self.engine.set_eq_settings(self.ident, self._current())
        self._refresh_preset_label()
        return False

    def _schedule_commit(self):
        # coalesce drags to ~30 updates/s
        if not self._commit_id:
            self._commit_id = GLib.timeout_add(33, self._commit)

    def _on_band(self, sc, i):
        sc.set_tooltip_text(f"{eqm.format_frequency(eqm.FREQUENCIES[i])} Hz: {sc.get_value():+.1f} dB")
        if not self._updating:
            self._schedule_commit()

    def _on_band_click(self, gesture, n_press, _x, _y, i):
        if n_press == 2:
            self.scales[i].set_value(0.0)

    def _on_scroll(self, _c, _dx, dy, sc):
        sc.set_value(max(eqm.MIN_GAIN_DB, min(eqm.MAX_GAIN_DB, sc.get_value() - math.copysign(1.0, dy))))
        return True

    def _on_toggle(self, *_):
        if not self._updating:
            self._commit()

    def apply_gains(self, gains):
        self._updating = True
        for sc, g in zip(self.scales, gains):
            sc.set_value(g)
        self._updating = False
        self._commit()

    def _refresh_preset_label(self):
        gains = [s.get_value() for s in self.scales]
        name = "Custom"
        p = eqm.matching_preset(gains)
        if p:
            name = p.name
        else:
            for up in self.engine.settings.get_user_presets():
                if all(abs(a - b) < 0.05 for a, b in zip(gains, up.settings.band_gains)):
                    name = up.name
                    break
        box = Gtk.Box(spacing=4)
        box.append(Gtk.Label(label=name))
        box.append(Gtk.Image.new_from_icon_name("pan-down-symbolic"))
        self.preset_button.set_child(box)

    # -------------------------------------------------------------- presets

    def _build_preset_popover(self) -> Gtk.Popover:
        pop = Gtk.Popover()
        pop.connect("show", lambda p: self._fill_presets(p))
        return pop

    def _menu_item(self, label, cb, bold=False, icon=None):
        btn = Gtk.Button()
        btn.add_css_class("flat")
        box = Gtk.Box(spacing=8)
        if icon:
            box.append(Gtk.Image.new_from_icon_name(icon))
        lbl = Gtk.Label(label=label, xalign=0, hexpand=True)
        if bold:
            lbl.add_css_class("heading")
        box.append(lbl)
        btn.set_child(box)
        btn.connect("clicked", lambda *_: cb())
        return btn

    def _fill_presets(self, pop: Gtk.Popover):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        outer.set_margin_top(6)
        outer.set_margin_bottom(6)
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=420,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        def choose(gains):
            pop.popdown()
            self.apply_gains(gains)

        user = self.engine.settings.get_user_presets()
        if user:
            t = Gtk.Label(label="MY PRESETS", xalign=0)
            t.add_css_class("ft-section-title")
            lst.append(t)
            for up in user:
                row = Gtk.Box(spacing=2)
                b = self._menu_item(up.name, lambda g=list(up.settings.band_gains): choose(g))
                b.set_hexpand(True)
                row.append(b)
                ren = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Rename preset")
                ren.add_css_class("flat")
                ren.connect("clicked", lambda _b, p=up: (pop.popdown(), self._rename_dialog(p)))
                row.append(ren)
                dele = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete preset")
                dele.add_css_class("flat")
                dele.connect("clicked", lambda _b, p=up: (self.engine.settings.delete_user_preset(p.id),
                                                          pop.popdown(), self._refresh_preset_label()))
                row.append(dele)
                lst.append(row)
        for cat in eqm.CATEGORIES:
            t = Gtk.Label(label=cat.upper(), xalign=0)
            t.add_css_class("ft-section-title")
            lst.append(t)
            for p in eqm.presets_for(cat):
                lst.append(self._menu_item(p.name, lambda g=list(p.gains): choose(g)))
        scroller.set_child(lst)
        outer.append(scroller)
        outer.append(Gtk.Separator())
        outer.append(self._menu_item("Save current EQ as preset…", lambda: (pop.popdown(), self._save_dialog()),
                                     icon="document-save-symbolic"))
        pop.set_child(outer)

    def _name_dialog(self, title, initial, on_ok):
        dlg = Gtk.Popover()
        dlg.set_parent(self.preset_button)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.append(Gtk.Label(label=title, xalign=0))
        entry = Gtk.Entry(text=initial)
        box.append(entry)
        btns = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        ok = Gtk.Button(label="Save")
        ok.add_css_class("suggested-action")
        btns.append(cancel)
        btns.append(ok)
        box.append(btns)
        dlg.set_child(box)

        def done(*_):
            name = entry.get_text().strip()
            dlg.popdown()
            if name:
                on_ok(name)

        cancel.connect("clicked", lambda *_: dlg.popdown())
        ok.connect("clicked", done)
        entry.connect("activate", done)
        dlg.connect("closed", lambda d: GLib.idle_add(lambda: (d.unparent(), False)[1]))
        dlg.popup()
        entry.grab_focus()

    def _save_dialog(self):
        def ok(name):
            self.engine.settings.create_user_preset(name, self._current())
            self._refresh_preset_label()
        self._name_dialog("Save current EQ as preset", "My Preset", ok)

    def _rename_dialog(self, preset):
        def ok(name):
            self.engine.settings.update_user_preset(preset.id, name)
            self._refresh_preset_label()
        self._name_dialog("Rename preset", preset.name, ok)

