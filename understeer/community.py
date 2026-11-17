"""The community preset library: downloading its catalog and keeping the
last good one, without GTK.

The catalog is one static file on GitHub Pages (docs/preset-library.md).
Only that address is ever asked, over HTTPS, without following redirects
and with nothing sent but the request itself. What comes back is checked
by library.parse_catalog only, and kept in the cache only when it passed,
so the library can still be browsed offline.

Nothing here runs on its own: the catalog is loaded when the user opens
the library (at most once an hour) or asks for it with Refresh.
"""
import logging
import time
from collections import namedtuple

from . import config, fetch, library

CATALOG_URL = "https://jamdabam.github.io/Understeer-Presets/catalog.json"
# Next to the game covers: it can be downloaded again, so it is a cache.
CACHE_FILE = config.COVER_DIR.parent / "library" / "catalog.json"
TIMEOUT = 15            # seconds for connecting and for each read
DEADLINE = 30           # seconds for the whole download
FRESH_FOR = 3600        # opening the library loads it again after this

log = logging.getLogger("understeer.community")


class Error(Exception):
    """The catalog couldn't be loaded. kind: "offline", "timeout", "http"
    (code is the HTTP status), "redirect", "too_big", "invalid" or
    "newer" (only a newer Understeer reads it)."""

    def __init__(self, kind, message, code=None):
        super().__init__(message)
        self.kind = kind
        self.code = code


# catalog: library.Catalog; when: time.time() it was downloaded
Loaded = namedtuple("Loaded", "catalog when")


def download(timeout=TIMEOUT, deadline=DEADLINE):
    """The bytes of the catalog at CATALOG_URL; raises Error."""
    try:
        return fetch.get(CATALOG_URL, "application/json",
                         library.MAX_CATALOG_BYTES, timeout, deadline)
    except fetch.Error as e:
        raise Error(e.kind, str(e), e.code) from None


def parse(raw):
    """library.Catalog of the bytes raw; raises Error."""
    try:
        return library.parse_catalog(raw)
    except library.NewerFormat as e:
        raise Error("newer", str(e)) from None
    except library.Error as e:
        raise Error("invalid", str(e)) from None


def read_cache(path=None):
    """The catalog last downloaded, checked again, or None when there is
    none or it can't be read or used."""
    path = CACHE_FILE if path is None else path
    try:
        if path.stat().st_size > library.MAX_CATALOG_BYTES:
            raise Error("too_big", "cache file too big")
        raw, when = path.read_bytes(), path.stat().st_mtime
        return Loaded(parse(raw), when)
    except FileNotFoundError:
        return None
    except (OSError, Error) as e:
        log.warning("Cached community catalog not used: %s", e)
        return None


def refresh(path=None):
    """Download and check the catalog, then keep it in the cache. Returns
    Loaded; raises Error and leaves the cache as it was. For a thread."""
    path = CACHE_FILE if path is None else path
    log.info("Loading the community catalog from %s", CATALOG_URL)
    raw = download()
    catalog = parse(raw)
    try:
        config.write_file(path, raw)
    except OSError as e:
        log.warning("Could not keep the community catalog: %s", e)
    log.info("Community catalog: %d presets, %d skipped",
             len(catalog.entries), len(catalog.skipped))
    return Loaded(catalog, time.time())


def due(loaded, now=None):
    """Whether opening the library should load the catalog again: there is
    none yet, it is older than FRESH_FOR, or its time is in the future."""
    if loaded is None:
        return True
    now = time.time() if now is None else now
    return not 0 <= now - loaded.when < FRESH_FOR
