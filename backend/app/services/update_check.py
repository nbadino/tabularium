"""Is a newer Tabularium release out?

One GET to GitHub's "latest release" endpoint, at most every few hours, from the
server (never from the browser: the page must not call third parties). Any failure
(offline machine, rate limit, proxy) is quiet: the answer is "unknown", never an
error in the interface. ``TABULARIUM_UPDATE_CHECK=0`` turns the check off, for an
air-gapped or privacy-strict install.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request

from .. import config

RELEASES_URL = "https://api.github.com/repos/nbadino/tabularium/releases/latest"
UPGRADE_GUIDE_URL = "https://nbadino.github.io/tabularium/guide/upgrading/"
_TTL_OK = 6 * 3600.0  # a release a day at most: four checks a day are plenty
_TTL_FAIL = 30 * 60.0  # retry sooner after a failure, without hammering
_TIMEOUT = 5.0
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


def enabled() -> bool:
    return os.environ.get("TABULARIUM_UPDATE_CHECK", "1").strip().lower() not in {"0", "false", "no", "off"}


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    match = _SEMVER.fullmatch((text or "").strip())
    return tuple(map(int, match.groups())) if match else None  # type: ignore[return-value]


def _fetch_latest() -> dict:
    request = urllib.request.Request(
        RELEASES_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"tabularium/{config.VERSION} (update check)",
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310 - fixed https URL
        data = json.loads(response.read().decode("utf-8"))
    return {
        "latest": str(data.get("tag_name") or "").lstrip("v") or None,
        "url": data.get("html_url") or None,
        "published_at": data.get("published_at") or None,
    }


def status(*, force: bool = False) -> dict:
    """Current version, latest release and whether it is newer (``None`` = unknown)."""
    global _cache
    current = config.VERSION
    base = {
        "enabled": enabled(),
        "current": current,
        "latest": None,
        "update_available": None,
        "url": None,
        "published_at": None,
        "guide_url": UPGRADE_GUIDE_URL,
        "checked_at": None,
        "error": None,
    }
    if not base["enabled"]:
        return base
    now = time.time()
    with _lock:
        if _cache is not None and not force:
            stamp, cached = _cache
            ttl = _TTL_FAIL if cached.get("error") else _TTL_OK
            if now - stamp < ttl:
                return {**base, **cached}
        try:
            found = _fetch_latest()
            found["error"] = None
        except Exception as exc:  # noqa: BLE001 - any failure means "unknown"
            found = {"error": type(exc).__name__}
        found["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        latest, mine = parse_version(found.get("latest")), parse_version(current)
        found["update_available"] = (latest > mine) if latest and mine else None
        _cache = (now, found)
        return {**base, **found}


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None
