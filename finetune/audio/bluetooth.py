"""Paired Bluetooth audio devices through BlueZ D-Bus (port of BluetoothDeviceMonitor)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from gi.repository import Gio, GLib

log = logging.getLogger("finetune.bluetooth")

AUDIO_UUIDS = {
    "0000110b-0000-1000-8000-00805f9b34fb",  # A2DP sink
    "0000110d-0000-1000-8000-00805f9b34fb",  # A2DP
    "0000111e-0000-1000-8000-00805f9b34fb",  # HFP
    "00001108-0000-1000-8000-00805f9b34fb",  # HSP
    "0000184e-0000-1000-8000-00805f9b34fb",  # LE Audio ASCS
}
CONNECT_TIMEOUT_S = 20


@dataclass
class PairedBluetoothDevice:
    path: str
    address: str
    name: str
    icon_name: str
    connected: bool
    connecting: bool = False
    error: str | None = None


def _icon_for(name: str, bluez_icon: str | None) -> str:
    n = name.lower()
    if any(k in n for k in ("buds", "airpods", "earbud", "wf-")):
        return "ft-earbuds-symbolic"
    if bluez_icon == "audio-headset" or "headset" in n:
        return "audio-headset-symbolic"
    if bluez_icon in ("audio-headphones",) or any(k in n for k in ("wh-", "headphone", "qc", "acton", "momentum")):
        return "audio-headphones-symbolic"
    if bluez_icon == "audio-card" or "speaker" in n:
        return "ft-hifispeaker-symbolic"
    return "bluetooth-symbolic"


class BluetoothMonitor:
    def __init__(self):
        self.devices: dict[str, PairedBluetoothDevice] = {}
        self._manager = None
        self._listeners = []
        self._timeouts: dict[str, int] = {}

    def connect_changed(self, cb):
        self._listeners.append(cb)

    def _emit(self):
        for cb in list(self._listeners):
            cb()

    def start(self):
        Gio.DBusObjectManagerClient.new_for_bus(
            Gio.BusType.SYSTEM, Gio.DBusObjectManagerClientFlags.NONE, "org.bluez", "/",
            None, None, None, self._on_manager)

    def _on_manager(self, _src, res):
        try:
            self._manager = Gio.DBusObjectManagerClient.new_for_bus_finish(res)
        except GLib.Error as e:
            log.info("BlueZ not available: %s", e.message)
            return
        self._manager.connect("object-added", lambda *_: self.refresh())
        self._manager.connect("object-removed", lambda *_: self.refresh())
        self._manager.connect("interface-proxy-properties-changed", lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        if not self._manager:
            return
        found = {}
        for obj in self._manager.get_objects():
            iface = obj.get_interface("org.bluez.Device1")
            if iface is None:
                continue

            def prop(name):
                v = iface.get_cached_property(name)
                return v.unpack() if v is not None else None

            if not prop("Paired"):
                continue
            uuids = {u.lower() for u in (prop("UUIDs") or [])}
            if not uuids & AUDIO_UUIDS:
                continue
            path = obj.get_object_path()
            name = prop("Alias") or prop("Name") or prop("Address") or "Bluetooth device"
            prev = self.devices.get(path)
            dev = PairedBluetoothDevice(path, prop("Address") or "", name, _icon_for(name, prop("Icon")),
                                        bool(prop("Connected")))
            if prev and prev.connecting and not dev.connected:
                dev.connecting = True
            dev.error = prev.error if prev else None
            found[path] = dev
        self.devices = found
        self._emit()

    def paired_disconnected(self) -> list[PairedBluetoothDevice]:
        return sorted((d for d in self.devices.values() if not d.connected), key=lambda d: d.name.lower())

    def connect_device(self, dev: PairedBluetoothDevice):
        if not self._manager:
            return
        obj = self._manager.get_object(dev.path)
        iface = obj.get_interface("org.bluez.Device1") if obj else None
        if iface is None:
            return
        dev.connecting = True
        dev.error = None
        self._emit()

        def timeout():
            self._timeouts.pop(dev.path, None)
            if dev.connecting:
                self._finish(dev, "Timed out")
            return False

        self._timeouts[dev.path] = GLib.timeout_add_seconds(CONNECT_TIMEOUT_S, timeout)

        def done(proxy, res):
            try:
                proxy.call_finish(res)
                self._finish(dev, None)
            except GLib.Error as e:
                self._finish(dev, e.message.split(":")[-1].strip() or "Failed")

        iface.call("Connect", None, Gio.DBusCallFlags.NONE, CONNECT_TIMEOUT_S * 1000, None, done)

    def _finish(self, dev, error):
        dev.connecting = False
        dev.error = error
        tid = self._timeouts.pop(dev.path, None)
        if tid:
            GLib.source_remove(tid)
        if error:
            def clear():
                dev.error = None
                self._emit()
                return False
            GLib.timeout_add_seconds(4, clear)
        self.refresh()
        self._emit()
