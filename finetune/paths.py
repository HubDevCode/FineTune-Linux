"""Filesystem locations (XDG equivalents of macOS Application Support)."""
import os
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "FineTune"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "FineTune"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "FineTune"


def autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "autostart"


def package_root() -> Path:
    return Path(__file__).resolve().parent


def project_root() -> Path:
    return package_root().parent


def resource_dir() -> Path:
    """Directory with icons and stylesheets (installed or source checkout)."""
    # source checkout: <root>/data ; installed: <prefix>/share/finetune (package lives inside it)
    for cand in (project_root() / "data", project_root(), Path("/usr/share/finetune"),
                 Path("/usr/local/share/finetune")):
        if (cand / "icons").is_dir():
            return cand
    return project_root() / "data"


def engine_binary() -> str:
    env = os.environ.get("FINETUNE_ENGINE")
    if env:
        return env
    for cand in (
        project_root() / "engine" / "finetune-engine",
        project_root().parent.parent / "lib" / "finetune" / "finetune-engine",
        Path("/usr/lib/finetune/finetune-engine"),
        Path("/usr/local/lib/finetune/finetune-engine"),
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return "finetune-engine"
