"""FineTune application (port of FineTuneApp.swift).

Single instance: every `finetune …` invocation is forwarded over D-Bus to the
running instance, so the CLI doubles as the control interface:

    finetune                      start (or toggle the popup if already running)
    finetune --background         start without opening the popup (autostart)
    finetune --toggle | --settings | --quit
    finetune --action ACTION      volume-up, volume-down, volume-mute, togglePopup,
                                  frontmostAppVolumeUp, frontmostAppVolumeDown, frontmostAppMuteToggle
    finetune "finetune://set-volumes?app=firefox&volume=40"
"""
from __future__ import annotations

import glob
import logging
import os
import sys
import time


def ensure_layer_shell_preload(argv):
    """gtk4-layer-shell must be linked before libwayland-client: re-exec with LD_PRELOAD."""
    if os.environ.get("FINETUNE_NO_LAYER_SHELL") or os.environ.get("_FINETUNE_PRELOADED"):
        return
    if not os.environ.get("WAYLAND_DISPLAY"):
        return
    libs = sorted(glob.glob("/usr/lib*/libgtk4-layer-shell.so*") + glob.glob("/usr/lib/*/libgtk4-layer-shell.so*"))
    if not libs:
        return
    pre = os.environ.get("LD_PRELOAD", "")
    if "gtk4-layer-shell" in pre:
        return
    env = dict(os.environ)
    env["LD_PRELOAD"] = (libs[0] + (" " + pre if pre else "")).strip()
    env["_FINETUNE_PRELOADED"] = "1"
    os.execve(sys.executable, [sys.executable, "-m", "finetune", *argv[1:]], env)


import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from finetune import APP_ID, VERSION, paths  # noqa: E402

log = logging.getLogger("finetune")


class FineTuneApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.engine = None
        self.popup = None
        self.hud = None
        self.tray = None
        self.portal = None
        self.settings_window = None
        self.url_handler = None
        self.resolver = None
        self._started = False
        self.add_main_option("background", ord("b"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start without showing the popup", None)
        self.add_main_option("toggle", ord("t"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Toggle the popup", None)
        self.add_main_option("settings", ord("s"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Open settings", None)
        self.add_main_option("page", ord("p"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Settings page to open (general, audio, shortcuts, updates, about)", "PAGE")
        self.add_main_option("quit", ord("q"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Quit FineTune", None)
        self.add_main_option("action", ord("a"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Run an action (volume-up, frontmostAppVolumeUp, …)", "ACTION")
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Print version", None)
        self.add_main_option("debug", ord("d"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Verbose logging", None)
        self.set_option_context_parameter_string("[finetune://URL]")

    # ------------------------------------------------------------- startup

    def do_handle_local_options(self, options):
        if options.contains("version"):
            print(f"FineTune {VERSION}")
            return 0
        logging.basicConfig(level=logging.DEBUG if options.contains("debug") else logging.INFO,
                            format="%(asctime)s %(name)s %(levelname)s: %(message)s")
        return -1

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()  # menu-bar app: keep running without windows
        from finetune.audio.engine import AudioEngine
        from finetune.control.shortcuts import GlobalShortcutsPortal
        from finetune.control.target_app import TargetAppResolver
        from finetune.control.url_handler import URLHandler
        from finetune.settings import SettingsManager
        from finetune.ui.hud import HUDWindow
        from finetune.ui.popup import PopupWindow
        from finetune.ui.tray import TrayIcon

        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display)
        res = paths.resource_dir()
        for sub in ("icons", "icons/hicolor/symbolic/apps", "icons/hicolor/scalable/apps"):
            theme.add_search_path(str(res / sub))
        css = Gtk.CssProvider()
        css.load_from_path(str(res / "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.Window.set_default_icon_name(APP_ID)

        settings = SettingsManager(scheduler=lambda ms, fn: GLib.timeout_add(ms, fn))
        self.engine = AudioEngine(settings, notify=self.notify)
        self._apply_appearance()
        settings.connect_app_settings_changed(lambda o, n: self._apply_appearance())
        self.url_handler = URLHandler(self.engine)
        self.resolver = TargetAppResolver()
        self.popup = PopupWindow(self, self.engine)
        self.hud = HUDWindow(self, self.engine)
        self.tray = TrayIcon(self, self.engine)
        self.portal = GlobalShortcutsPortal(APP_ID, self.dispatch_action)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit_app())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def _start_services(self):
        if self._started:
            return
        self._started = True
        self.engine.start()
        self.tray.start()
        a = self.engine.settings.app_settings
        self.portal.start(a.custom_shortcuts, a.media_key_control_enabled)
        if a.check_for_updates_automatically and time.time() - a.last_update_check > 24 * 3600:
            GLib.timeout_add_seconds(20, lambda: (self.check_updates_background(), False)[1])
        # If no tray host shows up, make sure the user can still reach FineTune.
        GLib.timeout_add_seconds(4, self._tray_fallback)

    def _tray_fallback(self):
        if not self.tray.registered:
            log.warning("No StatusNotifierWatcher (system tray) found. Use `finetune --toggle` or a keybinding "
                        "to open the popup.")
        return False

    def do_command_line(self, cmdline):
        od = cmdline.get_options_dict()
        opts = {}
        for key in ("background", "toggle", "settings", "quit"):
            if od.contains(key):
                opts[key] = True
        pv = od.lookup_value("page", GLib.VariantType("s"))
        if pv is not None:
            opts["settings"] = pv.get_string()
        av = od.lookup_value("action", GLib.VariantType("s"))
        if av is not None:
            opts["action"] = av.get_string()
        args = cmdline.get_arguments()[1:]
        first_run = not self._started
        self._start_services()
        if opts.get("quit"):
            self.quit_app()
            return 0
        urls = [a for a in args if a.lower().startswith("finetune:")]
        for u in urls:
            if self.engine.pulse.is_ready():
                self.url_handler.handle(u)
            else:
                self._defer_until_ready(lambda u=u: self.url_handler.handle(u))
        if opts.get("action"):
            act = opts["action"]
            if self.engine.pulse.is_ready():
                self.dispatch_action(act)
            else:
                self._defer_until_ready(lambda: self.dispatch_action(act))
        if opts.get("settings"):
            self.show_settings(opts["settings"] if isinstance(opts["settings"], str) else None)
        if opts.get("toggle"):
            self.toggle_popup()
        if not (urls or opts.get("action") or opts.get("settings") or opts.get("toggle") or opts.get("background")):
            if first_run:
                GLib.timeout_add(400, lambda: (self.toggle_popup(force_open=True), False)[1])
            else:
                self.toggle_popup()
        return 0

    def _defer_until_ready(self, fn, tries=50):
        def tick():
            nonlocal tries
            tries -= 1
            if self.engine.pulse.is_ready() or tries <= 0:
                GLib.timeout_add(150, lambda: (fn(), False)[1])
                return False
            return True
        GLib.timeout_add(100, tick)

    def do_activate(self):
        self._start_services()
        self.toggle_popup(force_open=True)

    # ------------------------------------------------------------- UI glue

    def _apply_appearance(self):
        sm = Adw.StyleManager.get_default()
        ap = self.engine.settings.app_settings.appearance
        sm.set_color_scheme({"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}
                            .get(ap, Adw.ColorScheme.DEFAULT))
        if self.tray:
            self.tray.update(force=True)

    def toggle_popup(self, force_open=False):
        if force_open and self.popup.get_visible():
            self.popup.present()
            return
        self.popup.toggle()

    def show_settings(self, page: str | None = None):
        from finetune.ui.settings_window import SettingsWindow
        if self.popup and self.popup.get_visible():
            self.popup.close_popup()
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self, self.engine, self.portal)
        if page:
            self.settings_window.stack.set_visible_child_name(page)
        self.settings_window.present()

    def check_updates_background(self):
        from finetune.ui.settings_window import SettingsWindow
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self, self.engine, self.portal)
        self.settings_window.check_updates()

    def notify(self, title: str, body: str):
        n = Gio.Notification.new(title)
        n.set_body(body)
        n.set_icon(Gio.ThemedIcon.new(APP_ID))
        try:
            self.send_notification(None, n)
        except GLib.Error:
            pass

    def show_device_hud(self, dev):
        self.hud.show_device(dev, self.engine.device_volume(dev), self.engine.device_muted(dev))

    # ------------------------------------------------------------- actions

    def media_action(self, action: str):
        e = self.engine
        dev = e.default_output()
        if dev is None:
            return
        step = e.settings.app_settings.volume_hotkey_step.slider_delta
        if action == "volume-up":
            e.step_device_volume(dev, +1, step)
        elif action == "volume-down":
            e.step_device_volume(dev, -1, step)
        elif action == "volume-mute":
            e.toggle_device_mute(dev)
        if e.settings.app_settings.volume_feedback_enabled and action != "volume-mute":
            from finetune.util import feedback
            feedback.play(dev.uid)
        self.show_device_hud(dev)

    def dispatch_action(self, action: str):
        """ShortcutsRegistry.dispatch port + media-key actions."""
        from finetune.models.volume import gain_to_slider
        e = self.engine
        if action in ("volume-up", "volume-down", "volume-mute"):
            if e.settings.app_settings.media_key_control_enabled:
                self.media_action(action)
            return
        if action == "togglePopup":
            self.toggle_popup()
            return
        if action in ("frontmostAppVolumeUp", "frontmostAppVolumeDown", "frontmostAppMuteToggle"):
            ident = self.resolver.resolve(e)
            app = e.apps.get(ident) if ident else None
            if app is None or e.settings.is_ignored(ident):
                self.hud.show_not_controlled(app.name if app else None)
                return
            step = e.settings.app_settings.volume_hotkey_step.slider_delta
            if action == "frontmostAppMuteToggle":
                e.toggle_mute(ident)
            else:
                direction = 1 if action.endswith("Up") else -1
                slider = gain_to_slider(e.get_volume(ident))
                nxt = max(0.0, min(1.0, slider + direction * step))
                muted = e.get_mute(ident)
                if direction > 0 and muted:
                    e.set_mute(ident, False)
                elif direction < 0:
                    if muted and nxt > 0.001:
                        e.set_mute(ident, False)
                    elif not muted and nxt <= 0.001:
                        e.set_mute(ident, True)
                e.step_app_volume(ident, direction, step)
            self.hud.show_app(ident, app.name, app.icon, gain_to_slider(e.get_volume(ident)), e.get_mute(ident))
            return
        log.warning("Unknown action: %s", action)

    def quit_app(self):
        log.info("Quitting")
        if self.engine:
            self.engine.shutdown()
        if self.tray:
            self.tray.stop()
        self.release()
        GLib.timeout_add(300, lambda: (self.quit(), False)[1])


def main(argv=None):
    argv = argv or sys.argv
    ensure_layer_shell_preload(argv)
    app = FineTuneApplication()
    return app.run(argv)
