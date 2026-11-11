"""Looking for a newer release on GitHub, without GTK.

Only the AppImage looks: it has a fixed version, while a checkout from git
is updated with git. Nothing here runs unless the user agreed to it
(setting "update_check"). The request asks GitHub for the newest release
only and sends nothing about the user.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request

from . import releases

LATEST_URL = "https://api.github.com/repos/JamDaBam/Understeer/releases/latest"
RELEASES_URL = "https://github.com/JamDaBam/Understeer/releases"
CHECK_EVERY = 3600              # at most one look an hour, also over restarts
USER_AGENT = "Understeer"

log = logging.getLogger("understeer.updates")


class Error(Exception):
    """GitHub could not be asked, or answered something unexpected."""


def can_check(version, environ=os.environ):
    """Whether this Understeer looks for updates: a released AppImage."""
    return bool(environ.get("APPIMAGE")) and releases.has_notes(version)


def due(last_checked, now=None):
    """Whether it is time to look again; last_checked: a time.time()."""
    now = time.time() if now is None else now
    try:
        last_checked = float(last_checked)
    except (TypeError, ValueError):
        return True
    return not 0 <= now - last_checked < CHECK_EVERY


def parse_latest(data):
    """{"version": "1.10.0", "url": "https://…"} from GitHub's answer."""
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        raise Error("no release in the answer")
    url = data.get("html_url")
    if not isinstance(url, str) or not url.startswith(RELEASES_URL + "/"):
        url = RELEASES_URL                 # only ever open our own page
    return {"version": releases.plain_version(data["tag_name"]), "url": url}


def fetch_latest(timeout=10):
    """The newest release on GitHub; raises Error."""
    req = urllib.request.Request(LATEST_URL, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read(256 * 1024))
    except (OSError, ValueError) as e:      # URLError is an OSError
        raise Error(str(e)) from e
    return parse_latest(data)


def is_newer(latest, current):
    """Whether version latest is newer than current, e.g. "1.10.0" > "1.9.5"."""
    new, old = releases.number(latest), releases.number(current)
    return new is not None and old is not None and new > old
