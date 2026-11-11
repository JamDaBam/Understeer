"""Looking for a newer release on GitHub and updating the AppImage,
without GTK.

Only the AppImage looks: it has a fixed version, while a checkout from git
is updated with git. Nothing here runs unless the user agreed to it
(setting "update_check"). The request asks GitHub for the newest release
only and sends nothing about the user.

Updating downloads the new AppImage next to the running one, checks it
against the SHA-256 that GitHub gives for it, and only then puts it in
place of the old file. The running Understeer goes on from its mounted
image until it is restarted.
"""
import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import time
import urllib.error
import urllib.request

from . import USER_AGENT, releases

LATEST_URL = "https://api.github.com/repos/JamDaBam/Understeer/releases/latest"
RELEASES_URL = "https://github.com/JamDaBam/Understeer/releases"
DOWNLOAD_URL = RELEASES_URL + "/download/"
MAX_DOWNLOAD = 200 * 1024 * 1024        # far more than an AppImage needs
CHECK_EVERY = 3600              # at most one look an hour, also over restarts

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


def _asset(data, version, machine):
    """{"url", "sha256", "size"} of the AppImage for this machine in a
    release, or None; only files of our own releases, with a checksum."""
    name = f"Understeer-{version}-{machine}.AppImage"
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        url, digest = asset.get("browser_download_url"), asset.get("digest")
        if (isinstance(url, str) and url.startswith(DOWNLOAD_URL)
                and isinstance(digest, str)
                and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)):
            return {"url": url, "sha256": digest[7:],
                    "size": asset.get("size") if isinstance(asset.get("size"), int)
                    else None}
    return None


def parse_latest(data, machine=None):
    """{"version": "1.10.0", "url": "https://…", "asset": {…} or None}
    from GitHub's answer."""
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        raise Error("no release in the answer")
    url = data.get("html_url")
    if not isinstance(url, str) or not url.startswith(RELEASES_URL + "/"):
        url = RELEASES_URL                 # only ever open our own page
    version = releases.plain_version(data["tag_name"])
    return {"version": version, "url": url,
            "asset": _asset(data, version, machine or platform.machine())}


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


def can_install(appimage):
    """Whether the AppImage file can be replaced: its folder is writable."""
    if not appimage or not os.path.isfile(appimage):
        return False
    return os.access(os.path.dirname(os.path.abspath(appimage)), os.W_OK)


def download(asset, appimage, timeout=30, progress=None):
    """Download the new AppImage next to appimage and check its SHA-256.
    Returns the path of the checked file; raises Error."""
    folder = os.path.dirname(os.path.abspath(appimage))
    part = os.path.join(folder, ".Understeer-update.AppImage.part")
    req = urllib.request.Request(asset["url"], headers={"User-Agent": USER_AGENT})
    digest, size = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, \
                open(part, "wb") as out:
            while chunk := resp.read(64 * 1024):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise Error("the download is far too big")
                digest.update(chunk)
                out.write(chunk)
                if progress and asset.get("size"):
                    progress(min(size / asset["size"], 1.0))
    except (OSError, ValueError) as e:
        _remove(part)
        raise Error(str(e)) from e
    except Error:
        _remove(part)
        raise
    if digest.hexdigest() != asset["sha256"]:
        _remove(part)
        raise Error("the checksum of the download is wrong")
    return part


def install(new_file, appimage):
    """Put the checked new_file in place of appimage, in one step."""
    try:
        os.chmod(new_file, 0o755)
        os.replace(new_file, appimage)
    except OSError as e:
        _remove(new_file)
        raise Error(str(e)) from e
    log.info("Updated %s", appimage)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def restart_command(appimage, pid, environ=os.environ):
    """(argv, env) that start appimage again once process pid has ended:
    Understeer runs only once, so the new one must wait for the old one."""
    env = {k: v for k, v in environ.items()
           if k not in ("APPDIR", "APPIMAGE", "ARGV0", "OWD")}
    old = environ.get("APPDIR")
    if old and env.get("PYTHONPATH"):
        kept = [p for p in env["PYTHONPATH"].split(os.pathsep)
                if p and not p.startswith(old)]
        if kept:
            env["PYTHONPATH"] = os.pathsep.join(kept)
        else:
            del env["PYTHONPATH"]
    script = 'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; exec "$2"'
    return ["sh", "-c", script, "sh", str(pid), appimage], env


def restart(appimage):
    """Start appimage again after this process has quit."""
    argv, env = restart_command(appimage, os.getpid())
    # From the home folder, like from the desktop, not from where this one
    # was started
    subprocess.Popen(argv, env=env, start_new_session=True,
                     cwd=os.path.expanduser("~"),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
