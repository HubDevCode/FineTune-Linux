"""System tray icon via the StatusNotifierItem D-Bus protocol (Waybar, KDE, swaybar, GNOME+AppIndicator).

Port of MenuBarIconCoordinator/MenuBarIconState: five icon styles (Default,
Speaker — live volume glyph, Device — default output glyph, Waveform,
Equalizer) and a brief flash of the new output's icon on device switches.
Left click toggles the popup, middle click toggles mute on the default
output, scroll changes its volume, right click opens a small menu.
"""
from __future__ import annotations

import logging
import os
import re

from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from finetune import APP_NAME, paths

log = logging.getLogger("finetune.tray")

SNI_IFACE = "org.kde.StatusNotifierItem"
WATCHER = "org.kde.StatusNotifierWatcher"
MENU_IFACE = "com.canonical.dbusmenu"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
FLASH_MS = 1500

SNI_XML = f"""
<node>
 <interface name="{SNI_IFACE}">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="i" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
  <signal name="NewTitle"/>
  <signal name="NewIcon"/>
  <signal name="NewToolTip"/>
  <signal name="NewStatus"><arg name="status" type="s"/></signal>
 </interface>
</node>"""

MENU_XML = f"""
<node>
 <interface name="{MENU_IFACE}">
  <property name="Version" type="u" access="read"/>
  <property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconThemePath" type="as" access="read"/>
  <method name="GetLayout">
   <arg type="i" name="parentId" direction="in"/><arg type="i" name="recursionDepth" direction="in"/>
   <arg type="as" name="propertyNames" direction="in"/>
   <arg type="u" name="revision" direction="out"/><arg type="(ia{{sv}}av)" name="layout" direction="out"/>
  </method>
  <method name="GetGroupProperties">
   <arg type="ai" name="ids" direction="in"/><arg type="as" name="propertyNames" direction="in"/>
   <arg type="a(ia{{sv}})" name="properties" direction="out"/>
  </method>
  <method name="GetProperty">
   <arg type="i" name="id" direction="in"/><arg type="s" name="name" direction="in"/>
   <arg type="v" name="value" direction="out"/>
  </method>
  <method name="Event">
   <arg type="i" name="id" direction="in"/><arg type="s" name="eventId" direction="in"/>
   <arg type="v" name="data" direction="in"/><arg type="u" name="timestamp" direction="in"/>
  </method>
  <method name="EventGroup">
   <arg type="a(isvu)" name="events" direction="in"/><arg type="ai" name="idErrors" direction="out"/>
  </method>
  <method name="AboutToShow"><arg type="i" name="id" direction="in"/><arg type="b" name="needUpdate" direction="out"/></method>
  <method name="AboutToShowGroup">
   <arg type="ai" name="ids" direction="in"/><arg type="ai" name="updatesNeeded" direction="out"/>
   <arg type="ai" name="idErrors" direction="out"/>
  </method>
  <signal name="ItemsPropertiesUpdated">
   <arg type="a(ia{{sv}})" name="updatedProps"/><arg type="a(ias)" name="removedProps"/>
  </signal>
  <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
 </interface>
</node>"""


def _recolor_svg(text: str, color: str) -> str:
    text = re.sub(r'fill="#[0-9a-fA-F]{3,8}"', f'fill="{color}"', text)
    text = re.sub(r"fill:#[0-9a-fA-F]{3,8}", f"fill:{color}", text)
    text = re.sub(r'stroke="#[0-9a-fA-F]{3,8}"', f'stroke="{color}"', text)
    if "fill=" not in text and "fill:" not in text:
        text = text.replace("<svg", f'<svg fill="{color}"', 1)
    return text


def _pixmaps_for_icon(icon_name: str, color: str):
    """Render an icon (our own or from the theme) into SNI ARGB32 pixmaps at a few sizes."""
    path = None
    for d in (paths.resource_dir() / "icons/hicolor/symbolic/apps",):
        p = d / f"{icon_name}.svg"
        if p.exists():
            path = str(p)
    if path is None:
        theme = Gtk.IconTheme.get_for_display(Gtk.Window().get_display()) if Gtk.is_initialized() else None
        if theme is not None:
            paint = theme.lookup_icon(icon_name, None, 32, 1, Gtk.TextDirection.NONE, 0)
            f = paint.get_file() if paint else None
            path = f.get_path() if f else None
    if not path:
        return []
    try:
        with open(path) as fh:
            svg = _recolor_svg(fh.read(), color)
    except OSError:
        return []
    out = []
    for size in (16, 22, 24, 32, 48):
        try:
            loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
            loader.set_size(size, size)
            loader.write(svg.encode())
            loader.close()
            pb = loader.get_pixbuf()
        except GLib.Error:
            continue
        if pb is None:
            continue
        if not pb.get_has_alpha():
            pb = pb.add_alpha(False, 0, 0, 0)
        w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
        px = pb.get_pixels()
        argb = bytearray(w * h * 4)
        for y in range(h):
            for x in range(w):
                i = y * stride + x * 4
                o = (y * w + x) * 4
                r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
                argb[o:o + 4] = bytes((a, r, g, b))
        out.append((w, h, bytes(argb)))
    return out


class TrayIcon:
    def __init__(self, app, engine):
        self.app = app
        self.engine = engine
        self._bus = None
        self._reg_ids = []
        self._icon_name = "finetune-symbolic"
        self._pixmaps = []
        self._tooltip = APP_NAME
        self._flash_until = 0
        self._last_default = None
        self._menu_revision = 1
        self._watcher_id = 0
        self._own_id = 0
        self.registered = False
        self._color = "#f0f0f0"

    # ------------------------------------------------------------- startup

    def start(self):
        self._service = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._own_id = Gio.bus_own_name(Gio.BusType.SESSION, self._service, Gio.BusNameOwnerFlags.NONE,
                                        self._on_bus_acquired, None, None)
        self.engine.connect_changed(self.update)
        self.engine.settings.connect_app_settings_changed(lambda o, n: self.update(force=True))

    def _on_bus_acquired(self, bus, _name):
        self._bus = bus
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._reg_ids.append(bus.register_object(ITEM_PATH, sni, self._on_sni_call, self._on_sni_prop, None))
        self._reg_ids.append(bus.register_object(MENU_PATH, menu, self._on_menu_call, self._on_menu_prop, None))
        self.update(force=True)
        self._watcher_id = Gio.bus_watch_name_on_connection(
            bus, WATCHER, Gio.BusNameWatcherFlags.NONE, self._register_with_watcher, self._on_watcher_lost)

    def _register_with_watcher(self, bus, *_):
        bus.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                 GLib.Variant("(s)", (self._service,)), None, Gio.DBusCallFlags.NONE, -1, None, self._on_registered)

    def _on_registered(self, bus, res):
        try:
            bus.call_finish(res)
            self.registered = True
            log.info("Tray icon registered")
        except GLib.Error as e:
            log.warning("StatusNotifierWatcher registration failed: %s", e.message)

    def _on_watcher_lost(self, *_):
        self.registered = False

    def stop(self):
        if self._bus:
            for rid in self._reg_ids:
                self._bus.unregister_object(rid)
        if self._own_id:
            Gio.bus_unown_name(self._own_id)

    # ------------------------------------------------------------- icon state

    def _style_icon(self) -> str:
        e = self.engine
        style = e.settings.app_settings.menu_bar_icon_style
        dev = e.default_output()
        if style == "Speaker":
            frac = e.device_volume(dev) if dev else 0
            muted = e.device_muted(dev) if dev else False
            from finetune.ui.widgets import speaker_icon
            return speaker_icon(frac, muted)
        if style == "Device":
            if dev:
                return e.settings.get_device_icon_override(dev.uid) or dev.icon_name
            return "audio-speakers-symbolic"
        if style == "Waveform":
            return "ft-waveform-symbolic"
        if style == "Equalizer":
            return "ft-eq-symbolic"
        return "finetune-symbolic"

    def update(self, force=False):
        e = self.engine
        dev = e.default_output()
        now = GLib.get_monotonic_time() // 1000
        uid = dev.uid if dev else None
        if self._last_default is not None and uid != self._last_default and dev is not None:
            # flash the new output's glyph on device switch
            self._flash_until = now + FLASH_MS
            GLib.timeout_add(FLASH_MS + 50, lambda: (self.update(), False)[1])
        self._last_default = uid
        if now < self._flash_until and dev is not None:
            icon = e.settings.get_device_icon_override(dev.uid) or dev.icon_name
        else:
            icon = self._style_icon()
        if dev:
            vol = int(round(e.device_volume(dev) * 100))
            tip = f"{dev.name} — {'Muted' if e.device_muted(dev) else f'{vol}%'}"
        else:
            tip = APP_NAME
        style_mgr_dark = True
        try:
            from gi.repository import Adw
            style_mgr_dark = Adw.StyleManager.get_default().get_dark()
        except (ImportError, ValueError):
            pass
        color = "#f2f2f2" if style_mgr_dark else "#2e3436"
        if force or icon != self._icon_name or color != self._color:
            self._icon_name = icon
            self._color = color
            self._pixmaps = _pixmaps_for_icon(icon, color)
            self._emit("NewIcon")
        if tip != self._tooltip:
            self._tooltip = tip
            self._emit("NewToolTip")

    def _emit(self, signal):
        if self._bus:
            try:
                self._bus.emit_signal(None, ITEM_PATH, SNI_IFACE, signal, None)
            except GLib.Error:
                pass

    # ------------------------------------------------------------- SNI

    def _on_sni_prop(self, _conn, _sender, _path, _iface, prop):
        icon_path = str(paths.resource_dir() / "icons/hicolor/symbolic/apps")
        v = {
            "Category": GLib.Variant("s", "Hardware"),
            "Id": GLib.Variant("s", "finetune"),
            "Title": GLib.Variant("s", APP_NAME),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", "" if self._pixmaps else self._icon_name),
            "IconPixmap": GLib.Variant("a(iiay)", self._pixmaps),
            "IconThemePath": GLib.Variant("s", icon_path),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], APP_NAME, self._tooltip)),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return v.get(prop)

    def _on_sni_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method in ("Activate", "SecondaryActivate"):
            if method == "Activate":
                self.app.toggle_popup()
            else:
                dev = self.engine.default_output()
                if dev:
                    self.engine.toggle_device_mute(dev)
                    self.app.show_device_hud(dev)
        elif method == "ContextMenu":
            self.app.show_settings()
        elif method == "Scroll":
            delta, orientation = params.unpack()
            if orientation.lower() == "vertical" and delta:
                self.app.media_action("volume-up" if delta < 0 else "volume-down")
        invocation.return_value(None)

    # ------------------------------------------------------------- dbusmenu

    def _menu_items(self):
        return [
            (1, "Open FineTune", "standard", True),
            (2, "Settings…", "standard", True),
            (3, "", "separator", True),
            (4, "Mute Output", "checkmark", True),
            (5, "", "separator", True),
            (6, "Quit FineTune", "standard", True),
        ]

    def _item_props(self, iid, label, kind):
        props = {"visible": GLib.Variant("b", True), "enabled": GLib.Variant("b", True)}
        if kind == "separator":
            props["type"] = GLib.Variant("s", "separator")
        else:
            props["label"] = GLib.Variant("s", label)
        if kind == "checkmark":
            dev = self.engine.default_output()
            props["toggle-type"] = GLib.Variant("s", "checkmark")
            props["toggle-state"] = GLib.Variant("i", 1 if dev and self.engine.device_muted(dev) else 0)
        return props

    def _on_menu_prop(self, _conn, _sender, _path, _iface, prop):
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }.get(prop)

    def _on_menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "GetLayout":
            children = [GLib.Variant("(ia{sv}av)", (iid, self._item_props(iid, label, kind), []))
                        for iid, label, kind, _ in self._menu_items()]
            root = (0, {"children-display": GLib.Variant("s", "submenu")}, children)
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self._menu_revision, root)))
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            out = [(iid, self._item_props(iid, label, kind)) for iid, label, kind, _ in self._menu_items()
                   if not ids or iid in ids]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method == "GetProperty":
            iid, name = params.unpack()
            for i, label, kind, _ in self._menu_items():
                if i == iid:
                    v = self._item_props(i, label, kind).get(name)
                    if v is not None:
                        invocation.return_value(GLib.Variant("(v)", (v,)))
                        return
            invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
        elif method == "Event":
            iid, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                GLib.idle_add(lambda: (self._menu_clicked(iid), False)[1])
            invocation.return_value(None)
        elif method == "EventGroup":
            events = params.unpack()[0]
            for iid, event_id, _d, _t in events:
                if event_id == "clicked":
                    GLib.idle_add(lambda i=iid: (self._menu_clicked(i), False)[1])
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (True,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def _menu_clicked(self, iid):
        if iid == 1:
            self.app.toggle_popup(force_open=True)
        elif iid == 2:
            self.app.show_settings()
        elif iid == 4:
            dev = self.engine.default_output()
            if dev:
                self.engine.toggle_device_mute(dev)
            self._menu_revision += 1
            if self._bus:
                self._bus.emit_signal(None, MENU_PATH, MENU_IFACE, "LayoutUpdated",
                                      GLib.Variant("(ui)", (self._menu_revision, 0)))
        elif iid == 6:
            self.app.quit_app()
