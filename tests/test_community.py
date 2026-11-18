"""Downloading and caching the community catalog, against a local HTTP
server on 127.0.0.1: nothing goes to the internet."""
import http.client
import http.server
import json
import os
import pathlib
import threading
import time
import urllib.parse

import pytest

from understeer import community, config, library

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "library" / "catalog.json"
GOOD = FIXTURE.read_bytes()


class Server:
    """A local server; answer(handler) answers each request."""

    def __init__(self):
        server = self
        self.requests = []
        self.answer = lambda h: h.send_body(GOOD)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                server.requests.append((self.path, dict(self.headers)))
                server.answer(self)

            def send_body(self, body, status=200, length=True):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if length:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,),
                         daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/Understeer-Presets/catalog.json"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    s = Server()
    monkeypatch.setattr(community, "CATALOG_URL", s.url)
    yield s
    s.close()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "cache" / "Understeer" / "library" / "catalog.json"
    monkeypatch.setattr(community, "CACHE_FILE", path)
    return path


def kind(fn, *args, **kwargs):
    with pytest.raises(community.Error) as e:
        fn(*args, **kwargs)
    return e.value.kind


# --- the address --------------------------------------------------------------

def test_catalog_address_is_https_on_github_pages():
    parts = urllib.parse.urlsplit(community.CATALOG_URL)
    assert (parts.scheme, parts.netloc, parts.path) == (
        "https", "jamdabam.github.io", "/Understeer-Presets/catalog.json")
    assert not parts.query and not parts.fragment


def test_cache_is_apart_from_presets_and_settings():
    assert community.CACHE_FILE.parent.parent == config.COVER_DIR.parent
    assert config.CONFIG_DIR not in community.CACHE_FILE.parents


# --- downloading --------------------------------------------------------------

def test_download(server):
    assert community.download() == GOOD
    (path, headers), = server.requests
    assert path == "/Understeer-Presets/catalog.json"
    # Nothing about the user, the presets or the wheelbase goes along
    assert headers["User-Agent"] == "Understeer"
    assert not {"Cookie", "Authorization"} & set(headers)


@pytest.mark.parametrize("status", [404, 500, 503])
def test_http_error(server, status):
    server.answer = lambda h: h.send_body(b"error", status)
    with pytest.raises(community.Error) as e:
        community.download()
    assert (e.value.kind, e.value.code) == ("http", status)


def test_no_content_is_an_error(server):
    server.answer = lambda h: h.send_body(b"", 204)
    assert kind(community.download) == "http"


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_is_not_followed(server, status):
    def redirect(h):
        h.send_response(status)
        h.send_header("Location", "https://example.org/evil.json")
        h.send_header("Content-Length", "0")
        h.end_headers()
    server.answer = redirect
    assert kind(community.download) == "redirect"
    assert len(server.requests) == 1          # nothing else was asked


@pytest.mark.parametrize("status, location, expected", [
    (404, None, "http"), (500, None, "http"),
    (302, "https://example.org/evil.json", "redirect"),
    (308, "/other.json", "redirect"),
])
def test_refused_answers_are_closed(server, monkeypatch, status, location,
                                    expected):
    """Kept here, an answer counts as closed only if the code closed it."""
    made = []
    init = http.client.HTTPResponse.__init__

    def keep(self, *args, **kwargs):
        init(self, *args, **kwargs)
        made.append(self)
    monkeypatch.setattr(http.client.HTTPResponse, "__init__", keep)

    def answer(h):
        if location is None:
            h.send_body(b"error", status)
        else:
            h.send_response(status)
            h.send_header("Location", location)
            h.send_header("Content-Length", "0")
            h.end_headers()
    server.answer = answer
    assert kind(community.download) == expected
    assert len(server.requests) == 1
    assert len(made) == 1 and made[0].closed


def test_timeout(server):
    server.answer = lambda h: (time.sleep(1.5), h.send_body(GOOD))
    assert kind(community.download, timeout=0.3) == "timeout"


def test_slow_download_hits_the_deadline(server):
    def trickle(h):
        h.send_response(200)
        h.end_headers()
        for _i in range(20):
            h.wfile.write(b" ")
            h.wfile.flush()
            time.sleep(0.05)
    server.answer = trickle
    assert kind(community.download, deadline=0.2) == "timeout"


def test_network_unavailable(monkeypatch):
    s = Server()
    url = s.url
    s.close()                                   # nobody listens there now
    monkeypatch.setattr(community, "CATALOG_URL", url)
    assert kind(community.download) == "offline"


def test_announced_oversize(server):
    def announce(h):
        h.send_response(200)
        h.send_header("Content-Length", str(library.MAX_CATALOG_BYTES + 1))
        h.end_headers()
        h.wfile.write(b"{}")
    server.answer = announce
    assert kind(community.download) == "too_big"


def test_oversize_without_length(server):
    big = b" " * (library.MAX_CATALOG_BYTES + 1)
    server.answer = lambda h: h.send_body(big, length=False)
    assert kind(community.download) == "too_big"


def test_largest_allowed_size(server):
    body = GOOD + b" " * (library.MAX_CATALOG_BYTES - len(GOOD))
    server.answer = lambda h: h.send_body(body)
    assert community.download() == body


# --- checking -----------------------------------------------------------------

@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"presets": []}',
                                 b'{"understeer_library": 1, "a": 1, "a": 2}'])
def test_invalid_catalog(raw):
    assert kind(community.parse, raw) == "invalid"


def test_unsupported_catalog_version():
    raw = json.dumps({"understeer_library": 2, "presets": []}).encode()
    assert kind(community.parse, raw) == "newer"


# --- the cache ----------------------------------------------------------------

def test_first_refresh_creates_the_cache(server, cache):
    assert community.read_cache() is None
    loaded = community.refresh()
    assert [e.id for e in loaded.catalog.entries] == \
        ["acc-gt3-endurance", "dirt-rally-2-gravel"]
    assert cache.read_bytes() == GOOD
    assert abs(loaded.when - time.time()) < 5
    assert sorted(p.name for p in cache.parent.iterdir()) == ["catalog.json"]


def test_valid_cache_is_read_and_checked_again(cache):
    cache.parent.mkdir(parents=True)
    cache.write_bytes(GOOD)
    os.utime(cache, (1000, 1000))
    loaded = community.read_cache()
    assert len(loaded.catalog.entries) == 2 and loaded.when == 1000


@pytest.mark.parametrize("content", [b"", b"garbage", b'{"understeer_library": 9}',
                                     b"\xff\xfe"])
def test_corrupted_cache_is_not_used(cache, content):
    cache.parent.mkdir(parents=True)
    cache.write_bytes(content)
    assert community.read_cache() is None
    assert cache.read_bytes() == content        # left for the next refresh


def test_oversized_cache_is_not_read(cache):
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b" " * (library.MAX_CATALOG_BYTES + 1))
    assert community.read_cache() is None


def test_unreadable_cache(cache):
    cache.mkdir(parents=True)                   # a folder where the file belongs
    assert community.read_cache() is None


@pytest.mark.parametrize("answer", [
    lambda h: h.send_body(b"broken", 200),
    lambda h: h.send_body(b"error", 500),
    lambda h: h.send_body(json.dumps({"understeer_library": 2,
                                      "presets": []}).encode()),
    lambda h: h.send_body(b'{"understeer_library": 1' + b"0" * 5000
                          + b', "presets": []}'),
], ids=["broken", "http-500", "newer-format", "too-long-number"])
def test_failed_refresh_keeps_the_cache(server, cache, answer):
    community.refresh()
    before = cache.stat().st_mtime_ns
    server.answer = answer
    with pytest.raises(community.Error):
        community.refresh()
    assert cache.read_bytes() == GOOD and cache.stat().st_mtime_ns == before
    assert not cache.with_name("catalog.json.part").exists()


def test_cache_is_replaced_atomically(server, cache, monkeypatch):
    community.refresh()
    replaced = []
    real = pathlib.Path.replace

    def spy(self, target):
        replaced.append((self.name, pathlib.Path(target).name))
        assert pathlib.Path(target).read_bytes() == GOOD   # old one whole until then
        return real(self, target)
    monkeypatch.setattr(pathlib.Path, "replace", spy)
    empty = json.dumps({"understeer_library": 1, "presets": []}).encode()
    server.answer = lambda h: h.send_body(empty)
    community.refresh()
    assert replaced == [("catalog.json.part", "catalog.json")]
    assert cache.read_bytes() == empty


def test_cache_that_cant_be_written_still_gives_the_catalog(server, cache,
                                                            monkeypatch):
    def fail(path, data):
        raise OSError("disk full")
    monkeypatch.setattr(config, "write_file", fail)
    assert len(community.refresh().catalog.entries) == 2


def test_no_needless_downloads():
    now = 10_000_000
    fresh = community.Loaded(None, now - community.FRESH_FOR + 1)
    assert not community.due(fresh, now)
    assert community.due(community.Loaded(None, now - community.FRESH_FOR), now)
    assert community.due(None, now)
    assert community.due(community.Loaded(None, now + 3600), now)  # clock went back


def test_nothing_runs_on_its_own():
    """No timer, thread or loop: the module only acts when called."""
    source = pathlib.Path(community.__file__).read_text()
    for word in ("threading", "timeout_add", "while True", "sched", "GLib"):
        assert word not in source


def test_too_long_number_is_an_invalid_catalog(server, cache):
    """Downloaded or cached, a number too long for an int is told as an
    invalid catalog, not as an unexpected error; the cache stays."""
    community.refresh()
    bad = b'{"understeer_library": 1, "presets": [' + b"9" * 5000 + b"]}"
    server.answer = lambda h: h.send_body(bad)
    with pytest.raises(community.Error) as e:
        community.refresh()
    assert e.value.kind == "invalid"
    assert cache.read_bytes() == GOOD
    cache.write_bytes(bad)                      # changed by someone else
    assert community.read_cache() is None
