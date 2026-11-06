"""Covers from SteamGridDB (https://www.steamgriddb.com), without GTK.

SteamGridDB answers only with an API key. Every user gets their own for
free from their account, so none is built in:
https://www.steamgriddb.com/profile/preferences/api

Nothing here runs unless the user searches for a cover.
"""
import json
import logging
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.steamgriddb.com/api/v2"
KEY_URL = "https://www.steamgriddb.com/profile/preferences/api"
USER_AGENT = "Understeer"

log = logging.getLogger("understeer.steamgriddb")


class Error(Exception):
    """A failed request. `kind` is "key" (missing or refused), "network"
    or "save" (the picture could not be stored)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _get(path, key, params=None, timeout=15):
    if not key:
        raise Error("key", "no API key")
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Error("key", f"API key refused ({e.code})") from e
        if e.code == 404:
            return []                       # e.g. a game without grids
        raise Error("network", f"HTTP {e.code}") from e
    except (OSError, ValueError) as e:
        raise Error("network", str(e)) from e
    if not isinstance(data, dict) or not data.get("success"):
        errors = data.get("errors") if isinstance(data, dict) else None
        raise Error("network", "; ".join(map(str, errors or ["bad answer"])))
    return data.get("data") or []


def search(term, key):
    """Games matching term: [{"id", "name", "year"}], best match first."""
    term = term.strip()
    if not term:
        return []
    log.info("Searching SteamGridDB for %r", term)
    found = []
    for g in _get("/search/autocomplete/" + urllib.parse.quote(term, safe=""), key):
        if not isinstance(g, dict) or "id" not in g:
            continue
        year = None
        if isinstance(g.get("release_date"), (int, float)) and g["release_date"] > 0:
            year = time.gmtime(g["release_date"]).tm_year
        found.append({"id": g["id"], "name": str(g.get("name") or g["id"]),
                      "year": year})
    return found


def covers(game_id, key):
    """Portrait covers (600x900) of a SteamGridDB game, best rated first.

    [{"url", "thumb", "author"}]; animated and adult pictures are left out.
    """
    log.info("Loading SteamGridDB covers of game %s", game_id)
    grids = _get(f"/grids/game/{int(game_id)}", key, {
        "dimensions": "600x900", "types": "static", "nsfw": "false",
        "mimes": "image/png,image/jpeg"})
    result = []
    for g in grids:
        if not isinstance(g, dict) or not g.get("url"):
            continue
        author = g.get("author") if isinstance(g.get("author"), dict) else {}
        result.append({"url": g["url"], "thumb": g.get("thumb") or g["url"],
                       "author": author.get("name") or ""})
    return result


def fetch(url, timeout=20, limit=20 * 1024 * 1024):
    """Bytes of a picture on SteamGridDB's servers."""
    host = urllib.parse.urlparse(url).hostname or ""
    if urllib.parse.urlparse(url).scheme != "https" or not (
            host == "steamgriddb.com" or host.endswith(".steamgriddb.com")):
        raise Error("network", f"unexpected address {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(limit + 1)
    except (OSError, ValueError) as e:
        raise Error("network", str(e)) from e
    if len(data) > limit:
        raise Error("network", "picture too large")
    return data


def download(url, dest):
    """Save the picture at url to dest (written completely or not at all)."""
    data = fetch(url)
    dest = pathlib.Path(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
    except OSError as e:
        raise Error("save", str(e)) from e
    return dest
