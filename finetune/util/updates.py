"""Update checks against GitHub releases (replaces Sparkle / appcast.xml)."""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request

from gi.repository import GLib

from finetune import REPO_URL, VERSION

log = logging.getLogger("finetune.updates")

API = REPO_URL.replace("https://github.com/", "https://api.github.com/repos/") + "/releases/latest"


def _vtuple(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def is_newer(remote: str, local: str = VERSION) -> bool:
    return _vtuple(remote) > _vtuple(local)


def check(callback):
    """callback(result: dict) on the main loop: {"ok", "version", "url", "newer", "error"}."""
    def work():
        res = {"ok": False, "version": None, "url": REPO_URL + "/releases", "newer": False, "error": None}
        try:
            req = urllib.request.Request(API, headers={"User-Agent": "FineTune-Linux",
                                                       "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode())
            tag = d.get("tag_name") or d.get("name") or ""
            res.update(ok=True, version=tag.lstrip("v"), url=d.get("html_url") or res["url"],
                       newer=is_newer(tag))
        except Exception as e:  # noqa: BLE001
            res["error"] = str(e)
            log.info("Update check failed: %s", e)
        GLib.idle_add(lambda: (callback(res), False)[1])

    threading.Thread(target=work, name="update-check", daemon=True).start()
