"""Covers from SteamGridDB (https://www.steamgriddb.com), without GTK.

SteamGridDB answers only with an API key. Every user gets their own for
free from their account, so none is built in:
https://www.steamgriddb.com/profile/preferences/api

Nothing here runs unless the user searches for a cover.
"""
import http.client
import json
import logging
import pathlib
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import USER_AGENT, config
# fetch() below is this module's own
from .fetch import Error as _FetchError, read_body as _read_body, release as _release

API = "https://www.steamgriddb.com/api/v2"
KEY_URL = "https://www.steamgriddb.com/profile/preferences/api"
# Most bytes of an answer of the API (lists of games or pictures): a limit
# for safety with much room, not a measured size
MAX_API_ANSWER = 4 * 1024 * 1024
# Seconds from the start of a request after which reading its answer stops;
# the timeout of each step on the network still comes on top, see fetch
API_DEADLINE = 30

log = logging.getLogger("understeer.steamgriddb")


class Error(Exception):
    """A failed request. `kind` is "key" (missing or refused), "network"
    or "save" (the picture could not be stored)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """An API answer that redirects is refused, not followed: urllib would
    send the API key along to wherever it points, another host or plain
    http:// too. Pictures (fetch()) still follow redirects; they carry
    no key."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Stopped here, the answer is never read and is ours to close.
        # Where it points is left out of the message: the server chose it.
        _release(fp)
        raise Error("network", f"the API answered with a redirect (HTTP {code})")


def _open_api(req, timeout):
    """urlopen() for API requests, without following redirects."""
    return urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)


def _get(path, key, params=None, timeout=15):
    """The "data" of an API answer; raises Error.

    timeout: seconds for connecting and for each read. The answer is read
    up to MAX_API_ANSWER bytes and until API_DEADLINE seconds after the
    request started, checked around each read of its body: best effort,
    no total limit. Looking up the name, connecting, TLS and the headers
    are bound only by the timeout of each step, and one read that blocks
    can end up to the timeout after the deadline."""
    if not key:
        raise Error("key", "no API key")
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # Also never copied to a redirect, should one be followed after all
    req.add_unredirected_header("Authorization", f"Bearer {key}")
    start = time.monotonic()
    try:
        with _open_api(req, timeout) as resp:
            body = _read_body(resp, MAX_API_ANSWER, start + API_DEADLINE)
    except urllib.error.HTTPError as e:
        _release(e)                         # it holds the answer, unread
        if e.code in (401, 403):
            raise Error("key", f"API key refused ({e.code})") from e
        if e.code == 404:
            return []                       # e.g. a game without grids
        raise Error("network", f"HTTP {e.code}") from e
    # HTTPException: e.g. a broken status line or an answer cut short;
    # _FetchError: too big, too slow, or fewer bytes than announced
    except (OSError, ValueError, http.client.HTTPException, _FetchError) as e:
        raise Error("network", str(e) or type(e).__name__) from e
    try:
        data = json.loads(body.decode("utf-8"))
    # RecursionError: JSON nested too deeply
    except (ValueError, RecursionError) as e:
        raise Error("network", str(e) or type(e).__name__) from e
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


def covers(game_id, key, steam_appid=None):
    """Portrait covers (600x900) of a SteamGridDB game, best rated first.

    Pass steam_appid instead of game_id to look a game up by its Steam id.
    [{"url", "thumb", "author"}]; animated and adult pictures are left out.
    """
    path = (f"/grids/steam/{int(steam_appid)}" if steam_appid
            else f"/grids/game/{int(game_id)}")
    log.info("Loading SteamGridDB covers: %s", path)
    grids = _get(path, key, {
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


def same_name(a, b):
    """Whether two game names are the same, ignoring case, accents,
    punctuation and signs like ™: "DiRT Rally 2.0™" == "Dirt Rally 2.0"."""
    def norm(name):
        name = unicodedata.normalize("NFKD", re.sub("[™®©]", "", name)).casefold()
        return re.sub(r"[^\w]|_", "", name)
    return bool(norm(a)) and norm(a) == norm(b)


def find_cover(name, key, steam_appid=None, game_id=None):
    """URL of the best cover for a game, or None.

    Used without asking the user, so only an unambiguous match counts:
    the SteamGridDB id chosen before, the Steam id, or a game whose name
    is exactly the given one.
    """
    if game_id:
        found = covers(game_id, key)
    elif steam_appid:
        found = covers(None, key, steam_appid=steam_appid)
    else:
        matches = [g for g in search(name, key) if same_name(g["name"], name)]
        if not matches:
            log.info("No SteamGridDB game named exactly %r", name)
            return None
        found = covers(matches[0]["id"], key)
    return found[0]["url"] if found else None


def fetch(url, timeout=20, limit=20 * 1024 * 1024, deadline=120):
    """Bytes of a picture on SteamGridDB's servers, a preview or the one
    chosen; raises Error.

    timeout: seconds for connecting and for each read. The picture is read
    up to limit bytes (a larger announced size is refused before reading)
    and until deadline seconds after the request started, checked around
    each read of its body: best effort, no total limit. Looking up the
    name, connecting, TLS and the headers are bound only by the timeout of
    each step, and one read that blocks can end up to the timeout after
    the deadline. Redirects are followed, as urlopen does."""
    host = urllib.parse.urlparse(url).hostname or ""
    if urllib.parse.urlparse(url).scheme != "https" or not (
            host == "steamgriddb.com" or host.endswith(".steamgriddb.com")):
        raise Error("network", f"unexpected address {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # also tells a picture cut short: fewer bytes than announced
            return _read_body(resp, limit, start + deadline)
    except _FetchError as e:
        if e.kind == "too_big":
            raise Error("network", "picture too large") from e
        raise Error("network", str(e)) from e       # too slow, or cut short
    # HTTPException: e.g. a broken status line or an answer cut short
    except (OSError, ValueError, http.client.HTTPException) as e:
        if isinstance(e, urllib.error.HTTPError):
            _release(e)                     # it holds the answer, unread
        raise Error("network", str(e) or type(e).__name__) from e


def download(url, dest):
    """Save the picture at url to dest (written completely or not at all)."""
    data = fetch(url)
    dest = pathlib.Path(dest)
    try:
        config.write_file(dest, data)
    except OSError as e:
        raise Error("save", str(e)) from e
    return dest
