"""Looking for a newer release on GitHub and updating the AppImage,
without GTK.

Only the AppImage looks: it has a fixed version, while a checkout from git
is updated with git. Nothing here runs unless the user agreed to it
(setting "update_check"). The request asks GitHub for the newest release
only and sends nothing about the user.

Test versions (betas) are never offered by that look: fetch_beta() finds
the newest one apart from it and only tells its version and page; the
window asks about it if the user switched that on (should_offer_beta()).

Updating (update_to()) asks GitHub again first and downloads only the
file that this fresh answer names, an AppImage of our own release for
this machine with its size and SHA-256: what was kept in the settings
from an earlier look (or came with an imported backup) is only shown,
never downloaded from. The download is checked against that size and
checksum and only then put in place of the old file. The running
Understeer goes on from its mounted image until it is restarted.
"""
import hashlib
import http.client
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
MAX_LATEST_ANSWER = 256 * 1024       # one release is a few KB
LATEST_DEADLINE = 20                 # seconds for reading it, see fetch
RELEASES_URL = "https://github.com/JamDaBam/Understeer/releases"
DOWNLOAD_URL = RELEASES_URL + "/download/"
MAX_DOWNLOAD = 200 * 1024 * 1024        # far more than an AppImage needs
CHECK_EVERY = 3600              # at most one look an hour, also over restarts
BETA_CHECK_EVERY = 24 * 3600    # at most one look for a beta a day
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


# What went wrong (Error.kind)
UNREACHABLE = "unreachable"     # GitHub could not be asked
INVALID = "invalid"             # ... or answered something unexpected
NO_ASSET = "no_asset"           # a release without a checked AppImage for us
NOT_NEWER = "not_newer"         # the newest release is no update any more
CHANGED = "changed"             # another version than the one chosen is newest
CANCELLED = "cancelled"         # looking for new versions was switched off
DOWNLOAD = "download"           # the download broke off
SIZE = "size"                   # ... or has another size than GitHub said
CHECKSUM = "checksum"           # ... or another SHA-256
INSTALL = "install"             # the new file could not be put in place


class Error(Exception):
    """GitHub could not be asked, answered something unexpected, or an
    update could not be done. kind: one of the kinds above; latest: the
    fresh answer of GitHub (parse_latest()) where it tells what to show
    now (NOT_NEWER, CHANGED, NO_ASSET), else None."""

    def __init__(self, message, kind=INVALID, latest=None):
        super().__init__(message)
        self.kind = kind
        self.latest = latest


def can_check(version, environ=os.environ):
    """Whether this Understeer looks for updates: a released AppImage."""
    return bool(environ.get("APPIMAGE")) and releases.has_notes(version)


def due(last_checked, now=None, every=CHECK_EVERY):
    """Whether it is time to look again; last_checked: a time.time()."""
    now = time.time() if now is None else now
    try:
        last_checked = float(last_checked)
    except (TypeError, ValueError):
        return True
    return not 0 <= now - last_checked < every


def checked_asset(asset, machine=None):
    """asset ({"version", "name", "url", "sha256", "size"}) if it is the
    AppImage of our own stable release of that version for this machine
    (machine: platform.machine() if None), else None: the name and the
    address exactly as our releases have them, a SHA-256 and a size of
    more than nothing and at most MAX_DOWNLOAD (a whole number, no bool)."""
    if not isinstance(asset, dict):
        return None
    version, name, url, sha256, size = (
        asset.get(k) for k in ("version", "name", "url", "sha256", "size"))
    if not isinstance(version, str) or not STABLE_TAG.fullmatch("v" + version):
        return None
    expected = f"Understeer-{version}-{machine or platform.machine()}.AppImage"
    if name != expected or url != f"{DOWNLOAD_URL}v{version}/{expected}":
        return None
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return None
    if type(size) is not int or not 0 < size <= MAX_DOWNLOAD:
        return None
    return {"version": version, "name": name, "url": url, "sha256": sha256,
            "size": size}


def _asset(data, version, machine):
    """The AppImage for this machine in a release (see checked_asset()),
    or None; only files of our own releases, with a checksum and a size."""
    name = f"Understeer-{version}-{machine}.AppImage"
    assets = data.get("assets")
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        digest = asset.get("digest")
        found = checked_asset({
            "version": version, "name": name,
            "url": asset.get("browser_download_url"),
            "sha256": digest[7:] if isinstance(digest, str)
            and digest.startswith("sha256:") else None,
            "size": asset.get("size")}, machine)
        if found is not None:
            return found
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
    url = f"{RELEASES_URL}/tag/{data['tag_name']}"
    if data.get("html_url") != url:
        url = RELEASES_URL                 # only ever open our own page
    version = releases.plain_version(data["tag_name"])
    return {"version": version, "url": url,
            "asset": _asset(data, version, machine or platform.machine())}


def fetch_latest(timeout=10):
    """The newest release on GitHub (parse_latest()); raises Error, kind
    UNREACHABLE if GitHub could not be asked, else INVALID.

    Exactly LATEST_URL is asked (fetch.get()): no redirect is followed,
    not even on the same server, and TLS is checked as urllib does.
    timeout: seconds for connecting and for each read. Reading the answer
    stops after LATEST_DEADLINE seconds from the start and at
    MAX_LATEST_ANSWER bytes (see fetch for what the deadline doesn't
    cover)."""
    try:
        body = fetch.get(LATEST_URL, "application/vnd.github+json",
                         MAX_LATEST_ANSWER, timeout, LATEST_DEADLINE)
    except fetch.Error as e:
        # A redirect or an answer too big is no answer of the API as asked
        kind = UNREACHABLE if e.kind in ("offline", "timeout", "http") else INVALID
        raise Error(str(e), kind) from e
    try:
        data = json.loads(body)
    except (ValueError, RecursionError) as e:   # also nested too deeply
        raise Error("the answer is no JSON") from e
    return parse_latest(data)


def for_display(latest):
    """What is kept of a look at GitHub (parse_latest()) in the settings:
    {"version", "url", "has_asset"}, only for showing the banner."""
    return {"version": latest["version"], "url": latest["url"],
            "has_asset": latest.get("asset") is not None}


def checked_latest(stored):
    """{"version", "url", "has_asset"} of update data as kept in the
    settings (also as older versions kept them, with the whole asset), or
    None for anything else. Only for showing: nothing is ever downloaded
    from it (see update_to()), and only our release's own page or the list
    of releases is opened."""
    if not isinstance(stored, dict):
        return None
    version = stored.get("version")
    if not isinstance(version, str) or not STABLE_TAG.fullmatch("v" + version):
        return None
    page = f"{RELEASES_URL}/tag/v{version}"
    has_asset = stored.get("has_asset")
    if not isinstance(has_asset, bool):
        has_asset = isinstance(stored.get("asset"), dict)
    return {"version": version,
            "url": page if stored.get("url") == page else RELEASES_URL,
            "has_asset": has_asset}


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


def checked_beta(beta):
    """{"version", "url"} of a beta as fetch_beta() gave it, checked again
    (it may come from the settings file), or None."""
    if not isinstance(beta, dict):
        return None
    version, url = beta.get("version"), beta.get("url")
    if (not isinstance(version, str) or not BETA_TAG.fullmatch("v" + version)
            or url != f"{RELEASES_URL}/tag/v{version}"):
        return None
    return {"version": version, "url": url}


def beta_series(version):
    """The release a beta is a test version of: "2.4.0" of "2.4.0-beta.2";
    None if version is no beta."""
    if not releases.is_prerelease(version):
        return None
    return releases.plain_version(version).split("-", 1)[0]


def notified_beta(version):
    """version if it is a beta version, e.g. "2.4.0-beta.2" as kept in the
    settings; None for anything else (missing, another type, a stable
    version, garbage)."""
    if isinstance(version, str) and BETA_TAG.fullmatch("v" + version):
        return version
    return None


def handled_series(series):
    """series if it is the version of a release, e.g. "2.4.0" as kept in
    the settings; None for anything else, a beta among them."""
    if (isinstance(series, str) and releases.number(series) is not None
            and not releases.is_prerelease(series)):
        return series
    return None


def newest(versions, check):
    """The newest of versions that check() lets through, by number (so
    beta.10 is newer than beta.9), or None. Keeps what was asked about
    from going back to an older version."""
    found = [v for v in map(check, versions) if v is not None]
    return max(found, key=releases.number, default=None)


def should_offer_beta(beta, current, stable, handled, notified=None):
    """Whether to ask about beta ({"version", "url"}) now.

    current: the running version; stable: the newest stable version known,
    or None. No stable release may be known that is newer than current or
    than the beta: a stable update comes first, and a stable release ends
    its betas.

    Running a stable version, the question is an invitation, once per
    release: the beta is a test version of a newer release than current,
    and of a newer release than the one whose beta was asked about already
    (handled, e.g. "2.4.0"). So beta.2 of a release asked about isn't
    asked about again.

    Running a beta, every newer test version is told about once: the beta
    is newer than current, by its whole number (2.4.0-beta.10 after
    2.4.0-beta.9, or 2.5.0-beta.1), and newer than the beta told about
    last (notified, e.g. "2.4.0-beta.2"). handled doesn't count here."""
    beta = checked_beta(beta)
    key, now = releases.number(beta and beta["version"]), releases.number(current)
    if beta is None or key is None or now is None:
        return False
    stable_key = releases.number(stable) if isinstance(stable, str) else None
    if stable_key is not None and not releases.is_prerelease(stable) and (
            stable_key > now or stable_key > key):
        return False
    if releases.is_prerelease(current):
        seen = releases.number(notified_beta(notified))
        return key > now and (seen is None or key > seen)
    if not key[0] > now[0]:
        return False                # not of a newer release than running
    done = releases.number(handled) if isinstance(handled, str) else None
    return done is None or key[0] > done[0]


def fetch_beta(current=None, timeout=10):
    """The newest beta on GitHub newer than current, see newest_beta(), or
    None; raises Error. Only asks GitHub; never downloads anything."""
    return newest_beta(_get_json(BETAS_URL, MAX_BETAS_ANSWER, timeout), current)


def can_install(appimage):
    """Whether the AppImage file can be replaced: its folder is writable."""
    if not appimage or not os.path.isfile(appimage):
        return False
    return os.access(os.path.dirname(os.path.abspath(appimage)), os.W_OK)


def prepare_update(latest, offered, current):
    """The asset to download for an update to offered (the version the
    user chose) from latest, the fresh answer of GitHub, while current
    runs. Raises Error: NOT_NEWER, CHANGED (another version is newest now:
    the user has to choose again) or NO_ASSET, with latest."""
    if not is_newer(latest["version"], current):
        raise Error(f"{latest['version']} is no newer version than {current}",
                    NOT_NEWER, latest)
    if latest["version"] != offered:
        raise Error(f"GitHub offers {latest['version']} now, not {offered}",
                    CHANGED, latest)
    asset = checked_asset(latest.get("asset"))
    if asset is None or asset["version"] != latest["version"]:
        raise Error("no checked AppImage of this release for this computer",
                    NO_ASSET, latest)
    return asset


def update_to(offered, current, appimage, cancelled=lambda: False,
              progress=None):
    """Update appimage to the version offered, as the user chose it. Asks
    GitHub again and downloads only the file its fresh answer names,
    nothing kept from before. cancelled() is asked before the download,
    while it runs and before the old file is replaced: True stops
    (CANCELLED). Returns the fresh answer; raises Error."""
    latest = fetch_latest()
    asset = prepare_update(latest, offered, current)
    if cancelled():
        raise Error("the update was called off", CANCELLED, latest)
    new = download(asset, appimage, progress=progress, cancelled=cancelled)
    if cancelled():
        _remove(new)
        raise Error("the update was called off", CANCELLED, latest)
    install(new, appimage)
    return latest


def download(asset, appimage, timeout=30, progress=None, cancelled=None):
    """Download the new AppImage next to appimage and check its size and
    SHA-256. asset: as checked_asset() lets it through, from a fresh
    answer of GitHub. Redirects are followed to https:// only (GitHub
    sends its files from another host); the size and the checksum the
    fresh answer gave decide whether the file is ours. Returns the path
    of the checked file; raises Error. Nothing is left behind otherwise."""
    asset = checked_asset(asset)
    if asset is None:
        raise Error("no checked AppImage of a release to download", NO_ASSET)
    folder = os.path.dirname(os.path.abspath(appimage))
    part = os.path.join(folder, ".Understeer-update.AppImage.part")
    req = urllib.request.Request(asset["url"], headers={"User-Agent": USER_AGENT})
    expected, digest, size = asset["size"], hashlib.sha256(), 0
    try:
        with fetch.open_https(req, timeout) as resp, open(part, "wb") as out:
            # Never more than one byte past the size asked for: that tells
            # that there is more. The size is at most MAX_DOWNLOAD.
            while chunk := resp.read(min(64 * 1024, expected + 1 - size)):
                if cancelled is not None and cancelled():
                    raise Error("the update was called off", CANCELLED)
                size += len(chunk)
                if size > expected:
                    raise Error("the download is bigger than GitHub said", SIZE)
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(size / expected)
    except fetch.Error as e:                # a redirect away from HTTPS
        _remove(part)
        raise Error(str(e), DOWNLOAD) from e
    # HTTPException: e.g. a broken status line or a chunk cut short; an
    # answer cut short otherwise just ends, and the size tells
    except (OSError, ValueError, http.client.HTTPException) as e:
        if isinstance(e, urllib.error.HTTPError):
            fetch.release(e)                # it holds the answer, unread
        _remove(part)
        raise Error(str(e) or type(e).__name__, DOWNLOAD) from e
    except Error:
        _remove(part)
        raise
    if size != expected:
        _remove(part)
        raise Error(f"the download has {size} of {expected} bytes", SIZE)
    if digest.hexdigest() != asset["sha256"]:
        _remove(part)
        raise Error("the checksum of the download is wrong", CHECKSUM)
    return part


def install(new_file, appimage):
    """Put the checked new_file in place of appimage, in one step."""
    try:
        os.chmod(new_file, 0o755)
        os.replace(new_file, appimage)
    except OSError as e:
        _remove(new_file)
        raise Error(str(e), INSTALL) from e
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
