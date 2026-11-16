"""Looking for a newer release on GitHub and updating the AppImage,
without GTK.

Only the AppImage looks: it has a fixed version, while a checkout from git
is updated with git. Nothing here runs unless the user agreed to it
(setting "update_check"). The request asks GitHub for the newest release
only and sends nothing about the user.

Test versions (betas) are never offered by that look: fetch_beta() finds
the newest one apart from it, only tells its version and page, and is
not called by the app yet.

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

from . import USER_AGENT, fetch, releases

LATEST_URL = "https://api.github.com/repos/JamDaBam/Understeer/releases/latest"
RELEASES_URL = "https://github.com/JamDaBam/Understeer/releases"
DOWNLOAD_URL = RELEASES_URL + "/download/"
MAX_DOWNLOAD = 200 * 1024 * 1024        # far more than an AppImage needs
CHECK_EVERY = 3600              # at most one look an hour, also over restarts
# The tag of a stable release, e.g. v2.3.3: test versions like v2.4.0-beta.1
# and anything else are never offered. ASCII digits only, as int() would
# also read other digits.
STABLE_TAG = re.compile(r"v[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")

# Test versions: GitHub's list of the newest releases, asked only when
# fetch_beta() is called. One page: see newest_beta().
BETAS_PER_PAGE = 10
BETAS_URL = ("https://api.github.com/repos/JamDaBam/Understeer/releases"
             f"?per_page={BETAS_PER_PAGE}")
MAX_BETAS_ANSWER = 1024 * 1024       # ten releases are about 75 KB
BETAS_DEADLINE = 20                  # seconds for the whole answer
BETA_TAG = re.compile(r"v[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}-beta\.[0-9]{1,4}")

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
    from GitHub's answer. Only a published stable release counts: one
    marked as a prerelease or draft, or with any other tag than v1.2.3, is
    refused, also if GitHub marks a test version as stable by mistake."""
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        raise Error("no release in the answer")
    if data.get("prerelease") is not False or data.get("draft") is not False:
        raise Error("the release is no published stable release")
    if not STABLE_TAG.fullmatch(data["tag_name"]):
        raise Error("the release has no tag of a stable version")
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
    """Whether the stable version latest is newer than current, e.g.
    "1.10.0" > "1.9.5", also "2.4.0" > "2.4.0-beta.1". A test version is
    never offered here, also not one from an old saved answer."""
    if releases.is_prerelease(latest):
        return False
    new, old = releases.number(latest), releases.number(current)
    return new is not None and old is not None and new > old


def _get_json(url, limit, timeout):
    """The JSON at an https:// address, see fetch.get(); raises Error."""
    if not url.startswith("https://"):
        raise Error("only HTTPS is asked")
    try:
        body = fetch.get(url, "application/vnd.github+json", limit, timeout,
                         BETAS_DEADLINE)
    except fetch.Error as e:
        raise Error(str(e)) from e          # offline, timeout, HTTP error
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as e:
        raise Error("the answer is no JSON") from e


def _listed_release(data):
    """("stable" or "beta", version, url) of one release in GitHub's list,
    or None for anything else: a draft, a tag of another form, a beta not
    marked as prerelease (or the other way round), or a page that is not
    exactly the release's own on GitHub. Nothing else of it is read."""
    if not isinstance(data, dict) or data.get("draft") is not False:
        return None
    tag, prerelease = data.get("tag_name"), data.get("prerelease")
    if not isinstance(tag, str) or not isinstance(prerelease, bool):
        return None
    if prerelease and BETA_TAG.fullmatch(tag):
        kind = "beta"
    elif not prerelease and STABLE_TAG.fullmatch(tag):
        kind = "stable"
    else:
        return None
    url = f"{RELEASES_URL}/tag/{tag}"
    version = releases.plain_version(tag)
    if data.get("html_url") != url or releases.number(version) is None:
        return None
    return kind, version, url


def newest_beta(data, current=None):
    """{"version": "2.4.0-beta.2", "url": "https://…"} of the newest beta
    in GitHub's list of releases, or None. Ordered by version, not by
    date. A beta only counts if it is newer than every stable release in
    the list and than current (the running version; None: any), so a
    stable 2.4.0 ends the betas of 2.4.0. Raises Error if data is no list.

    The list is one page of the newest releases (BETAS_PER_PAGE). A beta
    that ten newer releases have followed is not seen; such a beta is in
    practice ended by a stable release among them."""
    if not isinstance(data, list):
        raise Error("no list of releases in the answer")
    floor = None
    if current is not None:
        floor = releases.number(current)
        if floor is None:
            return None             # e.g. run from git: newer can't be told
    betas = []
    for item in data[:BETAS_PER_PAGE]:
        found = _listed_release(item)
        if found is None:
            continue
        kind, version, url = found
        key = releases.number(version)
        if kind == "stable":
            floor = key if floor is None else max(floor, key)
        else:
            betas.append((key, version, url))
    betas = [b for b in betas if floor is None or b[0] > floor]
    if not betas:
        return None
    _key, version, url = max(betas)
    return {"version": version, "url": url}


def fetch_beta(current=None, timeout=10):
    """The newest beta on GitHub newer than current, see newest_beta(), or
    None; raises Error. Only asks GitHub; never downloads anything."""
    return newest_beta(_get_json(BETAS_URL, MAX_BETAS_ANSWER, timeout), current)


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
