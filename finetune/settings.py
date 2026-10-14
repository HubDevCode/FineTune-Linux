"""Persistent settings (port of SettingsManager.swift).

The JSON layout mirrors the macOS settings.json (version 12) so the semantics
are identical: per-app state is keyed by the app's persistence identifier,
per-device state by the device UID (PipeWire node name on Linux).
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from pathlib import Path

from finetune import paths
from finetune.models.autoeq import AutoEQSelection
from finetune.models.eq import EQSettings, UserEQPreset
from finetune.models.volume import BOOST_LEVELS, VolumeHotkeyStep, VolumeTier

log = logging.getLogger("finetune.settings")

SETTINGS_VERSION = 12

MENU_BAR_ICON_STYLES = ["Default", "Speaker", "Device", "Waveform", "Equalizer"]
HUD_STYLES = ["tahoe", "classic"]
APPEARANCES = ["system", "light", "dark"]
POPUP_SIZES = ["compact", "comfortable", "spacious"]
POPUP_DIMENSIONS = {
    "compact": {"width": 470, "padding": 12, "max_height": 560},
    "comfortable": {"width": 510, "padding": 16, "max_height": 660},
    "spacious": {"width": 560, "padding": 20, "max_height": 760},
}

DEVICE_SELECTION_SINGLE = "single"
DEVICE_SELECTION_MULTI = "multi"


def _finite_nonneg(v, default=None, cap=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f) or f < 0:
        return default
    return min(f, cap) if cap is not None else f


class AppSettings:
    """App-wide preferences (General / Audio / Media keys / Shortcuts / Appearance)."""

    def __init__(self, d: dict | None = None):
        d = d if isinstance(d, dict) else {}
        self.launch_at_login = bool(d.get("launchAtLogin", False))
        style = d.get("menuBarIconStyle", "Default")
        self.menu_bar_icon_style = style if style in MENU_BAR_ICON_STYLES else "Default"
        v = _finite_nonneg(d.get("defaultNewAppVolume", 1.0), 1.0)
        self.default_new_app_volume = min(v, 1.0)
        self.lock_input_device = bool(d.get("lockInputDevice", True))
        self.show_device_disconnect_alerts = bool(d.get("showDeviceDisconnectAlerts", True))
        self.loudness_compensation_enabled = bool(d.get("loudnessCompensationEnabled", False))
        self.loudness_equalization_enabled = bool(d.get("loudnessEqualizationEnabled", False))
        hud = d.get("hudStyle", "tahoe")
        self.hud_style = hud if hud in HUD_STYLES else "tahoe"
        self.hud_enabled = bool(d.get("hudEnabled", True))
        self.media_key_control_enabled = bool(d.get("mediaKeyControlEnabled", True))
        try:
            self.volume_hotkey_step = VolumeHotkeyStep(d.get("volumeHotkeyStep", "normal"))
        except ValueError:
            self.volume_hotkey_step = VolumeHotkeyStep.NORMAL
        cs = d.get("customShortcuts", {})
        self.custom_shortcuts: dict[str, str] = {str(k): str(v) for k, v in cs.items()} if isinstance(cs, dict) else {}
        ap = d.get("appearance", "system")
        self.appearance = ap if ap in APPEARANCES else "system"
        ps = d.get("popupSize", "comfortable")
        self.popup_size = ps if ps in POPUP_SIZES else "comfortable"
        # Linux additions
        self.alert_volume = min(_finite_nonneg(d.get("alertVolume", 1.0), 1.0), 1.0)
        self.volume_feedback_enabled = bool(d.get("volumeFeedbackEnabled", False))
        self.check_for_updates_automatically = bool(d.get("checkForUpdatesAutomatically", True))
        self.last_update_check = float(d.get("lastUpdateCheck", 0) or 0)

    def set_unified_loudness_enabled(self, enabled: bool):
        self.loudness_compensation_enabled = enabled
        self.loudness_equalization_enabled = enabled

    def to_json(self) -> dict:
        return {
            "launchAtLogin": self.launch_at_login,
            "menuBarIconStyle": self.menu_bar_icon_style,
            "defaultNewAppVolume": self.default_new_app_volume,
            "lockInputDevice": self.lock_input_device,
            "showDeviceDisconnectAlerts": self.show_device_disconnect_alerts,
            "loudnessCompensationEnabled": self.loudness_compensation_enabled,
            "loudnessEqualizationEnabled": self.loudness_equalization_enabled,
            "hudStyle": self.hud_style,
            "hudEnabled": self.hud_enabled,
            "mediaKeyControlEnabled": self.media_key_control_enabled,
            "volumeHotkeyStep": self.volume_hotkey_step.value,
            "customShortcuts": dict(self.custom_shortcuts),
            "appearance": self.appearance,
            "popupSize": self.popup_size,
            "alertVolume": self.alert_volume,
            "volumeFeedbackEnabled": self.volume_feedback_enabled,
            "checkForUpdatesAutomatically": self.check_for_updates_automatically,
            "lastUpdateCheck": self.last_update_check,
        }

    def copy(self) -> "AppSettings":
        return AppSettings(self.to_json())


class SettingsManager:
    def __init__(self, directory: Path | None = None, scheduler=None):
        self.directory = Path(directory) if directory else paths.config_dir()
        self.path = self.directory / "settings.json"
        self._scheduler = scheduler  # callable(delay_ms, fn) -> handle ; None = save on flush only
        self._save_pending = False
        self._listeners = []
        self._reset_defaults()
        self._load()

    # ------------------------------------------------------------------ io

    def _reset_defaults(self):
        self.version = SETTINGS_VERSION
        self.app_volumes: dict[str, float] = {}
        self.app_device_routing: dict[str, str] = {}
        self.app_mutes: dict[str, bool] = {}
        self.app_boosts: dict[str, float] = {}
        self.app_eq_settings: dict[str, EQSettings] = {}
        self.app_settings = AppSettings()
        self.system_sounds_follows_default = True
        self.system_sounds_device_uid: str | None = None
        self.app_device_selection_mode: dict[str, str] = {}
        self.app_selected_device_uids: dict[str, list[str]] = {}
        self.locked_input_device_uid: str | None = None
        self.preferred_input_device_uid: str | None = None
        self.pinned_apps: set[str] = set()
        self.pinned_app_info: dict[str, dict] = {}
        self.ignored_apps: set[str] = set()
        self.ignored_app_info: dict[str, dict] = {}
        self.ddc_volumes: dict[str, int] = {}
        self.ddc_mute_states: dict[str, bool] = {}
        self.ddc_saved_volumes: dict[str, int] = {}
        self.software_device_volumes: dict[str, float] = {}
        self.software_device_mute_states: dict[str, bool] = {}
        self.software_device_saved_volumes: dict[str, float] = {}
        self.device_volume_tier_override: dict[str, VolumeTier] = {}
        self.device_icon_overrides: dict[str, str] = {}
        self.output_device_priority: list[str] = []
        self.input_device_priority: list[str] = []
        self.hidden_output_device_uids: set[str] = set()
        self.hidden_input_device_uids: set[str] = set()
        self.device_auto_eq: dict[str, AutoEQSelection] = {}
        self.favorite_auto_eq_profiles: set[str] = set()
        self.auto_eq_preamp_enabled = True
        self.user_eq_presets: list[UserEQPreset] = []

    def _load(self):
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError) as e:
            log.error("Failed to load settings: %s", e)
            try:
                self.path.replace(self.path.with_suffix(".json.corrupt"))
            except OSError:
                pass
            return
        self._apply_json(d)

    def _apply_json(self, d: dict):
        if not isinstance(d, dict):
            return

        def dict_of(key, conv):
            raw = d.get(key, {})
            out = {}
            if isinstance(raw, dict):
                for k, v in raw.items():
                    try:
                        cv = conv(v)
                    except (TypeError, ValueError, KeyError):
                        continue
                    if cv is not None:
                        out[str(k)] = cv
            return out

        self.version = int(d.get("version", 9))
        self.app_volumes = dict_of("appVolumes", lambda v: _finite_nonneg(v, None, 1.0))
        self.app_device_routing = dict_of("appDeviceRouting", str)
        self.app_mutes = dict_of("appMutes", bool)
        self.app_boosts = dict_of("appBoosts", lambda v: float(v) if float(v) in BOOST_LEVELS else None)
        self.app_eq_settings = dict_of("appEQSettings", EQSettings.from_json)
        self.app_settings = AppSettings(d.get("appSettings"))
        self.system_sounds_follows_default = bool(d.get("systemSoundsFollowsDefault", True))
        self.system_sounds_device_uid = d.get("systemSoundsDeviceUID")
        self.app_device_selection_mode = dict_of(
            "appDeviceSelectionMode", lambda v: v if v in (DEVICE_SELECTION_SINGLE, DEVICE_SELECTION_MULTI) else None)
        self.app_selected_device_uids = dict_of("appSelectedDeviceUIDs", lambda v: [str(x) for x in v])
        self.locked_input_device_uid = d.get("lockedInputDeviceUID")
        self.preferred_input_device_uid = d.get("preferredInputDeviceUID")
        self.pinned_apps = set(map(str, d.get("pinnedApps", []) or []))
        self.pinned_app_info = dict_of("pinnedAppInfo", lambda v: dict(v))
        self.ignored_apps = set(map(str, d.get("ignoredApps", []) or []))
        self.ignored_app_info = dict_of("ignoredAppInfo", lambda v: dict(v))
        self.ddc_volumes = dict_of("ddcVolumes", lambda v: max(0, min(100, int(v))))
        self.ddc_mute_states = dict_of("ddcMuteStates", bool)
        self.ddc_saved_volumes = dict_of("ddcSavedVolumes", lambda v: max(0, min(100, int(v))))
        self.software_device_volumes = dict_of("softwareDeviceVolumes", lambda v: _finite_nonneg(v, None, 1.0))
        self.software_device_mute_states = dict_of("softwareDeviceMuteStates", bool)
        self.software_device_saved_volumes = dict_of("softwareDeviceSavedVolumes", lambda v: _finite_nonneg(v, None, 1.0))
        self.device_volume_tier_override = dict_of("deviceVolumeTierOverride", VolumeTier)
        self.device_icon_overrides = dict_of("deviceIconOverrides", str)
        self.output_device_priority = [str(x) for x in d.get("outputDevicePriority", []) or []]
        self.input_device_priority = [str(x) for x in d.get("inputDevicePriority", []) or []]
        self.hidden_output_device_uids = set(map(str, d.get("hiddenOutputDeviceUIDs", []) or []))
        self.hidden_input_device_uids = set(map(str, d.get("hiddenInputDeviceUIDs", []) or []))
        self.device_auto_eq = dict_of("deviceAutoEQ", AutoEQSelection.from_json)
        self.favorite_auto_eq_profiles = set(map(str, d.get("favoriteAutoEQProfiles", []) or []))
        self.auto_eq_preamp_enabled = bool(d.get("autoEQPreampEnabled", True))
        self.user_eq_presets = [p for p in (UserEQPreset.from_json(x) for x in d.get("userEQPresets", []) or []) if p]

    def to_json(self) -> dict:
        return {
            "version": SETTINGS_VERSION,
            "appVolumes": self.app_volumes,
            "appDeviceRouting": self.app_device_routing,
            "appMutes": self.app_mutes,
            "appBoosts": self.app_boosts,
            "appEQSettings": {k: v.to_json() for k, v in self.app_eq_settings.items()},
            "appSettings": self.app_settings.to_json(),
            "systemSoundsFollowsDefault": self.system_sounds_follows_default,
            "systemSoundsDeviceUID": self.system_sounds_device_uid,
            "appDeviceSelectionMode": self.app_device_selection_mode,
            "appSelectedDeviceUIDs": self.app_selected_device_uids,
            "lockedInputDeviceUID": self.locked_input_device_uid,
            "preferredInputDeviceUID": self.preferred_input_device_uid,
            "pinnedApps": sorted(self.pinned_apps),
            "pinnedAppInfo": self.pinned_app_info,
            "ignoredApps": sorted(self.ignored_apps),
            "ignoredAppInfo": self.ignored_app_info,
            "ddcVolumes": self.ddc_volumes,
            "ddcMuteStates": self.ddc_mute_states,
            "ddcSavedVolumes": self.ddc_saved_volumes,
            "softwareDeviceVolumes": self.software_device_volumes,
            "softwareDeviceMuteStates": self.software_device_mute_states,
            "softwareDeviceSavedVolumes": self.software_device_saved_volumes,
            "deviceVolumeTierOverride": {k: v.value for k, v in self.device_volume_tier_override.items()},
            "deviceIconOverrides": self.device_icon_overrides,
            "outputDevicePriority": self.output_device_priority,
            "inputDevicePriority": self.input_device_priority,
            "hiddenOutputDeviceUIDs": sorted(self.hidden_output_device_uids),
            "hiddenInputDeviceUIDs": sorted(self.hidden_input_device_uids),
            "deviceAutoEQ": {k: v.to_json() for k, v in self.device_auto_eq.items()},
            "favoriteAutoEQProfiles": sorted(self.favorite_auto_eq_profiles),
            "autoEQPreampEnabled": self.auto_eq_preamp_enabled,
            "userEQPresets": [p.to_json() for p in self.user_eq_presets],
        }

    def schedule_save(self):
        if self._scheduler is None:
            self._save_pending = True
            return
        if self._save_pending:
            return
        self._save_pending = True

        def fire():
            self.flush_sync()
            return False

        self._scheduler(500, fire)

    def flush_sync(self):
        self._save_pending = False
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            data = json.dumps(self.to_json(), indent=2, sort_keys=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.directory), prefix=".settings.", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                f.write(data)
            os.replace(tmp, self.path)
        except OSError as e:
            log.error("Failed to save settings: %s", e)

    # ------------------------------------------------------------ per-app

    def get_volume(self, ident: str):
        return self.app_volumes.get(ident)

    def set_volume(self, ident: str, volume: float):
        self.app_volumes[ident] = max(0.0, min(1.0, float(volume)))
        self.schedule_save()

    def get_boost(self, ident: str):
        return self.app_boosts.get(ident)

    def set_boost(self, ident: str, boost: float):
        self.app_boosts[ident] = float(boost)
        self.schedule_save()

    def get_device_routing(self, ident: str):
        return self.app_device_routing.get(ident)

    def set_device_routing(self, ident: str, uid: str):
        self.app_device_routing[ident] = uid
        self.schedule_save()

    def is_following_default(self, ident: str) -> bool:
        return ident not in self.app_device_routing

    def set_follow_default(self, ident: str):
        self.app_device_routing.pop(ident, None)
        self.schedule_save()

    def set_system_sounds_follow_default(self, follows: bool, uid: str | None = None):
        self.system_sounds_follows_default = follows
        self.system_sounds_device_uid = None if follows else uid
        self.schedule_save()

    def get_mute(self, ident: str):
        return self.app_mutes.get(ident)

    def set_mute(self, ident: str, muted: bool):
        self.app_mutes[ident] = bool(muted)
        self.schedule_save()

    def get_eq_settings(self, ident: str) -> EQSettings:
        s = self.app_eq_settings.get(ident)
        return s.copy() if s else EQSettings()

    def set_eq_settings(self, settings: EQSettings, ident: str):
        self.app_eq_settings[ident] = settings.copy()
        self.schedule_save()

    def get_device_selection_mode(self, ident: str):
        return self.app_device_selection_mode.get(ident)

    def set_device_selection_mode(self, ident: str, mode: str):
        self.app_device_selection_mode[ident] = mode
        self.schedule_save()

    def get_selected_device_uids(self, ident: str):
        v = self.app_selected_device_uids.get(ident)
        return set(v) if v is not None else None

    def set_selected_device_uids(self, ident: str, uids):
        self.app_selected_device_uids[ident] = sorted(uids)
        self.schedule_save()

    def set_locked_input_device_uid(self, uid):
        self.locked_input_device_uid = uid
        self.schedule_save()

    def set_preferred_input_device_uid(self, uid):
        self.preferred_input_device_uid = uid
        self.schedule_save()

    # ------------------------------------------------------ pinned/ignored

    def pin_app(self, ident: str, display_name: str, bundle_id: str | None):
        self.pinned_apps.add(ident)
        self.pinned_app_info[ident] = {"persistenceIdentifier": ident, "displayName": display_name, "bundleID": bundle_id}
        self.schedule_save()

    def unpin_app(self, ident: str):
        self.pinned_apps.discard(ident)
        self.pinned_app_info.pop(ident, None)
        self.schedule_save()

    def is_pinned(self, ident: str) -> bool:
        return ident in self.pinned_apps

    def get_pinned_app_info(self) -> list[dict]:
        return [self.pinned_app_info[i] for i in sorted(self.pinned_apps) if i in self.pinned_app_info]

    def ignore_app(self, ident: str, display_name: str, bundle_id: str | None):
        self.ignored_apps.add(ident)
        self.ignored_app_info[ident] = {"persistenceIdentifier": ident, "displayName": display_name, "bundleID": bundle_id}
        # Hiding is mutually exclusive with pinning, and clears per-app state
        self.pinned_apps.discard(ident)
        self.pinned_app_info.pop(ident, None)
        for m in (self.app_volumes, self.app_boosts, self.app_mutes, self.app_device_routing,
                  self.app_eq_settings, self.app_device_selection_mode, self.app_selected_device_uids):
            m.pop(ident, None)
        self.schedule_save()

    def unignore_app(self, ident: str):
        self.ignored_apps.discard(ident)
        self.ignored_app_info.pop(ident, None)
        self.schedule_save()

    def is_ignored(self, ident: str) -> bool:
        return ident in self.ignored_apps

    def get_ignored_app_info(self) -> list[dict]:
        return [self.ignored_app_info[i] for i in sorted(self.ignored_apps) if i in self.ignored_app_info]

    # ------------------------------------------------------------- devices

    def get_device_volume_tier_override(self, uid: str):
        return self.device_volume_tier_override.get(uid)

    def set_device_volume_tier_override(self, uid: str, tier: VolumeTier | None):
        if tier is None:
            self.device_volume_tier_override.pop(uid, None)
        else:
            self.device_volume_tier_override[uid] = tier
        self.schedule_save()

    def get_device_icon_override(self, uid: str):
        return self.device_icon_overrides.get(uid)

    def set_device_icon_override(self, uid: str, icon: str | None):
        if icon:
            self.device_icon_overrides[uid] = icon
        else:
            self.device_icon_overrides.pop(uid, None)
        self.schedule_save()

    def ensure_device_in_priority(self, uid: str):
        if uid not in self.output_device_priority:
            self.output_device_priority.append(uid)
            self.schedule_save()

    def ensure_input_device_in_priority(self, uid: str):
        if uid not in self.input_device_priority:
            self.input_device_priority.append(uid)
            self.schedule_save()

    def set_device_priority_order(self, uids):
        self.output_device_priority = list(uids)
        self.schedule_save()

    def set_input_device_priority_order(self, uids):
        self.input_device_priority = list(uids)
        self.schedule_save()

    def merge_device_priority_order(self, connected_order):
        self.output_device_priority = merge_priority_order(self.output_device_priority, connected_order)
        self.schedule_save()

    def merge_input_device_priority_order(self, connected_order):
        self.input_device_priority = merge_priority_order(self.input_device_priority, connected_order)
        self.schedule_save()

    def toggle_output_device_hidden(self, uid: str):
        if uid in self.hidden_output_device_uids:
            self.hidden_output_device_uids.discard(uid)
        else:
            self.hidden_output_device_uids.add(uid)
        self.schedule_save()

    def toggle_input_device_hidden(self, uid: str):
        if uid in self.hidden_input_device_uids:
            self.hidden_input_device_uids.discard(uid)
        else:
            self.hidden_input_device_uids.add(uid)
        self.schedule_save()

    # software / DDC device volumes
    def get_software_device_volume(self, uid):
        return self.software_device_volumes.get(uid)

    def set_software_device_volume(self, uid, v):
        self.software_device_volumes[uid] = max(0.0, min(1.0, float(v)))
        self.schedule_save()

    def get_software_device_mute(self, uid) -> bool:
        return self.software_device_mute_states.get(uid, False)

    def set_software_device_mute(self, uid, muted):
        self.software_device_mute_states[uid] = bool(muted)
        self.schedule_save()

    def get_ddc_volume(self, uid):
        return self.ddc_volumes.get(uid)

    def set_ddc_volume(self, uid, v):
        self.ddc_volumes[uid] = max(0, min(100, int(v)))
        self.schedule_save()

    def get_ddc_mute(self, uid) -> bool:
        return self.ddc_mute_states.get(uid, False)

    def set_ddc_mute(self, uid, muted):
        self.ddc_mute_states[uid] = bool(muted)
        self.schedule_save()

    def get_ddc_saved_volume(self, uid):
        return self.ddc_saved_volumes.get(uid)

    def set_ddc_saved_volume(self, uid, v):
        self.ddc_saved_volumes[uid] = int(v)
        self.schedule_save()

    # -------------------------------------------------------------- AutoEQ

    def get_auto_eq_selection(self, uid: str):
        return self.device_auto_eq.get(uid)

    def set_auto_eq_selection(self, uid: str, sel: AutoEQSelection | None):
        if sel is None:
            self.device_auto_eq.pop(uid, None)
        else:
            self.device_auto_eq[uid] = sel
        self.schedule_save()

    def favorite_auto_eq_profile(self, pid: str):
        self.favorite_auto_eq_profiles.add(pid)
        self.schedule_save()

    def unfavorite_auto_eq_profile(self, pid: str):
        self.favorite_auto_eq_profiles.discard(pid)
        self.schedule_save()

    def is_auto_eq_favorite(self, pid: str) -> bool:
        return pid in self.favorite_auto_eq_profiles

    def set_auto_eq_preamp_enabled(self, enabled: bool):
        self.auto_eq_preamp_enabled = bool(enabled)
        self.schedule_save()

    # --------------------------------------------------------- user presets

    def get_user_presets(self) -> list[UserEQPreset]:
        return sorted(self.user_eq_presets, key=lambda p: p.created_at)

    def _unique_preset_name(self, name: str, exclude_id: str | None = None) -> str:
        name = name.strip() or "Custom"
        taken = {p.name.lower() for p in self.user_eq_presets if p.id != exclude_id}
        if name.lower() not in taken:
            return name
        counter = 2
        while f"{name} {counter}".lower() in taken:
            counter += 1
        return f"{name} {counter}"

    def create_user_preset(self, name: str, settings: EQSettings) -> UserEQPreset:
        preset = UserEQPreset(self._unique_preset_name(name), EQSettings(list(settings.band_gains), True))
        self.user_eq_presets.append(preset)
        self.schedule_save()
        return preset

    def update_user_preset(self, pid: str, name: str):
        for p in self.user_eq_presets:
            if p.id == pid:
                p.name = self._unique_preset_name(name, exclude_id=pid)
        self.schedule_save()

    def delete_user_preset(self, pid: str):
        self.user_eq_presets = [p for p in self.user_eq_presets if p.id != pid]
        self.schedule_save()

    # ------------------------------------------------------------ app-wide

    def update_app_settings(self, new: AppSettings):
        old = self.app_settings
        self.app_settings = new
        if old.launch_at_login != new.launch_at_login:
            from finetune.util import autostart
            autostart.set_enabled(new.launch_at_login)
        self.schedule_save()
        for cb in list(self._listeners):
            cb(old, new)

    def connect_app_settings_changed(self, cb):
        self._listeners.append(cb)

    def reset_all_settings(self):
        launch = self.app_settings.launch_at_login
        self._reset_defaults()
        self.app_settings.launch_at_login = launch
        self.flush_sync()

    def prune_stale_settings(self, active_identifiers):
        """Drop per-app entries that are inactive, unpinned, unrouted and default-valued."""
        active = set(active_identifiers)
        idents = (set(self.app_volumes) | set(self.app_boosts) | set(self.app_mutes) | set(self.app_eq_settings)
                  | set(self.app_device_selection_mode) | set(self.app_selected_device_uids))
        pruned = 0
        for ident in idents:
            if ident in active or ident in self.pinned_apps or ident in self.app_device_routing:
                continue
            vol = self.app_volumes.get(ident)
            boost = self.app_boosts.get(ident)
            mute = self.app_mutes.get(ident)
            eq = self.app_eq_settings.get(ident)
            if not ((vol is None or vol == 1.0) and (boost is None or boost == 1.0) and not mute
                    and (eq is None or (eq.is_flat() and eq.is_enabled))
                    and ident not in self.app_device_selection_mode
                    and not self.app_selected_device_uids.get(ident)):
                continue
            for m in (self.app_volumes, self.app_boosts, self.app_mutes, self.app_eq_settings,
                      self.app_device_selection_mode, self.app_selected_device_uids):
                m.pop(ident, None)
            pruned += 1
        if pruned:
            log.info("Pruned %d stale app settings entries", pruned)
            self.schedule_save()
        return pruned


def merge_priority_order(old_priority, connected_order):
    """Merge a reordered list of connected UIDs back into the full priority list.

    Disconnected UIDs keep their position relative to the connected UID that preceded
    them ("anchor"); disconnected UIDs before any connected one stay at the front.
    """
    connected = set(connected_order)
    groups: dict = {}
    anchor = None
    for uid in old_priority:
        if uid in connected:
            anchor = uid
        else:
            groups.setdefault(anchor, []).append(uid)
    result = list(groups.get(None, []))
    for uid in connected_order:
        result.append(uid)
        result.extend(groups.get(uid, []))
    return result


def resolve_highest_priority(priority_order, connected_uids, excluding=None, is_alive=None):
    """Return the highest-priority connected UID (port of AudioEngine.resolveHighestPriority)."""
    connected = [u for u in connected_uids if u != excluding and (is_alive is None or is_alive(u))]
    for uid in priority_order:
        if uid in connected:
            return uid
    return connected[0] if connected else None
