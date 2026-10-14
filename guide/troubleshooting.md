# Troubleshooting

## "Audio engine" banner in the popup

The C engine isn't running. Build it with `make -C engine` (needs the `libpipewire-0.3` development package) or reinstall with `scripts/install.sh`. You can point FineTune at a custom build with `FINETUNE_ENGINE=/path/to/finetune-engine`.

## A keybinding does nothing

Your compositor can't find `finetune` because it runs binds with a `PATH` that doesn't include `~/.local/bin`. Use the full path: `bind = SUPER, A, exec, ~/.local/bin/finetune --action togglePopup`. To test, run `hyprctl dispatch exec "finetune --version"` and check whether it reports "command not found".

## No devices / no apps

FineTune talks to PipeWire through `pactl` (pipewire-pulse). Check that `pactl info` reports "PulseAudio (on PipeWire …)".

## App not appearing

FineTune lists apps that currently have a playback stream. Start playback. Some apps (browsers) only open a stream while sound plays. Pin an app in edit mode to keep it visible. If an app shows up in edit mode with a crossed eye, it is ignored: click the eye to bring it back.

## App causing audio issues

Pro-audio tools, JACK clients, OBS, and VoIP apps with their own echo cancellation may prefer their native routing. Ignore them in edit mode (eye icon). FineTune then moves their streams back to the normal device and never touches them again.

## Volume slider not working

**Per-app slider:** some apps play through a helper process with a different name. Its row appears under that name.

**Per-device slider:** FineTune picks a backend automatically:
- **Hardware**: the PipeWire/ALSA mixer volume (PipeWire falls back to its own soft mixer when the hardware has none)
- **DDC**: monitor speakers through `ddcutil`. Load `i2c-dev` and add yourself to the `i2c` group.
- **Software**: FineTune attenuates inside its own audio graph

If a device's hardware control doesn't change the level (some HDMI/USB devices), open the inspector (ⓘ in edit mode) and turn on **Use FineTune's software volume**.

## Audio device not switching automatically

The device priority list decides. In edit mode, drag devices (or type a number) so the device you want is #1. When a higher-priority device connects, FineTune makes it the default. If WirePlumber switches to a lower-priority device on its own, FineTune switches back within a few seconds (longer for Bluetooth). Input and output have separate lists.

## Popup appears in the middle of the screen

Install `gtk4-layer-shell` so the popup can be anchored to the top-right corner on Wayland compositors (Hyprland, Sway, KDE). Without it, FineTune uses a normal window; position it with a window rule, e.g. Hyprland: `windowrulev2 = float, title:^(FineTune)$`.

## No tray icon

Your bar needs a StatusNotifierItem host:
- Waybar: `tray` module
- GNOME: the AppIndicator extension
- KDE / swaybar: built in

Without a tray, use `finetune --toggle` or a keybinding.

## Global shortcuts portal says "unavailable"

The GlobalShortcuts portal needs the `.desktop` file installed with `scripts/install.sh`, because the portal only accepts apps it can find. It also needs a portal backend that implements it. If you installed before, run the installer again and restart FineTune. Bind the `finetune --action …` commands in your compositor instead (see [keybindings.md](keybindings.md)).

## EQ not applying / sounds the same

- Check that the EQ switch in the panel header is on and that the preset isn't **Flat**
- EQ is per app: open the panel on the app you are listening to
- AutoEQ is separate and per device (wand icon)

## Audio quality sounds degraded

Large boosts (3x/4x) or big EQ boosts push the soft limiter. Reduce the boost, or pull bands down instead of up. AutoEQ profiles include a preamp cut for this reason.

## Reset everything

Run `finetune "finetune://reset"` to set all apps back to 100% and unmuted. Settings → General → Reset All Settings clears everything. The settings file is `~/.config/FineTune/settings.json`.
