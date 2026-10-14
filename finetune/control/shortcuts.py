"""Global shortcuts (port of ShortcutAction + ShortcutsRegistry).

Wayland does not let applications grab keys, so FineTune registers its
actions with the XDG GlobalShortcuts portal (KDE, GNOME 48+, Hyprland). On
compositors without the portal, bind the CLI instead, e.g. for Hyprland:

    bind = , XF86AudioRaiseVolume, exec, finetune --action volume-up
    bind = CTRL ALT, Up, exec, finetune --action frontmostAppVolumeUp

`finetune --action …` forwards to the running instance over D-Bus.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from gi.repository import Gio, GLib

log = logging.getLogger("finetune.shortcuts")


@dataclass(frozen=True)
class ShortcutAction:
    id: str
    display_name: str
    supports_repeat: bool
    default_trigger: str


ACTIONS = [
    ShortcutAction("togglePopup", "Toggle FineTune Popup", False, ""),
    ShortcutAction("frontmostAppVolumeUp", "App Volume Up", True, ""),
    ShortcutAction("frontmostAppVolumeDown", "App Volume Down", True, ""),
    ShortcutAction("frontmostAppMuteToggle", "App Mute", False, ""),
]
# Media-key actions on the default output device (bind XF86Audio* to these)
MEDIA_ACTIONS = [
    ShortcutAction("volume-up", "Output Volume Up", True, "XF86AudioRaiseVolume"),
    ShortcutAction("volume-down", "Output Volume Down", True, "XF86AudioLowerVolume"),
    ShortcutAction("volume-mute", "Output Mute", False, "XF86AudioMute"),
]
ALL_ACTIONS = ACTIONS + MEDIA_ACTIONS
ACTION_IDS = {a.id for a in ALL_ACTIONS}

REPEAT_INITIAL_MS = 450
REPEAT_INTERVAL_MS = 60

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
GS_IFACE = "org.freedesktop.portal.GlobalShortcuts"


class GlobalShortcutsPortal:
    """Minimal client for org.freedesktop.portal.GlobalShortcuts."""

    def __init__(self, app_id: str, dispatch):
        self.app_id = app_id
        self.dispatch = dispatch          # dispatch(action_id)
        self.status = "unavailable"       # unavailable | connecting | active | error:<msg>
        self._bus = None
        self._session = None
        self._repeat_id = 0
        self._repeat_action = None
        self._deactivation_seen = False
        self._subs = []

    def start(self, triggers: dict[str, str], media_keys: bool):
        try:
            # A private connection: the host app-id registry must be the first portal call on it,
            # and GTK/libadwaita already talk to the portal on the shared session connection.
            addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            self._bus = Gio.DBusConnection.new_for_address_sync(
                addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None)
        except GLib.Error as e:
            self.status = f"error:{e.message}"
            return
        self._triggers = triggers
        self._media_keys = media_keys
        # Host apps must register their app id first (portal ≥ 1.19); ignore failures.
        self._bus.call(PORTAL_BUS, PORTAL_PATH, "org.freedesktop.host.portal.Registry", "Register",
                       GLib.Variant("(sa{sv})", (self.app_id, {})), None, Gio.DBusCallFlags.NONE, 2000, None,
                       lambda b, r: self._after_register(b, r))

    def _after_register(self, bus, res):
        try:
            bus.call_finish(res)
        except GLib.Error:
            pass
        self.status = "connecting"
        token = f"finetune{random.randint(0, 1 << 30)}"
        self._await_response(token, self._on_session)
        self._bus.call(PORTAL_BUS, PORTAL_PATH, GS_IFACE, "CreateSession",
                       GLib.Variant("(a{sv})", ({"handle_token": GLib.Variant("s", token),
                                                 "session_handle_token": GLib.Variant("s", token + "s")},)),
                       GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 5000, None, self._on_call_done)

    def _on_call_done(self, bus, res):
        try:
            bus.call_finish(res)
        except GLib.Error as e:
            self.status = "unavailable" if "UnknownMethod" in e.message or "ServiceUnknown" in e.message \
                else f"error:{e.message}"
            log.info("GlobalShortcuts portal not available: %s", e.message)

    def _request_path(self, token):
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    def _await_response(self, token, cb):
        path = self._request_path(token)
        sub = None

        def on_signal(_bus, _sender, _path, _iface, _signal, params):
            code, results = params.unpack()
            self._bus.signal_unsubscribe(sub)
            cb(code, results)

        sub = self._bus.signal_subscribe(PORTAL_BUS, "org.freedesktop.portal.Request", "Response", path,
                                         None, Gio.DBusSignalFlags.NO_MATCH_RULE, on_signal)

    def _on_session(self, code, results):
        if code != 0:
            self.status = "error:session refused"
            return
        self._session = results.get("session_handle")
        for sig in ("Activated", "Deactivated"):
            self._subs.append(self._bus.signal_subscribe(
                PORTAL_BUS, GS_IFACE, sig, PORTAL_PATH, None, Gio.DBusSignalFlags.NONE, self._on_activation))
        self.bind()

    def bind(self, triggers: dict[str, str] | None = None):
        if triggers is not None:
            self._triggers = triggers
        if not self._session:
            return
        shortcuts = []
        actions = ACTIONS + (MEDIA_ACTIONS if self._media_keys else [])
        for a in actions:
            opts = {"description": GLib.Variant("s", a.display_name)}
            trig = self._triggers.get(a.id) or a.default_trigger
            if trig:
                opts["preferred_trigger"] = GLib.Variant("s", trig)
            shortcuts.append((a.id, opts))
        token = f"finetuneb{random.randint(0, 1 << 30)}"
        self._await_response(token, self._on_bound)
        self._bus.call(PORTAL_BUS, PORTAL_PATH, GS_IFACE, "BindShortcuts",
                       GLib.Variant("(oa(sa{sv})sa{sv})", (self._session, shortcuts, "",
                                                           {"handle_token": GLib.Variant("s", token)})),
                       GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 60000, None, self._on_call_done)

    def _on_bound(self, code, results):
        self.status = "active" if code == 0 else "error:binding refused"
        log.info("GlobalShortcuts portal: %s", self.status)

    def _on_activation(self, _bus, _sender, _path, _iface, signal, params):
        vals = params.unpack()
        session, action = vals[0], vals[1]
        if session != self._session:
            return
        if signal == "Deactivated":
            self._deactivation_seen = True
            self._stop_repeat()
            return
        self.dispatch(action)
        spec = next((a for a in ALL_ACTIONS if a.id == action), None)
        # Only emulate key-repeat when the portal proves it reports key release.
        if spec and spec.supports_repeat and self._deactivation_seen:
            self._start_repeat(action)

    def _start_repeat(self, action):
        self._stop_repeat()
        self._repeat_action = action

        def tick():
            self.dispatch(action)
            return True

        def first():
            self._repeat_id = GLib.timeout_add(REPEAT_INTERVAL_MS, tick)
            return False

        self._repeat_id = GLib.timeout_add(REPEAT_INITIAL_MS, first)

    def _stop_repeat(self):
        if self._repeat_id:
            GLib.source_remove(self._repeat_id)
            self._repeat_id = 0
