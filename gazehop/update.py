"""Is there a newer gazehop? Asks GitHub for the latest release, once per start.

Sends nothing about the user: one plain HTTPS request, the same for everyone. Turned off with
`check_updates` in the settings.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from . import __version__

RELEASES = "https://github.com/jamiepsm/gazehop/releases"
API = "https://api.github.com/repos/jamiepsm/gazehop/releases/latest"


def parse(version: str) -> tuple | None:
    """"v0.3.1" -> (0, 3, 1); None for anything else (a pre-release tag, a typo)."""
    try:
        return tuple(int(p) for p in version.strip().lstrip("v").split("."))
    except (AttributeError, ValueError):
        return None


def newer(release: dict, current: str = __version__) -> tuple[str, str] | None:
    """(version, page) if `release` (GitHub's JSON for one release) is newer than `current`."""
    have, got = parse(current), parse(release.get("tag_name", ""))
    page = release.get("html_url", "")
    if not (have and got and got > have) or not page.startswith(RELEASES + "/"):
        return None     # also refuses a page outside this project's releases, whatever the reply says
    return release["tag_name"].lstrip("v"), page


def check() -> tuple[str, str] | None:
    """Blocking (a few seconds at most): run it off the main thread. None when up to date or offline."""
    req = urllib.request.Request(API, headers={"User-Agent": f"gazehop/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return newer(json.load(r))
    except Exception:
        return None


def how_to_update() -> str:
    """The app bundle is replaced by downloading it again; a copy of the source is updated with git."""
    if getattr(sys, "frozen", False):
        return "Download it from the release page and replace this copy."
    if os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".git")):
        return "Update with:  git pull, then run the installer again."
    return "Download it from the release page."
