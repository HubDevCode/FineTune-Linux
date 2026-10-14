"""Launch at login through an XDG autostart entry (replaces SMAppService)."""
from __future__ import annotations

import logging
import shutil
import sys

from finetune import APP_ID, paths

log = logging.getLogger("finetune.autostart")


def _entry_path():
    return paths.autostart_dir() / f"{APP_ID}.desktop"


def _exec_line() -> str:
    exe = shutil.which("finetune")
    if exe:
        return f"{exe} --background"
    return f"{sys.executable} -m finetune --background"


def is_enabled() -> bool:
    return _entry_path().exists()


def set_enabled(enabled: bool):
    p = _entry_path()
    try:
        if enabled:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=FineTune\n"
                "Comment=Per-app volume control, routing and EQ\n"
                f"Exec={_exec_line()}\n"
                f"Icon={APP_ID}\n"
                "X-GNOME-Autostart-enabled=true\n"
                "NoDisplay=false\n"
            )
        elif p.exists():
            p.unlink()
    except OSError as e:
        log.error("Failed to update autostart entry: %s", e)
