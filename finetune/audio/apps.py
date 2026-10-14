"""Audio apps: grouping PipeWire playback streams into apps with a stable identity.

Port of AudioProcessMonitor/ProcessNameLookup. On macOS the persistence
identifier is the bundle ID; on Linux it is (in order of preference) the
Flatpak/portal app id, the matching .desktop file id, the process binary, or
"name:<application.name>".
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from gi.repository import Gio

from finetune.audio.devices import is_engine_node

log = logging.getLogger("finetune.apps")

# Streams that represent system sounds (notification/alert sounds via libcanberra).
EVENT_ROLES = {"event", "notification", "alert"}
SYSTEM_BINARIES = {"pipewire", "wireplumber", "pipewire-pulse", "pulseaudio"}
OWN_APP_NAMES = {"FineTune", "FineTune Engine", "fttestrec"}


@dataclass
class AudioApp:
    identifier: str              # persistence identifier
    name: str
    bundle_id: str | None        # desktop app id (≈ macOS bundle ID)
    binary: str | None
    icon: object = None          # Gio.Icon
    stream_indices: list = field(default_factory=list)
    pids: set = field(default_factory=set)
    corked: bool = True
    sink_indices: set = field(default_factory=set)

    @property
    def persistence_identifier(self) -> str:
        return self.identifier

    @property
    def is_playing(self) -> bool:
        return bool(self.stream_indices) and not self.corked

    def matches(self, query: str) -> bool:
        q = query.lower()
        cands = [self.identifier, self.bundle_id, self.binary, self.name]
        return any(c and c.lower() == q for c in cands)


class DesktopIndex:
    """Maps binaries / names / ids to installed .desktop applications."""

    def __init__(self):
        self._by_key: dict[str, Gio.DesktopAppInfo] = {}
        self.reload()

    def reload(self):
        self._by_key.clear()
        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            did = (info.get_id() or "").removesuffix(".desktop")
            keys = {did.lower(), did.split(".")[-1].lower()}
            exe = info.get_executable() or ""
            if exe:
                base = os.path.basename(exe).lower()
                keys.add(base)
                for suffix in ("-stable", "-bin", "-browser", ".sh", "-wrapper"):
                    keys.add(base.removesuffix(suffix))
            wm = info.get_startup_wm_class()
            if wm:
                keys.add(wm.lower())
            keys.add((info.get_name() or "").lower())
            for k in keys:
                if k and k not in self._by_key:
                    self._by_key[k] = info

    def lookup(self, *candidates) -> Gio.DesktopAppInfo | None:
        for c in candidates:
            if not c:
                continue
            c = str(c).lower().removesuffix(".desktop")
            for k in (c, c.split(".")[-1], c.removesuffix("-bin"), c.removesuffix("-stable")):
                if k in self._by_key:
                    return self._by_key[k]
        return None

    def lookup_bundle(self, bundle_id: str | None):
        if not bundle_id:
            return None
        try:
            return Gio.DesktopAppInfo.new(bundle_id + ".desktop")
        except TypeError:
            return None


def is_system_sound(props: dict) -> bool:
    role = (props.get("media.role") or "").lower()
    if role in EVENT_ROLES:
        return True
    if props.get("event.id") or props.get("media.event.id"):
        return True
    return (props.get("application.name") or "").lower() in ("libcanberra", "canberra-gtk-play")


def is_own_stream(props: dict) -> bool:
    return (props.get("finetune.engine") == "true"
            or (props.get("application.name") or "") in OWN_APP_NAMES
            or is_engine_node(props.get("node.name")))


def identity_for(props: dict, desktop: DesktopIndex):
    """Returns (identifier, display_name, bundle_id, binary, Gio.Icon)."""
    portal_id = props.get("pipewire.access.portal.app_id") or props.get("application.id")
    binary = props.get("application.process.binary")
    app_name = props.get("application.name")
    icon_name = props.get("application.icon_name")
    info = desktop.lookup_bundle(portal_id) if portal_id else None
    if info is None:
        info = desktop.lookup(portal_id, icon_name, binary, app_name)
    bundle_id = None
    name = app_name or binary or "Unknown"
    icon = None
    if info is not None:
        bundle_id = (info.get_id() or "").removesuffix(".desktop") or None
        name = info.get_name() or name
        icon = info.get_icon()
    if icon is None and icon_name:
        icon = Gio.ThemedIcon.new(icon_name)
    if icon is None:
        icon = Gio.ThemedIcon.new("application-x-executable")
    if bundle_id:
        ident = bundle_id
    elif portal_id:
        ident = portal_id
    elif binary:
        ident = binary
    else:
        ident = f"name:{name}"
    return ident, name, bundle_id or portal_id, binary, icon


def icon_for_identifier(ident: str, bundle_id: str | None, desktop: DesktopIndex):
    info = desktop.lookup_bundle(bundle_id) or desktop.lookup(bundle_id, ident.removeprefix("name:"))
    if info is not None and info.get_icon() is not None:
        return info.get_icon()
    return Gio.ThemedIcon.new("application-x-executable")


def group_apps(sink_inputs: list[dict], desktop: DesktopIndex) -> tuple[dict, list]:
    """Group sink-inputs into apps. Returns ({identifier: AudioApp}, [system sound streams])."""
    apps: dict[str, AudioApp] = {}
    system = []
    for si in sink_inputs:
        props = si.get("properties") or {}
        if is_own_stream(props):
            continue
        if (props.get("application.process.binary") or "") in SYSTEM_BINARIES and not props.get("application.name"):
            continue
        if is_system_sound(props):
            system.append(si)
            continue
        ident, name, bundle, binary, icon = identity_for(props, desktop)
        app = apps.get(ident)
        if app is None:
            app = AudioApp(ident, name, bundle, binary, icon)
            apps[ident] = app
        app.stream_indices.append(si["index"])
        app.sink_indices.add(si.get("sink"))
        pid = props.get("application.process.id")
        if pid:
            app.pids.add(pid)
        if not si.get("corked", False):
            app.corked = False
    return apps, system
