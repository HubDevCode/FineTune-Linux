"""Audio devices (sinks/sources) and the device icon catalog.

Port of AudioDevice, AudioDeviceID+Classification/Info/Inspector,
DeviceIconCatalog and DeviceIconResolver. On Linux the device UID is the
PipeWire node name, which is stable across reboots like a CoreAudio UID.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from finetune.audio.pulse import volume_fraction

ENGINE_PREFIXES = ("finetune_app_", "finetune_out.", "finetune_meter.")

TRANSPORT_BUILTIN = "Built-in"
TRANSPORT_USB = "USB"
TRANSPORT_BLUETOOTH = "Bluetooth"
TRANSPORT_HDMI = "HDMI"
TRANSPORT_DISPLAYPORT = "DisplayPort"
TRANSPORT_VIRTUAL = "Virtual"
TRANSPORT_NETWORK = "Network"
TRANSPORT_FIREWIRE = "FireWire"
TRANSPORT_THUNDERBOLT = "Thunderbolt"
TRANSPORT_PCI = "PCI"
TRANSPORT_UNKNOWN = "Unknown"

FORMAT_LABELS = {
    "s16le": "16-bit PCM", "s16be": "16-bit PCM", "s24le": "24-bit PCM", "s24be": "24-bit PCM",
    "s24-32le": "24-bit PCM", "s32le": "32-bit PCM", "s32be": "32-bit PCM",
    "float32le": "32-bit Float", "float32be": "32-bit Float", "u8": "8-bit PCM",
}

COMMON_RATES = [44100, 48000, 88200, 96000, 176400, 192000]


def is_engine_node(name: str | None) -> bool:
    return bool(name) and name.startswith(ENGINE_PREFIXES)


@dataclass
class AudioDevice:
    uid: str
    index: int
    name: str
    is_input: bool
    volume: float = 1.0
    muted: bool = False
    state: str = ""
    sample_spec: str = ""
    rate: int = 0
    format: str = ""
    channels: int = 2
    transport: str = TRANSPORT_UNKNOWN
    icon_name: str = "audio-card-symbolic"
    form_factor: str = ""
    ports: list = field(default_factory=list)
    active_port: str | None = None
    card_index: int | None = None
    card_name: str | None = None
    has_hw_volume: bool = True
    is_virtual: bool = False
    product: str = ""
    bluetooth_address: str | None = None
    properties: dict = field(default_factory=dict)

    @property
    def supports_auto_eq(self) -> bool:
        return not self.is_input and not self.is_virtual

    @property
    def format_label(self) -> str:
        return FORMAT_LABELS.get(self.format, self.format or "—")

    @property
    def port_type(self) -> str:
        for p in self.ports:
            if p.get("name") == self.active_port:
                return (p.get("type") or "").lower()
        return ""

    @property
    def active_port_description(self) -> str:
        for p in self.ports:
            if p.get("name") == self.active_port:
                return p.get("description") or ""
        return ""

    @property
    def is_headphones(self) -> bool:
        return (self.form_factor in ("headphone", "headset", "hands-free")
                or self.port_type in ("headphones", "headset")
                or "headphone" in (self.active_port or ""))

    @classmethod
    def from_pactl(cls, e: dict, is_input: bool) -> "AudioDevice":
        props = e.get("properties") or {}
        spec = e.get("sample_specification") or ""
        fmt, ch, rate = _parse_spec(spec)
        flags = e.get("flags") or []
        d = cls(
            uid=e.get("name", ""),
            index=int(e.get("index", -1)),
            name=props.get("device.description") and _description(e, props) or e.get("description") or e.get("name", ""),
            is_input=is_input,
            volume=volume_fraction(e),
            muted=bool(e.get("mute")),
            state=e.get("state", ""),
            sample_spec=spec, rate=rate, format=fmt, channels=ch,
            form_factor=props.get("device.form_factor", ""),
            ports=e.get("ports") or [],
            active_port=e.get("active_port"),
            card_index=_int(props.get("card.id") or e.get("card")),
            card_name=props.get("device.name"),
            has_hw_volume="HW_VOLUME_CTRL" in flags,
            product=props.get("device.product.name", ""),
            bluetooth_address=props.get("api.bluez5.address"),
            properties=props,
        )
        d.transport = classify_transport(props, d.active_port or "")
        d.is_virtual = d.transport == TRANSPORT_VIRTUAL
        d.icon_name = default_icon(d)
        return d


def _description(e, props):
    # Prefer the node description (includes the profile, e.g. "… Analog Stereo") but
    # fall back to the device description; pactl already localizes it.
    return e.get("description") or props.get("device.description") or e.get("name", "")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_spec(spec: str):
    m = re.match(r"(\S+)\s+(\d+)ch\s+(\d+)Hz", spec or "")
    if not m:
        return "", 2, 0
    return m.group(1), int(m.group(2)), int(m.group(3))


def classify_transport(props: dict, active_port: str) -> str:
    api = props.get("device.api", "")
    bus = props.get("device.bus", "")
    factory = props.get("factory.name", "")
    port = (active_port or "").lower()
    profile = props.get("device.profile.name", "").lower()
    if api == "bluez5" or bus == "bluetooth":
        return TRANSPORT_BLUETOOTH
    if props.get("node.virtual") == "true" or "null-audio-sink" in factory or props.get("media.class", "").startswith("Audio/Sink") and not api:
        if "raop" in factory or props.get("device.api") == "raop":
            return TRANSPORT_NETWORK
        return TRANSPORT_VIRTUAL
    if api in ("raop", "snapcast", "roc") or "raop" in factory or "network" in factory or bus == "network":
        return TRANSPORT_NETWORK
    if "hdmi" in port or "hdmi" in profile:
        return TRANSPORT_HDMI
    if "displayport" in port or "-dp" in port:
        return TRANSPORT_DISPLAYPORT
    if bus == "usb":
        return TRANSPORT_USB
    if bus == "firewire" or api == "ffado":
        return TRANSPORT_FIREWIRE
    if bus == "thunderbolt":
        return TRANSPORT_THUNDERBOLT
    if bus == "pci" or props.get("device.form_factor") == "internal":
        return TRANSPORT_BUILTIN
    return TRANSPORT_UNKNOWN


def default_icon(d: AudioDevice) -> str:
    """Best-guess icon for a device (port of DeviceIconResolver / AudioDeviceIconSymbol)."""
    ff = d.form_factor
    pt = d.port_type
    name = d.name.lower()
    if d.is_input:
        if ff == "webcam" or "webcam" in name or "camera" in name:
            return "camera-web-symbolic"
        if ff in ("headset", "hands-free") or pt == "headset":
            return "audio-headset-symbolic"
        return "audio-input-microphone-symbolic"
    if ff in ("headset", "hands-free") or pt == "headset":
        return "audio-headset-symbolic"
    if ff == "headphone" or pt == "headphones" or "headphone" in (d.active_port or ""):
        if any(k in name for k in ("buds", "airpods", "earbud", "wf-", "galaxy buds")):
            return "ft-earbuds-symbolic"
        return "audio-headphones-symbolic"
    if d.transport == TRANSPORT_BLUETOOTH:
        if any(k in name for k in ("buds", "airpods", "earbud", "wf-")):
            return "ft-earbuds-symbolic"
        if any(k in name for k in ("wh-", "headphone", "qc", "momentum", "acton", "bose", "beats", "xm")):
            return "audio-headphones-symbolic"
        return "ft-hifispeaker-symbolic"
    if d.transport in (TRANSPORT_HDMI, TRANSPORT_DISPLAYPORT):
        return "video-display-symbolic"
    if d.transport == TRANSPORT_VIRTUAL:
        return "ft-waveform-symbolic"
    if d.transport == TRANSPORT_NETWORK:
        return "ft-airplay-symbolic"
    if ff == "tv":
        return "ft-tv-symbolic"
    if ff in ("speaker", "computer"):
        return "audio-speakers-symbolic"
    if d.transport == TRANSPORT_USB:
        return "audio-card-symbolic"
    if d.transport == TRANSPORT_BUILTIN or ff == "internal":
        return "ft-laptop-symbolic" if pt in ("speaker", "") else "audio-speakers-symbolic"
    return "audio-speakers-symbolic"


# --------------------------------------------------------------- icon catalog

ICON_CATALOG = [
    ("Headphones & Earbuds", [
        ("audio-headphones-symbolic", ["headphones", "cans", "over-ear"]),
        ("ft-earbuds-symbolic", ["earbuds", "in-ear", "airpods", "buds"]),
        ("audio-headset-symbolic", ["headset", "gaming", "calls", "boom"]),
    ]),
    ("Speakers", [
        ("audio-speakers-symbolic", ["speaker", "speakers"]),
        ("ft-hifispeaker-symbolic", ["speaker", "hifi", "bluetooth"]),
        ("ft-speakers-pair-symbolic", ["speakers", "stereo", "pair", "monitors"]),
        ("ft-soundbar-symbolic", ["soundbar", "tv", "home theater"]),
        ("ft-receiver-symbolic", ["receiver", "amp", "home theater"]),
        ("ft-radio-symbolic", ["radio", "boombox"]),
        ("ft-megaphone-symbolic", ["megaphone", "pa"]),
    ]),
    ("Computers & Displays", [
        ("ft-laptop-symbolic", ["laptop", "notebook", "built-in"]),
        ("computer-symbolic", ["desktop", "computer", "pc"]),
        ("video-display-symbolic", ["display", "monitor", "screen", "hdmi"]),
        ("ft-tv-symbolic", ["tv", "television", "hdmi"]),
        ("ft-projector-symbolic", ["projector", "beamer"]),
        ("phone-symbolic", ["phone", "mobile"]),
        ("multimedia-player-symbolic", ["player", "ipod", "dap"]),
    ]),
    ("Microphones", [
        ("audio-input-microphone-symbolic", ["mic", "microphone"]),
        ("ft-mic-studio-symbolic", ["mic", "vocal", "studio", "podcast"]),
        ("camera-web-symbolic", ["webcam", "camera"]),
    ]),
    ("Connectors & Other", [
        ("audio-card-symbolic", ["interface", "dac", "usb", "sound card"]),
        ("ft-cable-symbolic", ["cable", "jack", "aux", "3.5mm"]),
        ("ft-eq-symbolic", ["mixer", "faders", "interface"]),
        ("ft-waveform-symbolic", ["waveform", "virtual", "loopback"]),
        ("ft-airplay-symbolic", ["airplay", "network", "cast"]),
        ("bluetooth-symbolic", ["bluetooth", "wireless"]),
        ("network-wireless-symbolic", ["wireless", "wifi", "signal"]),
        ("input-gaming-symbolic", ["game", "controller", "console"]),
        ("ft-car-symbolic", ["car", "auto"]),
        ("ft-guitar-symbolic", ["guitar", "instrument", "amp"]),
        ("ft-piano-symbolic", ["piano", "keyboard", "midi"]),
        ("media-optical-symbolic", ["cd", "disc", "player"]),
        ("audio-x-generic-symbolic", ["music", "note", "audio"]),
    ]),
]

ALL_ICONS = [icon for _, entries in ICON_CATALOG for icon, _ in entries]


def search_icons(query: str) -> list[str]:
    q = query.lower().strip()
    if not q:
        return ALL_ICONS
    out = []
    for _, entries in ICON_CATALOG:
        for icon, kws in entries:
            if q in icon or any(q in k for k in kws):
                out.append(icon)
    return out
