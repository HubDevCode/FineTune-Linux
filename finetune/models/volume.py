"""Volume mapping, boost levels, hotkey steps and the volume backend tiers."""
from __future__ import annotations

import math
from enum import Enum


class VolumeTier(str, Enum):
    """How a device's volume is controlled (port of VolumeControlTier)."""
    HARDWARE = "hardware"   # PipeWire/ALSA mixer volume (hardware or PipeWire soft-mixer)
    DDC = "ddc"             # external monitor speakers through DDC/CI (ddcutil)
    SOFTWARE = "software"   # FineTune attenuates inside its own audio graph

    @property
    def label(self) -> str:
        return {"hardware": "Hardware", "ddc": "DDC", "software": "Software"}[self.value]


def slider_to_gain(slider: float) -> float:
    """Per-app slider → linear PCM gain, square-law for perceptual linearity."""
    if slider <= 0:
        return 0.0
    t = min(slider, 1.0)
    return t * t


def gain_to_slider(gain: float) -> float:
    if gain <= 0:
        return 0.0
    return math.sqrt(min(gain, 1.0))


def slider_fraction_for_system_gain(gain: float, tier: VolumeTier) -> float:
    if tier == VolumeTier.SOFTWARE:
        return gain_to_slider(gain)
    return max(0.0, min(1.0, gain))


def system_gain_for_slider_fraction(fraction: float, tier: VolumeTier) -> float:
    if tier == VolumeTier.SOFTWARE:
        return slider_to_gain(fraction)
    return max(0.0, min(1.0, fraction))


BOOST_LEVELS = [1.0, 2.0, 3.0, 4.0]


def boost_label(boost: float) -> str:
    return f"{int(boost)}x"


def next_boost(boost: float) -> float:
    try:
        i = BOOST_LEVELS.index(float(boost))
    except ValueError:
        return 1.0
    return BOOST_LEVELS[(i + 1) % len(BOOST_LEVELS)]


def normalize_boost(raw) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return v if v in BOOST_LEVELS else 1.0


class VolumeHotkeyStep(str, Enum):
    COARSE = "coarse"
    NORMAL = "normal"
    FINE = "fine"
    EXTRA_FINE = "extraFine"

    @property
    def slider_delta(self) -> float:
        return {"coarse": 1 / 8, "normal": 1 / 16, "fine": 1 / 32, "extraFine": 1 / 64}[self.value]

    @property
    def label(self) -> str:
        return {"coarse": "Coarse", "normal": "Normal", "fine": "Fine", "extraFine": "Extra-Fine"}[self.value]

    @property
    def detail(self) -> str:
        return {"coarse": "8 steps", "normal": "16 steps", "fine": "32 steps", "extraFine": "64 steps"}[self.value]


def pulse_volume_to_fraction(raw: int) -> float:
    """PulseAudio volume integer (65536 = 100%) → 0..1+ fraction (cubic-mapped by PipeWire)."""
    return max(0.0, raw / 65536.0)


def fraction_to_percent(f: float) -> int:
    return int(round(f * 100))
