# URL Schemes & Command Line

Control FineTune from a terminal, shell scripts, launchers (rofi, wofi, fuzzel, Ulauncher), or compositor keybindings. `scripts/install.sh` registers `finetune://` as a URL handler, so `xdg-open` works. You can also pass a URL straight to `finetune`.

## Actions

| Action | Format | Description |
|--------|--------|-------------|
| Set volume | `finetune://set-volumes?app=APP&volume=PERCENT` | 0–100; 101–400 also sets the matching boost (2x/3x/4x) |
| Step volume | `finetune://step-volume?app=APP&direction=up` | Nudge by 5% of the slider |
| Set mute | `finetune://set-mute?app=APP&muted=true` | Mute or unmute |
| Toggle mute | `finetune://toggle-mute?app=APP` | Toggle mute state |
| Set device | `finetune://set-device?app=APP&device=DEVICE_UID` | Route an app; `device=default` follows the system default |
| Reset | `finetune://reset` | Reset all apps (or the listed `app=`s) to 100% and unmuted |

`set-volumes`, `set-mute` and `toggle-mute` accept several `app=` entries in one URL.

## Examples

```bash
xdg-open "finetune://set-volumes?app=spotify&volume=50"
finetune "finetune://set-volumes?app=spotify&volume=80&app=discord&volume=40"
finetune "finetune://set-mute?app=spotify&muted=true&app=firefox&muted=true"
finetune "finetune://step-volume?app=discord&direction=down"
finetune "finetune://set-device?app=spotify&device=bluez_output.AA_BB_CC_DD_EE_FF.1"
finetune "finetune://reset"
```

## Finding app identifiers

`APP` matches, case-insensitively, any of:
- the identifier FineTune uses (the `.desktop` id, e.g. `firefox`, `com.spotify.Client`)
- the process binary name, e.g. `spotify`
- the name shown in the popup

FineTune also accepts the Flatpak app id. List the apps playing now with:

```bash
pactl -f json list sink-inputs | python3 -c 'import json,sys;[print(s["properties"].get("application.process.binary"), "|", s["properties"].get("application.name")) for s in json.load(sys.stdin)]'
```

## Finding device UIDs

Device UIDs are PipeWire node names. In FineTune, enter edit mode (pencil, or Ctrl+E), click the ⓘ button on a device, then copy its UID. From a terminal: `pactl list short sinks`.

## Other command-line options

```
finetune --toggle          show/hide the popup
finetune --settings        open settings
finetune --action NAME     run an action (see guide/keybindings.md)
finetune --background      start without opening the popup
finetune --quit            quit (apps go back to their devices)
finetune --debug           verbose logging
```
