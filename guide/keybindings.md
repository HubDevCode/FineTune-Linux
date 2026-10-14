# Keybindings

On Wayland, apps cannot grab global keys, so the compositor binds the keys to FineTune's actions. The running instance receives the command over D-Bus instantly.

| Action | What it does |
|---|---|
| `volume-up` / `volume-down` | Default output volume, one step, through FineTune's volume pipeline (works on hardware, DDC and software-volume devices), with the HUD |
| `volume-mute` | Toggle mute on the default output |
| `frontmostAppVolumeUp` / `frontmostAppVolumeDown` | Volume of the app that is making sound (the focused one wins); volume-up also unmutes |
| `frontmostAppMuteToggle` | Mute or unmute that app |
| `togglePopup` | Open or close the popup |

The step size comes from Settings → Shortcuts → Volume Step. The **Volume Keys Control FineTune** switch enables the three `volume-*` actions.

> **Use the full path.** Hyprland and Sway run `exec` binds with their own `PATH`, which usually
> doesn't include `~/.local/bin`. If you installed to `~/.local`, write `~/.local/bin/finetune`
> in your binds (as below). A bare `finetune` silently does nothing.
> Settings → Shortcuts shows a ready-to-copy snippet with the right path.

## Hyprland

```ini
bind  = , XF86AudioMute,        exec, ~/.local/bin/finetune --action volume-mute
binde = , XF86AudioRaiseVolume, exec, ~/.local/bin/finetune --action volume-up
binde = , XF86AudioLowerVolume, exec, ~/.local/bin/finetune --action volume-down
binde = CTRL ALT, Up,   exec, ~/.local/bin/finetune --action frontmostAppVolumeUp
binde = CTRL ALT, Down, exec, ~/.local/bin/finetune --action frontmostAppVolumeDown
bind  = CTRL ALT, M,    exec, ~/.local/bin/finetune --action frontmostAppMuteToggle
bind  = SUPER, V,       exec, ~/.local/bin/finetune --action togglePopup
exec-once = ~/.local/bin/finetune --background
```

`binde` repeats while the key is held. Remove any existing `wpctl`/`pactl` bindings for the same keys.

Alternatively, once FineTune is installed with `scripts/install.sh`, it registers its actions with the
GlobalShortcuts portal (xdg-desktop-portal-hyprland), and you can bind them as Hyprland `global` shortcuts:

```ini
bind = SUPER, V,        global, io.github.hubdevcode.FineTune:togglePopup
bind = CTRL ALT, Up,    global, io.github.hubdevcode.FineTune:frontmostAppVolumeUp
bind = CTRL ALT, Down,  global, io.github.hubdevcode.FineTune:frontmostAppVolumeDown
bind = CTRL ALT, M,     global, io.github.hubdevcode.FineTune:frontmostAppMuteToggle
```

`hyprctl globalshortcuts` lists what FineTune registered. Settings → Shortcuts shows whether the portal is active.

## Sway

```ini
bindsym XF86AudioRaiseVolume exec ~/.local/bin/finetune --action volume-up
bindsym XF86AudioLowerVolume exec ~/.local/bin/finetune --action volume-down
bindsym XF86AudioMute        exec ~/.local/bin/finetune --action volume-mute
bindsym Ctrl+Alt+Up   exec ~/.local/bin/finetune --action frontmostAppVolumeUp
bindsym Ctrl+Alt+Down exec ~/.local/bin/finetune --action frontmostAppVolumeDown
exec ~/.local/bin/finetune --background
```

## KDE Plasma / GNOME

FineTune registers its actions with the XDG GlobalShortcuts portal. The triggers you type in Settings → Shortcuts are suggested to the desktop, which asks for confirmation. Alternatively, add custom shortcuts in System Settings that run the commands above.
