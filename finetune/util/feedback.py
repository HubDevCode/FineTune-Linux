"""Volume-change feedback sound (port of VolumeFeedbackPlayer)."""
from __future__ import annotations

import shutil
import time

from gi.repository import GLib

COOLDOWN_S = 0.12
_last = 0.0


def play(device_uid: str | None = None):
    global _last
    now = time.monotonic()
    if now - _last < COOLDOWN_S:
        return
    _last = now
    if shutil.which("canberra-gtk-play"):
        argv = ["canberra-gtk-play", "-i", "audio-volume-change", "-d", "FineTune volume feedback"]
    elif shutil.which("pw-play") and GLib.file_test("/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga",
                                                     GLib.FileTest.EXISTS):
        argv = ["pw-play", "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga"]
        if device_uid:
            argv[1:1] = ["--target", device_uid]
    else:
        return
    try:
        GLib.spawn_async(argv, flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.STDOUT_TO_DEV_NULL
                         | GLib.SpawnFlags.STDERR_TO_DEV_NULL)
    except GLib.Error:
        pass
