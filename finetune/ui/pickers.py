"""Popovers: output device picker (single/multi), AutoEQ search panel, device icon picker."""
from __future__ import annotations

from gi.repository import Gio, GLib, Gtk

from finetune.audio.devices import ICON_CATALOG, search_icons
from finetune.settings import DEVICE_SELECTION_MULTI, DEVICE_SELECTION_SINGLE
from finetune.ui.widgets import clear_box


def _row_button(child, selected=False, sensitive=True):
    b = Gtk.Button()
    b.add_css_class("flat")
    b.add_css_class("ft-picker-row")
    if selected:
        b.add_css_class("ft-selected")
    b.set_child(child)
    b.set_sensitive(sensitive)
    return b


class DevicePickerPopover(Gtk.Popover):
    """Single: route to one device or "System Audio" (follow default). Multi: any set of devices."""

    def __init__(self, engine, ident: str, on_changed=None):
        super().__init__()
        self.engine = engine
        self.ident = ident
        self.on_changed = on_changed
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for m in ("top", "bottom", "start", "end"):
            getattr(self.box, f"set_margin_{m}")(6)
        self.box.set_size_request(300, -1)
        self.set_child(self.box)
        self.connect("show", lambda *_: self.rebuild())

    def rebuild(self):
        clear_box(self.box)
        e, ident = self.engine, self.ident
        mode = e.get_device_selection_mode(ident)

        seg = Gtk.Box(homogeneous=True)
        seg.add_css_class("ft-segmented")
        single = Gtk.ToggleButton(label="Single", active=mode == DEVICE_SELECTION_SINGLE)
        multi = Gtk.ToggleButton(label="Multi", active=mode == DEVICE_SELECTION_MULTI, group=single)
        single.connect("toggled", lambda b: b.get_active() and self._set_mode(DEVICE_SELECTION_SINGLE))
        multi.connect("toggled", lambda b: b.get_active() and self._set_mode(DEVICE_SELECTION_MULTI))
        seg.append(single)
        seg.append(multi)
        self.box.append(seg)
        self.box.append(Gtk.Separator())

        default_uid = e.default_output_uid()
        following = e.is_following_default(ident)
        current = set(e.current_outputs(ident))

        # System Audio (follow default)
        sa = Gtk.Box(spacing=8)
        if mode == DEVICE_SELECTION_MULTI:
            sa.append(Gtk.CheckButton(sensitive=False))
        sa.append(Gtk.Image.new_from_icon_name("ft-globe-symbolic"))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        texts.append(Gtk.Label(label="System Audio", xalign=0))
        sub = Gtk.Label(xalign=0)
        sub.add_css_class("ft-subtitle")
        if mode == DEVICE_SELECTION_MULTI:
            sub.set_text("Not available in multi mode")
        else:
            dd = e.default_output()
            sub.set_text(f"Follows default · {dd.name}" if dd else "Follows the default output")
        texts.append(sub)
        sa.append(texts)
        if mode == DEVICE_SELECTION_SINGLE and following:
            sa.append(Gtk.Image.new_from_icon_name("object-select-symbolic"))
        b = _row_button(sa, selected=mode == DEVICE_SELECTION_SINGLE and following,
                        sensitive=mode == DEVICE_SELECTION_SINGLE)
        b.connect("clicked", lambda *_: self._choose(None))
        self.box.append(b)

        selected = e.get_selected_device_uids(ident)
        for dev in e.outputs:
            if dev.uid in e.settings.hidden_output_device_uids and dev.uid not in current:
                continue
            row = Gtk.Box(spacing=8)
            if mode == DEVICE_SELECTION_MULTI:
                cb = Gtk.CheckButton(active=dev.uid in selected)
                cb.set_can_target(False)
                row.append(cb)
            row.append(Gtk.Image.new_from_icon_name(e.settings.get_device_icon_override(dev.uid) or dev.icon_name))
            row.append(Gtk.Label(label=dev.name, xalign=0, hexpand=True, ellipsize=3))
            if dev.uid == default_uid:
                star = Gtk.Image.new_from_icon_name("starred-symbolic")
                star.set_tooltip_text("System default")
                star.set_opacity(0.6)
                row.append(star)
            is_sel = (mode == DEVICE_SELECTION_SINGLE and not following and dev.uid in current)
            if is_sel:
                row.append(Gtk.Image.new_from_icon_name("object-select-symbolic"))
            btn = _row_button(row, selected=is_sel or (mode == DEVICE_SELECTION_MULTI and dev.uid in selected))
            if mode == DEVICE_SELECTION_MULTI:
                btn.connect("clicked", lambda _b, u=dev.uid: self._toggle(u))
            else:
                btn.connect("clicked", lambda _b, u=dev.uid: self._choose(u))
            self.box.append(btn)

    def _set_mode(self, mode):
        if mode != self.engine.get_device_selection_mode(self.ident):
            self.engine.set_device_selection_mode(self.ident, mode)
            GLib.idle_add(lambda: (self.rebuild(), False)[1])
            self._changed()

    def _choose(self, uid):
        self.engine.set_device(self.ident, uid)
        self.popdown()
        self._changed()

    def _toggle(self, uid):
        sel = self.engine.get_selected_device_uids(self.ident)
        if uid in sel:
            if len(sel) <= 1:
                return  # keep at least one device
            sel.discard(uid)
        else:
            sel.add(uid)
        self.engine.set_selected_device_uids(self.ident, sel)
        self.rebuild()
        self._changed()

    def _changed(self):
        if self.on_changed:
            self.on_changed()


class AutoEQPopover(Gtk.Popover):
    """Search thousands of AutoEQ headphone profiles, favorites, import ParametricEQ.txt."""

    def __init__(self, engine, device_uid: str):
        super().__init__()
        self.engine = engine
        self.uid = device_uid
        self.manager = engine.autoeq
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for m in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{m}")(6)
        outer.set_size_request(360, 440)
        self.search = Gtk.SearchEntry(placeholder_text="Search headphones…")
        self.search.connect("search-changed", lambda *_: self.rebuild())
        outer.append(self.search)
        self.status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.append(self.status)
        sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sc.set_child(self.list)
        outer.append(sc)
        outer.append(Gtk.Separator())
        imp = Gtk.Button()
        imp.add_css_class("flat")
        ib = Gtk.Box(spacing=8)
        ib.append(Gtk.Image.new_from_icon_name("folder-download-symbolic"))
        ib.append(Gtk.Label(label="Import ParametricEQ.txt…", xalign=0))
        imp.set_child(ib)
        imp.connect("clicked", lambda *_: self._import())
        outer.append(imp)
        self.set_child(outer)
        self._loading_profile = None
        self.connect("show", lambda *_: (self.rebuild(), self.search.grab_focus()))
        self.manager.connect_changed(lambda: self.get_mapped() and self.rebuild())

    def _entry_row(self, entry, selected=False, show_star=True):
        box = Gtk.Box(spacing=8)
        t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        name = Gtk.Label(label=entry.name, xalign=0, ellipsize=3)
        if selected:
            name.add_css_class("heading")
        t.append(name)
        if entry.measured_by:
            s = Gtk.Label(label=entry.measured_by, xalign=0)
            s.add_css_class("ft-subtitle")
            t.append(s)
        box.append(t)
        if self._loading_profile == entry.id:
            box.append(Gtk.Spinner(spinning=True))
        if show_star:
            fav = self.engine.settings.is_auto_eq_favorite(entry.id)
            star = Gtk.Button(icon_name="starred-symbolic" if fav else "non-starred-symbolic")
            star.add_css_class("flat")
            star.add_css_class("ft-icon-button")
            star.set_tooltip_text("Unfavorite" if fav else "Favorite")
            if not fav:
                star.set_opacity(0.3)
            star.connect("clicked", lambda _b, i=entry.id, f=fav: self._toggle_fav(i, f))
            box.append(star)
        if entry.measured_by == "Imported":
            dele = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete imported profile")
            dele.add_css_class("flat")
            dele.add_css_class("ft-icon-button")
            dele.connect("clicked", lambda _b, i=entry.id: self._delete(i))
            box.append(dele)
        if selected:
            box.append(Gtk.Image.new_from_icon_name("object-select-symbolic"))
        btn = _row_button(box, selected=selected)
        btn.connect("clicked", lambda *_: self._select(entry))
        return btn

    def _section(self, title):
        l = Gtk.Label(label=title, xalign=0)
        l.add_css_class("ft-section-title")
        return l

    def rebuild(self):
        clear_box(self.list)
        clear_box(self.status)
        e = self.engine
        sel = e.get_auto_eq_selection(self.uid)
        state = self.manager.catalog_state
        if state == "loading" and not self.manager.catalog:
            box = Gtk.Box(spacing=8)
            box.append(Gtk.Spinner(spinning=True))
            box.append(Gtk.Label(label="Loading headphone catalog…"))
            self.status.append(box)
        elif state.startswith("error:"):
            box = Gtk.Box(spacing=8)
            lbl = Gtk.Label(label=state[6:], xalign=0, wrap=True, hexpand=True)
            lbl.add_css_class("ft-error-text")
            box.append(lbl)
            retry = Gtk.Button(label="Retry")
            retry.connect("clicked", lambda *_: self.manager.refresh_catalog())
            box.append(retry)
            self.status.append(box)

        # No correction
        nb = Gtk.Box(spacing=8)
        nb.append(Gtk.Image.new_from_icon_name("window-close-symbolic"))
        nl = Gtk.Label(label="No correction", xalign=0, hexpand=True)
        nl.add_css_class("heading")
        nb.append(nl)
        if sel is None:
            nb.append(Gtk.Image.new_from_icon_name("object-select-symbolic"))
        none_btn = _row_button(nb, selected=sel is None)
        none_btn.connect("clicked", lambda *_: self._clear())
        self.list.append(none_btn)

        if sel:
            entry = self.manager.entry_for_display(sel.profile_id)
            if entry:
                self.list.append(self._entry_row(entry, selected=True))
            sw = Gtk.Box(spacing=8)
            sw.set_margin_start(8)
            sw.append(Gtk.Label(label="Correction", xalign=0, hexpand=True))
            s = Gtk.Switch(active=sel.is_enabled, valign=Gtk.Align.CENTER)
            s.connect("notify::active", lambda w, *_: e.set_auto_eq_enabled(self.uid, w.get_active()))
            sw.append(s)
            self.list.append(sw)
            pa = Gtk.Box(spacing=8)
            pa.set_margin_start(8)
            pre_on = e.settings.auto_eq_preamp_enabled
            pa.append(Gtk.Label(label="Preamp on (quieter, no clipping)" if pre_on else "Preamp off (louder, limiter catches peaks)",
                                xalign=0, hexpand=True))
            ps = Gtk.Switch(active=pre_on, valign=Gtk.Align.CENTER)
            ps.connect("notify::active", lambda w, *_: (e.set_auto_eq_preamp_enabled(w.get_active()),
                                                         GLib.idle_add(lambda: (self.rebuild(), False)[1])))
            pa.append(ps)
            self.list.append(pa)

        q = self.search.get_text().strip()
        current_id = sel.profile_id if sel else None
        if q:
            res = self.manager.search(q)
            if res.entries:
                shown, total = len(res.entries), res.total_count
                self.list.append(self._section(f"Showing {shown} of {total} results" if total > shown
                                               else f"{total} results"))
                favs = self.engine.settings.favorite_auto_eq_profiles
                ordered = sorted(res.entries, key=lambda x: x.id not in favs)
                for entry in ordered:
                    if entry.id != current_id:
                        self.list.append(self._entry_row(entry))
            else:
                l = Gtk.Label(label="No profiles found")
                l.add_css_class("dim-label")
                self.list.append(l)
        else:
            favs = [self.manager.entry_for_display(f) for f in sorted(self.engine.settings.favorite_auto_eq_profiles)]
            favs = [f for f in favs if f and f.id != current_id]
            if favs:
                self.list.append(self._section("FAVORITES"))
                for f in sorted(favs, key=lambda x: x.name.lower()):
                    self.list.append(self._entry_row(f))
            imported = [p for p in self.manager.profiles.values() if p.source == "imported" and p.id != current_id]
            if imported:
                self.list.append(self._section("IMPORTED"))
                for p in sorted(imported, key=lambda x: x.name.lower()):
                    self.list.append(self._entry_row(self.manager.entry_for_display(p.id)))
            if not favs and not imported:
                hint = Gtk.Label(label="Search or pick a favorite below" if self.manager.catalog else "Type to search headphones")
                hint.add_css_class("dim-label")
                hint.set_margin_top(12)
                self.list.append(hint)

    def _toggle_fav(self, pid, fav):
        if fav:
            self.engine.settings.unfavorite_auto_eq_profile(pid)
        else:
            self.engine.settings.favorite_auto_eq_profile(pid)
        self.rebuild()

    def _select(self, entry):
        self._loading_profile = entry.id
        self.rebuild()

        def done(prof):
            self._loading_profile = None
            if prof:
                self.engine.set_auto_eq_profile(self.uid, prof.id)
            self.rebuild()

        if entry.measured_by == "Imported" or entry.id in self.manager.profiles:
            done(self.manager.profile(entry.id))
        else:
            self.manager.resolve_entry(entry, done)

    def _clear(self):
        self.engine.set_auto_eq_profile(self.uid, None)
        self.rebuild()

    def _delete(self, pid):
        sel = self.engine.get_auto_eq_selection(self.uid)
        if sel and sel.profile_id == pid:
            self.engine.set_auto_eq_profile(self.uid, None)
        self.engine.settings.unfavorite_auto_eq_profile(pid)
        self.manager.delete_imported_profile(pid)
        self.rebuild()

    def _import(self):
        dlg = Gtk.FileDialog(title="Import ParametricEQ.txt")
        filt = Gtk.FileFilter()
        filt.set_name("EqualizerAPO ParametricEQ (*.txt)")
        filt.add_pattern("*.txt")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dlg.set_filters(store)
        root = self.get_root()
        self.popdown()

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            prof = self.manager.import_profile(f.get_path())
            if prof:
                self.engine.set_auto_eq_profile(self.uid, prof.id)
            else:
                self.engine._notify("Import failed", "No valid filters found in that file.")

        dlg.open(root if isinstance(root, Gtk.Window) else None, None, done)


class IconPickerPopover(Gtk.Popover):
    def __init__(self, engine, device_uid: str, on_changed=None):
        super().__init__()
        self.engine = engine
        self.uid = device_uid
        self.on_changed = on_changed
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for m in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{m}")(6)
        outer.set_size_request(300, 340)
        self.search = Gtk.SearchEntry(placeholder_text="Search icons…")
        self.search.connect("search-changed", lambda *_: self.rebuild())
        outer.append(self.search)
        sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sc.set_child(self.content)
        outer.append(sc)
        restore = Gtk.Button(label="Restore Default")
        restore.add_css_class("flat")
        restore.connect("clicked", lambda *_: self._pick(None))
        outer.append(restore)
        self.set_child(outer)
        self.connect("show", lambda *_: self.rebuild())

    def _grid(self, icons, current):
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=7, min_children_per_line=7,
                           homogeneous=True)
        for icon in icons:
            b = Gtk.Button(icon_name=icon, tooltip_text=icon.replace("-symbolic", "").replace("ft-", ""))
            b.add_css_class("flat")
            b.add_css_class("ft-icon-tile")
            if icon == current:
                b.add_css_class("ft-selected")
            b.connect("clicked", lambda _b, i=icon: self._pick(i))
            flow.append(b)
        return flow

    def rebuild(self):
        clear_box(self.content)
        current = self.engine.settings.get_device_icon_override(self.uid)
        q = self.search.get_text().strip()
        if q:
            icons = search_icons(q)
            if icons:
                self.content.append(self._grid(icons, current))
            else:
                l = Gtk.Label(label="No matching icons")
                l.add_css_class("dim-label")
                self.content.append(l)
            return
        for cat, entries in ICON_CATALOG:
            t = Gtk.Label(label=cat.upper(), xalign=0)
            t.add_css_class("ft-section-title")
            self.content.append(t)
            self.content.append(self._grid([i for i, _ in entries], current))

    def _pick(self, icon):
        self.engine.settings.set_device_icon_override(self.uid, icon)
        self.popdown()
        if self.on_changed:
            self.on_changed()
