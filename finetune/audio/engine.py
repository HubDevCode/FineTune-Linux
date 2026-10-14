"""AudioEngine — the orchestrator (port of AudioEngine.swift + VolumeState + monitors).

It reconciles three things on every PipeWire change:

* the session state (devices, default devices, app streams) read via pactl,
* the user's intent (SettingsManager: volumes, routing, EQ, priorities, …),
* the DSP graph inside the C engine (one chain per app).

Routing is resolved dynamically: an app pinned to a device that disappears
falls back to the default device and automatically returns when the device
reconnects (macOS "auto-restore"), with notifications on both transitions.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter

from gi.repository import GLib

from finetune.audio import apps as apps_mod
from finetune.audio.bluetooth import BluetoothMonitor
from finetune.audio.ddc import DDCService
from finetune.audio.devices import AudioDevice, TRANSPORT_BLUETOOTH, is_engine_node
from finetune.audio.engine_client import EngineClient
from finetune.audio.pulse import PulseBackend, volume_fraction
from finetune.autoeq.manager import AutoEQManager
from finetune.models import loudness
from finetune.models.autoeq import AutoEQSelection
from finetune.models.eq import EQSettings
from finetune.models.volume import (
    VolumeTier, gain_to_slider, normalize_boost, slider_to_gain,
)
from finetune.settings import (
    DEVICE_SELECTION_MULTI, DEVICE_SELECTION_SINGLE, SettingsManager, resolve_highest_priority,
)

log = logging.getLogger("finetune.audio")

AUTO_SWITCH_GRACE_S = 3.0
BT_AUTO_SWITCH_GRACE_S = 8.0
CHAIN_LINGER_S = 15.0
ECHO_TTL_S = 5.0
MOVE_RETRY_S = 2.0


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:64]


class EchoTracker:
    """Suppresses the change notifications caused by FineTune's own default-device writes."""

    def __init__(self):
        self._pending: list[tuple[str, float]] = []

    def increment(self, uid: str):
        self._pending.append((uid, time.monotonic()))

    def consume(self, uid: str) -> bool:
        now = time.monotonic()
        self._pending = [(u, t) for u, t in self._pending if now - t < ECHO_TTL_S]
        for i, (u, _) in enumerate(self._pending):
            if u == uid:
                del self._pending[i]
                return True
        return False

    @property
    def has_pending(self) -> bool:
        now = time.monotonic()
        return any(now - t < ECHO_TTL_S for _, t in self._pending)


class Chain:
    def __init__(self, cid: str, ident: str, node_name: str):
        self.id = cid
        self.ident = ident
        self.node_name = node_name
        self.outputs: list[str] = []
        self.sent: dict[str, str] = {}
        self.gone_since: float | None = None


class AudioEngine:
    def __init__(self, settings: SettingsManager, notify=None):
        self.settings = settings
        self._notify = notify or (lambda title, body: None)
        self.pulse = PulseBackend()
        self.client = EngineClient()
        self.desktop = apps_mod.DesktopIndex()
        self.ddc = DDCService()
        self.bluetooth = BluetoothMonitor()
        self.autoeq = AutoEQManager(dispatch=lambda fn, *a: GLib.idle_add(fn, *a))

        self.outputs: list[AudioDevice] = []
        self.inputs: list[AudioDevice] = []
        self.apps: dict[str, apps_mod.AudioApp] = {}
        self.system_streams: list[dict] = []
        self.levels: dict[str, float] = {}       # app identifier / input uid → peak

        self._chains: dict[str, Chain] = {}
        self._chain_seq = 0
        self._sent_devices: dict[str, str] = {}
        self._pending_moves: dict[int, float] = {}
        self._applied_system_volume: dict[int, float] = {}
        self._known_outputs: set[str] | None = None
        self._known_inputs: set[str] | None = None
        self._output_names: dict[str, str] = {}
        self._last_default_out: str | None = None
        self._last_default_in: str | None = None
        self.confirmed_default_out: str | None = None
        self.confirmed_default_in: str | None = None
        self._pending_autoswitch: tuple[str, float] | None = None
        self._pending_input_autoswitch: tuple[str, float] | None = None
        self._echo_out = EchoTracker()
        self._echo_in = EchoTracker()
        self._resolved_profiles: dict[str, object] = {}
        self._resolving: set[str] = set()
        self._meters: set[str] = set()
        self._listeners = []
        self._level_listeners = []
        self._sync_id = 0
        self._started = False
        self._mem_selected: dict[str, set] = {}

        self.pulse.connect_changed(lambda kinds: self.schedule_sync())
        self.client.connect_ready(self._on_engine_ready)
        self.client.connect_levels(self._on_levels)
        self.client.connect_state(self._on_engine_state)
        self.ddc.connect_changed(self.schedule_sync)
        self.bluetooth.connect_changed(self._emit)
        self.autoeq.connect_changed(self._emit)
        settings.connect_app_settings_changed(self._on_app_settings_changed)

    # ----------------------------------------------------------- lifecycle

    def start(self):
        if self._started:
            return
        self._started = True
        self.pulse.start()
        self.client.start()
        self.ddc.probe()
        self.bluetooth.start()
        self.autoeq.load_catalog()
        GLib.timeout_add_seconds(5, self._housekeeping)

    def shutdown(self):
        """Return every app to its device (without FineTune's DSP) and stop the engine."""
        default = self.default_output_uid()
        for chain in self._chains.values():
            target = chain.outputs[0] if chain.outputs else default
            app = self.apps.get(chain.ident)
            if app and target:
                for idx in app.stream_indices:
                    self.pulse.move_sink_input(idx, target)
        self.client.stop()
        self.pulse.stop()
        self.settings.flush_sync()

    def connect_changed(self, cb):
        self._listeners.append(cb)

    def connect_levels(self, cb):
        self._level_listeners.append(cb)

    def _emit(self):
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("listener failed")

    def schedule_sync(self):
        if not self._sync_id:
            self._sync_id = GLib.timeout_add(30, self._sync)

    @property
    def engine_ok(self) -> bool:
        return self.client.ready

    # ------------------------------------------------------------- engine

    def _on_engine_ready(self):
        # A (re)started engine has no chains; rebuild everything.
        self._chains.clear()
        self._sent_devices.clear()
        for uid in self._meters:
            self.client.send(f"meter in_{_sanitize(uid)} {uid}")
        self.schedule_sync()

    def _on_engine_state(self, key: str, state: str, error: str):
        if state == "error":
            log.warning("engine stream %s error: %s", key, error)

    def _on_levels(self, levels: dict):
        out = {}
        for chain in self._chains.values():
            v = levels.get(chain.id)
            if v is not None:
                out[chain.ident] = v
        for uid in self._meters:
            v = levels.get(f"in_{_sanitize(uid)}")
            if v is not None:
                out[uid] = v
        self.levels = out
        for cb in self._level_listeners:
            cb(out)

    # --------------------------------------------------------------- sync

    def _sync(self):
        self._sync_id = 0
        if not self.pulse.is_ready():
            return False
        try:
            self._sync_devices()
            self._sync_apps()
            self._sync_system_sounds()
            self._sync_device_params()
        except Exception:  # noqa: BLE001 - never let a reconcile error kill the main loop
            log.exception("sync failed")
        self._emit()
        return False

    def _sync_devices(self):
        outs, ins = [], []
        for s in self.pulse.sinks:
            if is_engine_node(s.get("name")):
                continue
            outs.append(AudioDevice.from_pactl(s, False))
        for s in self.pulse.sources:
            props = s.get("properties") or {}
            name = s.get("name", "")
            if is_engine_node(name) or props.get("device.class") == "monitor" or name.endswith(".monitor"):
                continue
            if s.get("monitor_of_sink") not in (None, "", "n/a") or props.get("media.class") == "Audio/Sink":
                continue
            ins.append(AudioDevice.from_pactl(s, True))
        self.outputs, self.inputs = outs, ins

        out_uids = {d.uid for d in outs}
        in_uids = {d.uid for d in ins}
        names = {d.uid: d.name for d in outs}

        if self._known_outputs is None:
            # first snapshot: register everything, no notifications
            for d in outs:
                self.settings.ensure_device_in_priority(d.uid)
            for d in ins:
                self.settings.ensure_input_device_in_priority(d.uid)
            self.confirmed_default_out = self._real_default_out()
            self.confirmed_default_in = self.pulse.default_source
            if self.settings.app_settings.lock_input_device and not self.settings.locked_input_device_uid:
                self.settings.set_locked_input_device_uid(self.pulse.default_source)
        else:
            for uid in out_uids - self._known_outputs:
                self._handle_output_connected(uid, names.get(uid, uid))
            for uid in self._known_outputs - out_uids:
                self._handle_output_disconnected(uid, self._output_names.get(uid, uid))
            for uid in in_uids - self._known_inputs:
                self._handle_input_connected(uid)
            for uid in self._known_inputs - in_uids:
                self._handle_input_disconnected(uid)
        self._known_outputs, self._known_inputs = out_uids, in_uids
        self._output_names.update(names)

        default_out = self.pulse.default_sink
        if default_out != self._last_default_out:
            self._last_default_out = default_out
            if default_out:
                self._handle_default_changed(default_out)
        default_in = self.pulse.default_source
        if default_in != self._last_default_in:
            self._last_default_in = default_in
            if default_in:
                self._handle_default_input_changed(default_in)

    # ----------------------------------------------------- default devices

    def _real_default_out(self) -> str | None:
        d = self.pulse.default_sink
        if d and not is_engine_node(d) and any(o.uid == d for o in self.outputs):
            return d
        return None

    def default_output_uid(self) -> str | None:
        d = self._real_default_out()
        if d:
            return d
        if self.confirmed_default_out and any(o.uid == self.confirmed_default_out for o in self.outputs):
            return self.confirmed_default_out
        return resolve_highest_priority(self.settings.output_device_priority, [o.uid for o in self.outputs])

    def default_output(self) -> AudioDevice | None:
        return self.output_by_uid(self.default_output_uid())

    def default_input(self) -> AudioDevice | None:
        return self.input_by_uid(self.pulse.default_source)

    def output_by_uid(self, uid) -> AudioDevice | None:
        return next((d for d in self.outputs if d.uid == uid), None)

    def input_by_uid(self, uid) -> AudioDevice | None:
        return next((d for d in self.inputs if d.uid == uid), None)

    def set_default_output(self, uid: str, user: bool = True):
        if not uid or is_engine_node(uid):
            return
        self._echo_out.increment(uid)
        if user:
            self.confirmed_default_out = uid
            self._pending_autoswitch = None
        self.pulse.set_default_sink(uid)

    def set_default_input(self, uid: str, user: bool = True):
        if not uid:
            return
        self._echo_in.increment(uid)
        if user:
            self.confirmed_default_in = uid
            self._pending_input_autoswitch = None
            if self.settings.app_settings.lock_input_device:
                self.settings.set_locked_input_device_uid(uid)
                self.settings.set_preferred_input_device_uid(uid)
        self.pulse.set_default_source(uid)

    def _grace_for(self, uid):
        d = self.output_by_uid(uid) or self.input_by_uid(uid)
        return BT_AUTO_SWITCH_GRACE_S if d and d.transport == TRANSPORT_BLUETOOTH else AUTO_SWITCH_GRACE_S

    def _handle_output_connected(self, uid: str, name: str):
        self.settings.ensure_device_in_priority(uid)
        connected = [o.uid for o in self.outputs]
        best = resolve_highest_priority(self.settings.output_device_priority, connected)
        current = self._real_default_out()
        if best == uid and current != uid:
            log.info("Switching to higher-priority device %s", name)
            self.set_default_output(uid)
        else:
            # The session manager may auto-switch to the new device; revert it if so.
            self._pending_autoswitch = (uid, time.monotonic() + self._grace_for(uid))
        returning = [a.name for ident, a in self.apps.items()
                     if self.settings.get_device_routing(ident) == uid]
        if returning and self.settings.app_settings.show_device_disconnect_alerts:
            self._notify("Audio Device Reconnected",
                         f"{len(returning)} app(s) returned to “{name}”")

    def _handle_output_disconnected(self, uid: str, name: str):
        self._sent_devices.pop(uid, None)
        affected = [c.ident for c in self._chains.values() if uid in c.outputs]
        connected = [o.uid for o in self.outputs]
        fallback = resolve_highest_priority(self.settings.output_device_priority, connected, excluding=uid)
        if self.confirmed_default_out == uid or self.pulse.default_sink == uid or not self._real_default_out():
            if fallback:
                self.set_default_output(fallback)
        # multi-mode: drop the device from the in-memory selection (persisted choice stays)
        for ident in affected:
            sel = self.get_selected_device_uids(ident)
            if uid in sel:
                self._mem_selected[ident] = sel - {uid}
        if affected and self.settings.app_settings.show_device_disconnect_alerts:
            fb = self._output_names.get(fallback, fallback or "none")
            self._notify("Audio Device Disconnected",
                         f"“{name}” disconnected. {len(affected)} app(s) switched to “{fb}”")

    def _handle_default_changed(self, new: str):
        if self._echo_out.consume(new):
            if not is_engine_node(new):
                self.confirmed_default_out = new
            return
        if is_engine_node(new):
            # Never let one of FineTune's virtual sinks become the system default.
            fallback = self.confirmed_default_out or resolve_highest_priority(
                self.settings.output_device_priority, [o.uid for o in self.outputs])
            if fallback:
                self.set_default_output(fallback, user=False)
            return
        pending = self._pending_autoswitch
        if pending and pending[0] == new and time.monotonic() < pending[1] and self.confirmed_default_out \
                and self.confirmed_default_out != new and self.output_by_uid(self.confirmed_default_out):
            log.info("Reverting session-manager auto-switch to %s (priority)", new)
            self.set_default_output(self.confirmed_default_out, user=False)
            self._pending_autoswitch = (new, time.monotonic() + self._grace_for(new))
            return
        self._pending_autoswitch = None
        prev = self.confirmed_default_out
        self.confirmed_default_out = new
        if prev and prev != new:
            following = [a for i, a in self.apps.items() if self.is_following_default(i) and not self.settings.is_ignored(i)]
            if following and self.settings.app_settings.show_device_disconnect_alerts:
                dev = self.output_by_uid(new)
                self._notify("Default Audio Device Changed",
                             f"{len(following)} app(s) switched to “{dev.name if dev else new}”")

    def _handle_input_connected(self, uid: str):
        self.settings.ensure_input_device_in_priority(uid)
        s = self.settings
        if s.app_settings.lock_input_device:
            if s.preferred_input_device_uid == uid and self.pulse.default_source != uid:
                self.set_default_input(uid, user=False)
                s.set_locked_input_device_uid(uid)
            else:
                self._pending_input_autoswitch = (uid, time.monotonic() + self._grace_for(uid))
        else:
            best = resolve_highest_priority(s.input_device_priority, [i.uid for i in self.inputs])
            if best == uid and self.pulse.default_source != uid:
                self.set_default_input(uid, user=False)

    def _handle_input_disconnected(self, uid: str):
        s = self.settings
        if s.app_settings.lock_input_device and s.locked_input_device_uid == uid:
            fallback = resolve_highest_priority(s.input_device_priority, [i.uid for i in self.inputs], excluding=uid)
            if fallback:
                s.set_locked_input_device_uid(fallback)  # preferred stays for when it returns
                self.set_default_input(fallback, user=False)
        self.stop_input_meter(uid)

    def _handle_default_input_changed(self, new: str):
        if self._echo_in.consume(new):
            self.confirmed_default_in = new
            return
        s = self.settings
        pending = self._pending_input_autoswitch
        locked = s.locked_input_device_uid
        if s.app_settings.lock_input_device and locked and locked != new and self.input_by_uid(locked):
            if pending and pending[0] == new and time.monotonic() < pending[1]:
                log.info("Input lock: reverting auto-switch to %s", new)
                self.set_default_input(locked, user=False)
                return
            # explicit user change outside FineTune → respect it and move the lock
            s.set_locked_input_device_uid(new)
            s.set_preferred_input_device_uid(new)
        self._pending_input_autoswitch = None
        self.confirmed_default_in = new

    # --------------------------------------------------------------- apps

    def _sync_apps(self):
        apps, system = apps_mod.group_apps(self.pulse.sink_inputs, self.desktop)
        self.apps = apps
        self.system_streams = system
        sink_index = {s.get("name"): s.get("index") for s in self.pulse.sinks}
        now = time.monotonic()
        self._pending_moves = {k: v for k, v in self._pending_moves.items() if now - v < MOVE_RETRY_S}
        default = self.default_output_uid()

        for ident, app in apps.items():
            if self.settings.is_ignored(ident):
                # Disengage: streams parked on a FineTune sink go back to the default device
                for chain in [c for c in self._chains.values() if c.ident == ident]:
                    self._drop_chain(chain)
                engine_sinks = {s.get("index") for s in self.pulse.sinks if is_engine_node(s.get("name"))}
                for si in self.pulse.sink_inputs:
                    if si["index"] in app.stream_indices and si.get("sink") in engine_sinks and default:
                        self._move(si["index"], default)
                continue
            if not self.client.ready:
                continue
            chain = self._chains.get(ident)
            if chain is None:
                chain = self._create_chain(app)
            chain.gone_since = None
            self._apply_chain_state(chain, app)
            target_index = sink_index.get(chain.node_name)
            if target_index is None:
                continue  # sink not visible yet; next sync moves the streams
            for si in self.pulse.sink_inputs:
                if si["index"] in app.stream_indices and si.get("sink") != target_index:
                    self._move(si["index"], chain.node_name)

        for chain in list(self._chains.values()):
            if chain.ident not in apps:
                if chain.gone_since is None:
                    chain.gone_since = now
                    self._send(chain, "active", "0")
                elif now - chain.gone_since > CHAIN_LINGER_S:
                    self._drop_chain(chain)

    def _move(self, index: int, target: str):
        if index in self._pending_moves:
            return
        self._pending_moves[index] = time.monotonic()
        self.pulse.move_sink_input(index, target)

    def _create_chain(self, app) -> Chain:
        self._chain_seq += 1
        cid = f"c{self._chain_seq}"
        node = "finetune_app_" + _sanitize(app.identifier)
        chain = Chain(cid, app.identifier, node)
        self._chains[app.identifier] = chain
        self.client.send(f"chain {cid} {node} FineTune · {app.name}")
        log.info("Created chain %s for %s", cid, app.name)
        return chain

    def _drop_chain(self, chain: Chain):
        self.client.send(f"drop {chain.id}")
        self._chains.pop(chain.ident, None)

    def _send(self, chain: Chain, cmd: str, args: str):
        if chain.sent.get(cmd) == args:
            return
        chain.sent[cmd] = args
        self.client.send(f"{cmd} {chain.id} {args}".rstrip())

    def _apply_chain_state(self, chain: Chain, app):
        ident = chain.ident
        outs = self.resolve_outputs(ident)
        if outs != chain.outputs:
            chain.outputs = outs
        self._send(chain, "outs", " ".join(outs))
        vol = self.get_volume(ident)
        self._send(chain, "gain", f"{vol * self.get_boost(ident):.5f}")
        self._send(chain, "mute", "1" if self.get_mute(ident) else "0")
        eq = self.get_eq_settings(ident)
        self._send(chain, "eq", ("1 " if eq.is_enabled else "0 ") + " ".join(f"{g:.2f}" for g in eq.clamped_gains))
        a = self.settings.app_settings
        self._send(chain, "lev", "1" if a.loudness_equalization_enabled else "0")
        if a.loudness_compensation_enabled:
            dev = self.output_by_uid(outs[0]) if outs else None
            dev_vol = self.device_volume(dev) if dev else 1.0
            enabled, gains = loudness.section_gains_for_volume(min(1.0, dev_vol) * vol)
            self._send(chain, "lc", ("1 " if enabled else "0 ") + " ".join(f"{g:.2f}" for g in gains))
        else:
            self._send(chain, "lc", "0 0 0 0 0")
        self._send(chain, "active", "1" if app.stream_indices else "0")

    def refresh_chain(self, ident: str):
        chain = self._chains.get(ident)
        app = self.apps.get(ident)
        if chain and app:
            self._apply_chain_state(chain, app)

    def refresh_all_chains(self):
        for ident in list(self._chains):
            self.refresh_chain(ident)

    def resolve_outputs(self, ident: str) -> list[str]:
        connected = [o.uid for o in self.outputs]
        default = self.default_output_uid()
        if self.get_device_selection_mode(ident) == DEVICE_SELECTION_MULTI:
            sel = self.get_selected_device_uids(ident)
            chosen = [u for u in connected if u in sel]
            if chosen:
                return chosen
        if not self.is_following_default(ident):
            uid = self.settings.get_device_routing(ident)
            if uid in connected:
                return [uid]
        return [default] if default else []

    # ------------------------------------------------------- system sounds

    def _sync_system_sounds(self):
        a = self.settings.app_settings
        target = None
        if not self.settings.system_sounds_follows_default:
            uid = self.settings.system_sounds_device_uid
            if self.output_by_uid(uid):
                target = uid
        sink_index = {s.get("name"): s.get("index") for s in self.pulse.sinks}
        alive = set()
        for si in self.system_streams:
            idx = si["index"]
            alive.add(idx)
            if abs(volume_fraction(si) - a.alert_volume) > 0.01 and self._applied_system_volume.get(idx) != a.alert_volume:
                self._applied_system_volume[idx] = a.alert_volume
                self.pulse.set_sink_input_volume(idx, a.alert_volume)
            if target and si.get("sink") != sink_index.get(target):
                self._move(idx, target)
        self._applied_system_volume = {k: v for k, v in self._applied_system_volume.items() if k in alive}

    def system_sounds_device(self) -> AudioDevice | None:
        if self.settings.system_sounds_follows_default:
            return None
        return self.output_by_uid(self.settings.system_sounds_device_uid)

    def set_system_sounds_device(self, uid: str | None):
        self.settings.set_system_sounds_follow_default(uid is None, uid)
        self.schedule_sync()

    # ------------------------------------------------- per-device DSP params

    def _sync_device_params(self):
        if not self.client.ready:
            return
        for dev in self.outputs:
            gain, mute = 1.0, False
            if self.volume_tier(dev) == VolumeTier.SOFTWARE:
                sw = self.settings.get_software_device_volume(dev.uid)
                gain = 1.0 if sw is None else slider_to_gain(sw)
                mute = self.settings.get_software_device_mute(dev.uid)
            aeq_on, preamp, rate, filters = False, 0.0, 48000.0, []
            sel = self.settings.get_auto_eq_selection(dev.uid)
            if sel and sel.is_enabled:
                prof = self._profile_for_selection(sel)
                if prof:
                    v = prof.validated()
                    if v.filters:
                        aeq_on = True
                        preamp = v.preamp_db if self.settings.auto_eq_preamp_enabled else 0.0
                        rate = v.optimized_sample_rate
                        filters = v.filters
            line = (f"{dev.uid} {gain:.5f} {1 if mute else 0} {1 if aeq_on else 0} {preamp:.2f} {rate:.0f} {len(filters)}"
                    + "".join(f" {f.engine_code} {f.frequency:.3f} {f.gain_db:.3f} {f.q:.4f}" for f in filters))
            if self._sent_devices.get(dev.uid) != line:
                self._sent_devices[dev.uid] = line
                self.client.send("dev " + line)

    def _profile_for_selection(self, sel: AutoEQSelection):
        pid = sel.profile_id
        prof = self.autoeq.profile(pid)
        if prof:
            return prof
        if pid not in self._resolving:
            self._resolving.add(pid)

            def done(p):
                self._resolving.discard(pid)
                if p:
                    self.schedule_sync()
            self.autoeq.resolve_profile(pid, done)
        return None

    # ------------------------------------------------------ app accessors

    def get_volume(self, ident: str) -> float:
        v = self.settings.get_volume(ident)
        return v if v is not None else self.settings.app_settings.default_new_app_volume

    def set_volume(self, ident: str, volume: float):
        self.settings.set_volume(ident, max(0.0, min(1.0, volume)))
        self.refresh_chain(ident)

    def get_boost(self, ident: str) -> float:
        return normalize_boost(self.settings.get_boost(ident) or 1.0)

    def set_boost(self, ident: str, boost: float):
        self.settings.set_boost(ident, normalize_boost(boost))
        self.refresh_chain(ident)

    def get_mute(self, ident: str) -> bool:
        return bool(self.settings.get_mute(ident))

    def set_mute(self, ident: str, muted: bool):
        self.settings.set_mute(ident, muted)
        self.refresh_chain(ident)

    def toggle_mute(self, ident: str):
        self.set_mute(ident, not self.get_mute(ident))

    def get_eq_settings(self, ident: str) -> EQSettings:
        return self.settings.get_eq_settings(ident)

    def set_eq_settings(self, ident: str, eq: EQSettings):
        self.settings.set_eq_settings(eq, ident)
        self.refresh_chain(ident)

    def get_device_selection_mode(self, ident: str) -> str:
        return self.settings.get_device_selection_mode(ident) or DEVICE_SELECTION_SINGLE

    def set_device_selection_mode(self, ident: str, mode: str):
        self.settings.set_device_selection_mode(ident, mode)
        if mode == DEVICE_SELECTION_MULTI and not self.get_selected_device_uids(ident):
            current = self.resolve_outputs(ident)
            self.settings.set_selected_device_uids(ident, set(current))
        self.refresh_chain(ident)
        self._emit()

    def get_selected_device_uids(self, ident: str) -> set:
        if ident in self._mem_selected:
            return set(self._mem_selected[ident])
        return self.settings.get_selected_device_uids(ident) or set()

    def set_selected_device_uids(self, ident: str, uids):
        self._mem_selected.pop(ident, None)
        self.settings.set_selected_device_uids(ident, set(uids))
        self.refresh_chain(ident)
        self._emit()

    def is_following_default(self, ident: str) -> bool:
        if self.settings.is_following_default(ident):
            return True
        return self.settings.get_device_routing(ident) not in {o.uid for o in self.outputs}

    def get_device_uid(self, ident: str) -> str | None:
        return self.settings.get_device_routing(ident)

    def set_device(self, ident: str, uid: str | None):
        """Route an app to a device; None = follow the system default ("System Audio")."""
        if uid is None:
            self.settings.set_follow_default(ident)
        else:
            self.settings.set_device_routing(ident, uid)
        if self.get_device_selection_mode(ident) == DEVICE_SELECTION_MULTI:
            self.settings.set_device_selection_mode(ident, DEVICE_SELECTION_SINGLE)
        self.refresh_chain(ident)
        self._emit()

    def current_outputs(self, ident: str) -> list[str]:
        chain = self._chains.get(ident)
        return chain.outputs if chain else self.resolve_outputs(ident)

    def is_audible(self, ident: str) -> bool:
        return self.levels.get(ident, 0.0) > 0.001

    # ------------------------------------------------------ pinned/ignored

    def pin_app(self, ident: str, name: str, bundle_id: str | None):
        self.settings.pin_app(ident, name, bundle_id)
        self._emit()

    def unpin_app(self, ident: str):
        self.settings.unpin_app(ident)
        self._emit()

    def ignore_app(self, ident: str, name: str, bundle_id: str | None):
        self.settings.ignore_app(ident, name, bundle_id)
        self.schedule_sync()

    def unignore_app(self, ident: str):
        self.settings.unignore_app(ident)
        self.schedule_sync()

    def displayable_apps(self) -> list[tuple[str, object]]:
        """[("active", AudioApp) | ("pinned", info dict)] — active first, then pinned inactive."""
        active = [a for i, a in self.apps.items() if not self.settings.is_ignored(i)]
        active.sort(key=lambda a: (not self.settings.is_pinned(a.identifier), a.name.lower()))
        out = [("active", a) for a in active]
        for info in sorted(self.settings.get_pinned_app_info(), key=lambda i: i.get("displayName", "").lower()):
            if info["persistenceIdentifier"] not in self.apps:
                out.append(("pinned", info))
        return out

    def app_icon(self, ident: str, bundle_id: str | None = None):
        app = self.apps.get(ident)
        if app and app.icon is not None:
            return app.icon
        return apps_mod.icon_for_identifier(ident, bundle_id, self.desktop)

    def find_app(self, query: str):
        for a in self.apps.values():
            if a.matches(query):
                return a
        return None

    def reset_all_apps(self):
        """finetune://reset — every app back to 100% and unmuted."""
        for ident in set(self.settings.app_volumes) | set(self.apps):
            self.settings.set_volume(ident, 1.0)
            self.settings.set_mute(ident, False)
        self.refresh_all_chains()
        self._emit()

    def handle_settings_reset(self):
        self._mem_selected.clear()
        self._sent_devices.clear()
        for c in self._chains.values():
            c.sent.clear()
        self.schedule_sync()

    # ------------------------------------------------------- device volume

    def auto_volume_tier(self, dev: AudioDevice) -> VolumeTier:
        if dev.is_input:
            return VolumeTier.HARDWARE
        if self.ddc.match_device(dev):
            return VolumeTier.DDC
        return VolumeTier.HARDWARE

    def volume_tier(self, dev: AudioDevice) -> VolumeTier:
        o = self.settings.get_device_volume_tier_override(dev.uid)
        if o == VolumeTier.DDC and not self.ddc.match_device(dev):
            o = None
        return o or self.auto_volume_tier(dev)

    def set_volume_tier_override(self, dev: AudioDevice, tier: VolumeTier | None):
        self.settings.set_device_volume_tier_override(dev.uid, tier)
        self.schedule_sync()

    def device_volume(self, dev: AudioDevice | None) -> float:
        if dev is None:
            return 0.0
        tier = self.volume_tier(dev)
        if tier == VolumeTier.SOFTWARE:
            v = self.settings.get_software_device_volume(dev.uid)
            return 1.0 if v is None else v
        if tier == VolumeTier.DDC:
            disp = self.ddc.match_device(dev)
            saved = self.settings.get_ddc_volume(dev.uid)
            if saved is not None:
                return saved / 100
            return self.ddc.percent(disp) / 100 if disp else 0.5
        return min(dev.volume, 1.0)

    def device_muted(self, dev: AudioDevice | None) -> bool:
        if dev is None:
            return False
        tier = self.volume_tier(dev)
        if tier == VolumeTier.SOFTWARE:
            return self.settings.get_software_device_mute(dev.uid)
        if tier == VolumeTier.DDC:
            return self.settings.get_ddc_mute(dev.uid)
        return dev.muted

    def set_device_volume(self, dev: AudioDevice, fraction: float):
        f = max(0.0, min(1.0, fraction))
        if dev.is_input:
            dev.volume = f
            self.pulse.set_source_volume(dev.uid, f)
            if f > 0 and dev.muted:
                self.pulse.set_source_mute(dev.uid, False)
            return
        tier = self.volume_tier(dev)
        if tier == VolumeTier.SOFTWARE:
            self.settings.set_software_device_volume(dev.uid, f)
            if f > 0 and self.settings.get_software_device_mute(dev.uid):
                self.settings.set_software_device_mute(dev.uid, False)
            self._sync_device_params()
        elif tier == VolumeTier.DDC:
            disp = self.ddc.match_device(dev)
            self.settings.set_ddc_volume(dev.uid, int(round(f * 100)))
            if self.settings.get_ddc_mute(dev.uid) and f > 0:
                self.settings.set_ddc_mute(dev.uid, False)
            if disp:
                self.ddc.set_volume(disp, int(round(f * 100)))
        else:
            dev.volume = f
            self.pulse.set_sink_volume(dev.uid, f)
            if f > 0 and dev.muted:
                dev.muted = False
                self.pulse.set_sink_mute(dev.uid, False)
        if self.settings.app_settings.loudness_compensation_enabled:
            self.refresh_all_chains()

    def set_device_mute(self, dev: AudioDevice, muted: bool):
        if dev.is_input:
            dev.muted = muted
            self.pulse.set_source_mute(dev.uid, muted)
            return
        tier = self.volume_tier(dev)
        if tier == VolumeTier.SOFTWARE:
            self.settings.set_software_device_mute(dev.uid, muted)
            self._sync_device_params()
        elif tier == VolumeTier.DDC:
            disp = self.ddc.match_device(dev)
            self.settings.set_ddc_mute(dev.uid, muted)
            if muted:
                cur = self.settings.get_ddc_volume(dev.uid)
                self.settings.set_ddc_saved_volume(dev.uid, cur if cur is not None else 50)
                if disp:
                    self.ddc.set_volume(disp, 0)
            else:
                restore = self.settings.get_ddc_saved_volume(dev.uid) or 50
                self.settings.set_ddc_volume(dev.uid, restore)
                if disp:
                    self.ddc.set_volume(disp, restore)
        else:
            dev.muted = muted
            self.pulse.set_sink_mute(dev.uid, muted)

    def toggle_device_mute(self, dev: AudioDevice):
        self.set_device_mute(dev, not self.device_muted(dev))

    def step_device_volume(self, dev: AudioDevice, direction: int, step: float) -> float:
        """Slider-domain step with auto-unmute on volume up (media keys / hotkeys / arrows)."""
        cur = self.device_volume(dev)
        if direction > 0 and self.device_muted(dev):
            self.set_device_mute(dev, False)
        new = max(0.0, min(1.0, round((cur + direction * step) / step) * step))
        self.set_device_volume(dev, new)
        return new

    def step_app_volume(self, ident: str, direction: int, step: float) -> float:
        slider = gain_to_slider(self.get_volume(ident))
        if direction > 0 and self.get_mute(ident):
            self.set_mute(ident, False)
        slider = max(0.0, min(1.0, slider + direction * step))
        gain = slider_to_gain(slider)
        self.set_volume(ident, gain)
        return gain

    def set_sample_rate(self, rate: int | None):
        """PipeWire graph rate (clock.force-rate); 0/None returns to automatic."""
        args = ["pw-metadata", "-n", "settings", "0", "clock.force-rate", str(int(rate or 0))]
        try:
            GLib.spawn_async(args, flags=GLib.SpawnFlags.SEARCH_PATH)
        except GLib.Error as e:
            log.error("pw-metadata failed: %s", e.message)
        GLib.timeout_add(500, lambda: (self.pulse.refresh({"sinks", "sources"}), False)[1])

    def set_device_port(self, dev: AudioDevice, port: str):
        if dev.is_input:
            self.pulse.set_source_port(dev.uid, port)
        else:
            self.pulse.set_sink_port(dev.uid, port)

    # ------------------------------------------------------------- AutoEQ

    def get_auto_eq_selection(self, uid: str):
        return self.settings.get_auto_eq_selection(uid)

    def set_auto_eq_profile(self, uid: str, profile_id: str | None):
        self.settings.set_auto_eq_selection(uid, AutoEQSelection(profile_id, True) if profile_id else None)
        self.schedule_sync()

    def set_auto_eq_enabled(self, uid: str, enabled: bool):
        sel = self.settings.get_auto_eq_selection(uid)
        if sel:
            self.settings.set_auto_eq_selection(uid, AutoEQSelection(sel.profile_id, enabled))
            self.schedule_sync()

    def set_auto_eq_preamp_enabled(self, enabled: bool):
        self.settings.set_auto_eq_preamp_enabled(enabled)
        self.schedule_sync()

    def auto_eq_profile_name(self, uid: str) -> str | None:
        sel = self.settings.get_auto_eq_selection(uid)
        if not sel:
            return None
        prof = self.autoeq.profile(sel.profile_id)
        if prof:
            return prof.name
        e = self.autoeq.entry_for_display(sel.profile_id)
        return e.name if e else None

    # --------------------------------------------------------- input meters

    def start_input_meter(self, uid: str):
        if uid in self._meters:
            return
        self._meters.add(uid)
        self.client.send(f"meter in_{_sanitize(uid)} {uid}")

    def stop_input_meter(self, uid: str):
        if uid not in self._meters:
            return
        self._meters.discard(uid)
        self.client.send(f"unmeter in_{_sanitize(uid)}")

    def stop_all_input_meters(self):
        for uid in list(self._meters):
            self.stop_input_meter(uid)

    # ------------------------------------------------------------ settings

    def _on_app_settings_changed(self, old, new):
        if (old.loudness_compensation_enabled != new.loudness_compensation_enabled
                or old.loudness_equalization_enabled != new.loudness_equalization_enabled):
            self.refresh_all_chains()
        if old.lock_input_device != new.lock_input_device and new.lock_input_device:
            self.settings.set_locked_input_device_uid(self.pulse.default_source)
            self.settings.set_preferred_input_device_uid(self.pulse.default_source)
        if old.alert_volume != new.alert_volume:
            self._applied_system_volume.clear()
        self.schedule_sync()

    def _housekeeping(self):
        # periodic reconcile (chain linger timeouts, stale-settings pruning)
        self.schedule_sync()
        if self.pulse.is_ready():
            self.settings.prune_stale_settings(set(self.apps))
        return True
