"""Settings window (port of SettingsRootView + General/Audio/Shortcuts/Updates/About tabs)."""
from __future__ import annotations

import os
import shutil
import time

from gi.repository import Adw, GLib, Gtk

from finetune import APP_ID, AUTHOR, DONATE_URL, ORIGINAL_PROJECT, REPO_URL, VERSION
from finetune.control.shortcuts import ACTIONS, MEDIA_ACTIONS
from finetune.models.volume import VolumeHotkeyStep
from finetune.settings import APPEARANCES, HUD_STYLES, MENU_BAR_ICON_STYLES, POPUP_SIZES
from finetune.ui.widgets import copy_to_clipboard, open_uri
from finetune.util import autostart, updates

ICON_FOR_STYLE = {
    "Default": "finetune-symbolic", "Speaker": "audio-volume-high-symbolic",
    "Device": "audio-headphones-symbolic", "Waveform": "ft-waveform-symbolic", "Equalizer": "ft-eq-symbolic",
}


def finetune_executable() -> str:
    """Absolute path of the launcher: compositors (Hyprland, Sway) run binds with a PATH
    that usually lacks ~/.local/bin, so a bare `finetune` would not be found."""
    exe = shutil.which("finetune")
    if exe:
        return exe.replace(os.path.expanduser("~"), "~", 1)
    local = os.path.expanduser("~/.local/bin/finetune")
    return "~/.local/bin/finetune" if os.path.exists(local) else "finetune"


def _ago(ts: float) -> str:
    if not ts:
        return "never"
    d = int(time.time() - ts)
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


class SettingsWindow(Adw.Window):
    def __init__(self, app, engine, portal):
        super().__init__(application=app, title="FineTune Settings")
        self.app = app
        self.engine = engine
        self.portal = portal
        self.settings = engine.settings
        self.set_default_size(640, 720)
        self.set_hide_on_close(True)

        stack = self.stack = Adw.ViewStack()
        stack.add_titled_with_icon(self._general(), "general", "General", "emblem-system-symbolic")
        stack.add_titled_with_icon(self._audio(), "audio", "Audio", "audio-speakers-symbolic")
        stack.add_titled_with_icon(self._shortcuts(), "shortcuts", "Shortcuts", "preferences-desktop-keyboard-shortcuts-symbolic")
        stack.add_titled_with_icon(self._updates(), "updates", "Updates", "software-update-available-symbolic")
        stack.add_titled_with_icon(self._about(), "about", "About", "help-about-symbolic")
        header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(stack)
        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)

    # --------------------------------------------------------------- helpers

    def _update(self, **kw):
        new = self.settings.app_settings.copy()
        for k, v in kw.items():
            setattr(new, k, v)
        self.settings.update_app_settings(new)

    def _switch_row(self, title, subtitle, value, on_change):
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=value)
        row.connect("notify::active", lambda r, *_: on_change(r.get_active()))
        return row

    def _combo_row(self, title, subtitle, labels, index, on_change):
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new(labels))
        row.set_selected(max(0, index))
        row.connect("notify::selected", lambda r, *_: on_change(r.get_selected()))
        return row

    def _slider_row(self, title, subtitle, value, on_change):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
        scale.set_value(value)
        scale.set_size_request(200, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.add_css_class("ft-slider")
        lbl = Gtk.Label(label=f"{int(round(value * 100))}%")
        lbl.add_css_class("ft-percent")

        def changed(s):
            lbl.set_text(f"{int(round(s.get_value() * 100))}%")
            on_change(s.get_value())

        scale.connect("value-changed", changed)
        row.add_suffix(scale)
        row.add_suffix(lbl)
        return row

    def _segmented_row(self, title, subtitle, options, current, on_change, icons=None):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        box = Gtk.Box(valign=Gtk.Align.CENTER)
        box.add_css_class("ft-segmented")
        first = None
        for opt in options:
            if icons:
                b = Gtk.ToggleButton(icon_name=icons[opt], tooltip_text=opt)
            else:
                b = Gtk.ToggleButton(label=opt[:1].upper() + opt[1:])
            if first is None:
                first = b
            else:
                b.set_group(first)
            b.set_active(opt == current)
            b.connect("toggled", lambda btn, o=opt: btn.get_active() and on_change(o))
            box.append(b)
        row.add_suffix(box)
        return row

    # --------------------------------------------------------------- General

    def _general(self):
        a = self.settings.app_settings
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="General")
        g.add(self._switch_row("Launch at Login", "Start FineTune when you log in",
                               a.launch_at_login or autostart.is_enabled(),
                               lambda v: self._update(launch_at_login=v)))
        page.add(g)

        m = Adw.PreferencesGroup(title="Menu Bar")
        m.add(self._segmented_row("Icon Style", "How FineTune appears in your tray / bar",
                                  MENU_BAR_ICON_STYLES, a.menu_bar_icon_style,
                                  lambda v: self._update(menu_bar_icon_style=v), icons=ICON_FOR_STYLE))
        page.add(m)

        ap = Adw.PreferencesGroup(title="Appearance")
        ap.add(self._segmented_row("Theme", "Match the system, or lock to Light or Dark",
                                   APPEARANCES, a.appearance, lambda v: self._update(appearance=v)))
        ap.add(self._segmented_row("Popup Size", "Smaller fits more on screen; larger leaves more breathing room.",
                                   POPUP_SIZES, a.popup_size, lambda v: self._update(popup_size=v)))
        page.add(ap)

        n = Adw.PreferencesGroup(title="Notifications")
        n.add(self._switch_row("Device Disconnect Alerts", "Show notification when an audio device disconnects",
                               a.show_device_disconnect_alerts,
                               lambda v: self._update(show_device_disconnect_alerts=v)))
        page.add(n)

        d = Adw.PreferencesGroup(title="Data")
        reset = Adw.ActionRow(title="Reset All Settings", subtitle="Clear all volumes, EQ, and device routings")
        rb = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER)
        rb.add_css_class("destructive-action")
        rb.connect("clicked", lambda *_: self._confirm_reset())
        reset.add_suffix(rb)
        d.add(reset)
        loc = Adw.ActionRow(title="Settings file", subtitle=str(self.settings.path))
        loc.set_subtitle_selectable(True)
        d.add(loc)
        page.add(d)
        return page

    def _confirm_reset(self):
        dlg = Adw.AlertDialog(heading="Reset All Settings?", body="This cannot be undone.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("reset", "Reset")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(d, res):
            if d.choose_finish(res) == "reset":
                self.settings.reset_all_settings()
                self.engine.handle_settings_reset()
                self.toasts.add_toast(Adw.Toast(title="All settings were reset"))

        dlg.choose(self, None, done)

    # ----------------------------------------------------------------- Audio

    def _audio(self):
        a = self.settings.app_settings
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Audio")
        g.add(self._slider_row("Default Volume", "Initial volume for new apps", a.default_new_app_volume,
                               lambda v: self._update(default_new_app_volume=v)))
        g.add(self._switch_row("Lock Input Device", "Prevent auto-switching when devices connect",
                               a.lock_input_device, lambda v: self._update(lock_input_device=v)))
        g.add(self._switch_row("Loudness Equalization",
                               "Balances frequency response and volume levels at low listening levels",
                               a.loudness_compensation_enabled or a.loudness_equalization_enabled,
                               self._set_loudness))
        g.add(self._switch_row("AutoEQ Preamp", "Apply the headphone profile's preamp (quieter, no clipping)",
                               self.settings.auto_eq_preamp_enabled, self.engine.set_auto_eq_preamp_enabled))
        g.add(self._switch_row("Volume Feedback", "Play a short sound when changing a device's volume",
                               a.volume_feedback_enabled, lambda v: self._update(volume_feedback_enabled=v)))
        page.add(g)

        s = Adw.PreferencesGroup(title="System Sounds")
        devices = self.engine.outputs
        labels = ["Follow Default Output"] + [d.name for d in devices]
        cur = self.settings.system_sounds_device_uid
        idx = 0
        if not self.settings.system_sounds_follows_default:
            for i, d in enumerate(devices):
                if d.uid == cur:
                    idx = i + 1
        self._sfx_devices = devices
        s.add(self._combo_row("Sound Effects", "Output device for alerts and notifications", labels, idx,
                              lambda i: self.engine.set_system_sounds_device(
                                  None if i == 0 else self._sfx_devices[i - 1].uid)))
        s.add(self._slider_row("Alert Volume", "Volume for alerts and notifications", a.alert_volume,
                               lambda v: self._update(alert_volume=v)))
        page.add(s)
        return page

    def _set_loudness(self, on):
        new = self.settings.app_settings.copy()
        new.set_unified_loudness_enabled(on)
        self.settings.update_app_settings(new)

    # ------------------------------------------------------------- Shortcuts

    def _shortcuts(self):
        exe = finetune_executable()
        a = self.settings.app_settings
        page = Adw.PreferencesPage()
        mk = Adw.PreferencesGroup(title="Media Keys &amp; HUD")
        mk.add(self._switch_row("Volume Keys Control FineTune",
                                "Volume keys drive the default output through FineTune's volume pipeline",
                                a.media_key_control_enabled, self._set_media_keys))
        mk.add(self._switch_row("Show HUD", "On-screen volume indicator", a.hud_enabled,
                                lambda v: self._update(hud_enabled=v)))
        mk.add(self._segmented_row("HUD Style", "How the volume indicator appears", HUD_STYLES, a.hud_style,
                                   lambda v: self._update(hud_style=v)))
        steps = list(VolumeHotkeyStep)
        mk.add(self._combo_row("Volume Step",
                               "How much each keypress changes the volume. Applies to media keys, hotkeys and arrow keys in the popup.",
                               [f"{s.label} ({s.detail})" for s in steps], steps.index(a.volume_hotkey_step),
                               lambda i: self._update(volume_hotkey_step=steps[i])))
        test = Adw.ActionRow(title="Preview HUD", subtitle="Show the HUD for the default output device")
        tb = Gtk.Button(label="Show", valign=Gtk.Align.CENTER)
        tb.connect("clicked", lambda *_: self.engine.default_output() and self.app.show_device_hud(self.engine.default_output()))
        test.add_suffix(tb)
        mk.add(test)
        page.add(mk)

        hk = Adw.PreferencesGroup(
            title="Global Hotkeys",
            description="Each action can be bound to a key in your compositor with the command shown "
                        "(copy button). Where the desktop supports the XDG GlobalShortcuts portal, the "
                        "trigger you type is also suggested to it.")
        self.portal_row = Adw.ActionRow(title="Desktop shortcuts portal")
        self.portal_icon = Gtk.Image(valign=Gtk.Align.CENTER)
        self.portal_row.add_prefix(self.portal_icon)
        hk.add(self.portal_row)
        self._refresh_portal_status()
        for act in ACTIONS + MEDIA_ACTIONS:
            cmd = f"{exe} --action {act.id}"
            row = Adw.EntryRow(title=act.display_name)
            row.set_tooltip_text(f"Suggested trigger, e.g. CTRL+ALT+Up · command: {cmd}")
            row.set_text(a.custom_shortcuts.get(act.id, act.default_trigger))
            row.set_show_apply_button(True)
            row.connect("apply", lambda r, aid=act.id: self._set_trigger(aid, r.get_text()))
            cp = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=f"Copy “{cmd}”", valign=Gtk.Align.CENTER)
            cp.add_css_class("flat")
            cp.connect("clicked", lambda b, c=cmd: (copy_to_clipboard(b, c),
                                                     self.toasts.add_toast(Adw.Toast(title=f"Copied “{c}”"))))
            row.add_suffix(cp)
            hk.add(row)
        page.add(hk)

        hy = Adw.PreferencesGroup(title="Hyprland / Sway example")
        snippet = (f"bind  = , XF86AudioMute, exec, {exe} --action volume-mute\n"
                   f"binde = , XF86AudioRaiseVolume, exec, {exe} --action volume-up\n"
                   f"binde = , XF86AudioLowerVolume, exec, {exe} --action volume-down\n"
                   f"binde = CTRL ALT, Up, exec, {exe} --action frontmostAppVolumeUp\n"
                   f"binde = CTRL ALT, Down, exec, {exe} --action frontmostAppVolumeDown\n"
                   f"bind  = CTRL ALT, M, exec, {exe} --action frontmostAppMuteToggle\n"
                   f"bind  = SUPER, A, exec, {exe} --action togglePopup")
        lbl = Gtk.Label(label=snippet, xalign=0, selectable=True, wrap=True)
        lbl.add_css_class("ft-mono")
        lbl.set_margin_top(8)
        lbl.set_margin_bottom(8)
        lbl.set_margin_start(12)
        lbl.set_margin_end(12)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("card")
        card.append(lbl)
        cpy = Gtk.Button(label="Copy", halign=Gtk.Align.END)
        cpy.add_css_class("flat")
        cpy.connect("clicked", lambda b: (copy_to_clipboard(b, snippet),
                                          self.toasts.add_toast(Adw.Toast(title="Copied"))))
        card.append(cpy)
        hy.add(card)
        page.add(hy)
        return page

    def _refresh_portal_status(self):
        status = self.portal.status if self.portal else "unavailable"
        if status == "active":
            text, icon = "Registered — shortcuts are managed by your desktop", "object-select-symbolic"
        elif status == "connecting":
            text, icon = "Connecting…", "content-loading-symbolic"
        else:
            text, icon = ("Not available on this desktop — bind the commands below in your compositor",
                          "dialog-information-symbolic")
        self.portal_row.set_subtitle(text)
        self.portal_icon.set_from_icon_name(icon)
        if status == "connecting":
            GLib.timeout_add_seconds(1, lambda: (self._refresh_portal_status(), False)[1])

    def _set_media_keys(self, v):
        self._update(media_key_control_enabled=v)
        if self.portal:
            self.portal._media_keys = v
            self.portal.bind()

    def _set_trigger(self, action_id, text):
        cs = dict(self.settings.app_settings.custom_shortcuts)
        if text.strip():
            cs[action_id] = text.strip()
        else:
            cs.pop(action_id, None)
        self._update(custom_shortcuts=cs)
        if self.portal:
            self.portal.bind(cs)
        self.toasts.add_toast(Adw.Toast(title="Shortcut saved"))

    # --------------------------------------------------------------- Updates

    def _updates(self):
        a = self.settings.app_settings
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Software Updates")
        g.add(self._switch_row("Automatic updates", "Check for new versions automatically",
                               a.check_for_updates_automatically,
                               lambda v: self._update(check_for_updates_automatically=v)))
        self.update_row = Adw.ActionRow(title=f"Version {VERSION}",
                                        subtitle=f"Last checked {_ago(a.last_update_check)}")
        btn = Gtk.Button(label="Check Now", valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: self.check_updates(manual=True))
        self.update_row.add_suffix(btn)
        g.add(self.update_row)
        page.add(g)
        return page

    def check_updates(self, manual=False):
        self.update_row.set_subtitle("Checking…")

        def done(res):
            self._update(last_update_check=time.time())
            if not res["ok"]:
                self.update_row.set_subtitle(f"Check failed: {res['error']}")
            elif res["newer"]:
                self.update_row.set_subtitle(f"FineTune {res['version']} is available")
                self.app.notify("Update available", f"FineTune {res['version']} is available.")
                if manual:
                    open_uri(res["url"])
            else:
                self.update_row.set_subtitle(f"Up to date · last checked {_ago(time.time())}")

        updates.check(done)

    # ----------------------------------------------------------------- About

    def _about(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER)
        img = Gtk.Image.new_from_icon_name(APP_ID)
        img.set_pixel_size(96)
        box.append(img)
        t = Gtk.Label(label="FineTune")
        t.add_css_class("title-1")
        box.append(t)
        v = Gtk.Label(label=f"Version {VERSION}")
        v.add_css_class("dim-label")
        box.append(v)
        d = Gtk.Label(label=f"Per-app volume, routing, EQ and headphone correction for Linux.\nCreated by {AUTHOR}.",
                      justify=Gtk.Justification.CENTER)
        box.append(d)
        links = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        for label, icon, url in (("Star on GitHub", "starred-symbolic", REPO_URL),
                                 ("Support FineTune", "emote-love-symbolic", DONATE_URL)):
            b = Gtk.Button()
            bb = Gtk.Box(spacing=6)
            bb.append(Gtk.Image.new_from_icon_name(icon))
            bb.append(Gtk.Label(label=label))
            b.set_child(bb)
            b.add_css_class("pill")
            b.connect("clicked", lambda *_, u=url: open_uri(u))
            links.append(b)
        box.append(links)
        c = Gtk.Label(label=f"© 2026 {AUTHOR} · GPL-3.0")
        c.add_css_class("dim-label")
        box.append(c)
        o = Gtk.Label(label=f"Based on {ORIGINAL_PROJECT}")
        o.add_css_class("dim-label")
        o.add_css_class("caption")
        box.append(o)
        g.add(box)
        page.add(g)
        return page
