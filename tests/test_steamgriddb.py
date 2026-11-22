import contextlib
import io
import json
import logging
import urllib.parse
import urllib.request
from unittest import mock

import pytest

from understeer import steamgriddb


class Response:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {}           # as a real answer, here without a length

    def read(self, limit=None):
        return self.body if limit is None else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serve(answer):
    """Patch the opening of API requests and urlopen (pictures);
    answer(request) returns a body or raises."""
    asked = []

    def urlopen(req, timeout):
        asked.append(req)
        return Response(answer(req))
    @contextlib.contextmanager
    def patch():
        with mock.patch.object(steamgriddb, "_open_api", urlopen), \
                mock.patch.object(steamgriddb.urllib.request, "urlopen", urlopen):
            yield
    return patch(), asked


def http_error(code):
    def raise_(req):
        raise steamgriddb.urllib.error.HTTPError(req.full_url, code, "", {}, None)
    return raise_


def test_search():
    patch, asked = serve(lambda req: {"success": True, "data": [
        {"id": 5248, "name": "Diablo II", "release_date": 961891200},
        {"id": 5249, "name": "Diablo II: Resurrected"},
        "junk"]})
    with patch:
        found = steamgriddb.search("Diablo II", "secret")
    assert found == [{"id": 5248, "name": "Diablo II", "year": 2000},
                     {"id": 5249, "name": "Diablo II: Resurrected", "year": None}]
    assert asked[0].full_url.endswith("/search/autocomplete/Diablo%20II")
    assert asked[0].get_header("Authorization") == "Bearer secret"


def test_search_without_key_or_term():
    with pytest.raises(steamgriddb.Error) as e:
        steamgriddb.search("Diablo", "")
    assert e.value.kind == "key"
    assert steamgriddb.search("  ", "secret") == []


@pytest.mark.parametrize("code, kind", [(401, "key"), (403, "key"), (500, "network")])
def test_errors(code, kind):
    patch, _asked = serve(http_error(code))
    with patch, pytest.raises(steamgriddb.Error) as e:
        steamgriddb.search("Diablo", "secret")
    assert e.value.kind == kind


def test_unsuccessful_answer():
    patch, _asked = serve(lambda req: {"success": False, "errors": ["Game not found"]})
    with patch, pytest.raises(steamgriddb.Error) as e:
        steamgriddb.covers(1, "secret")
    assert "Game not found" in str(e.value)


def test_covers():
    patch, asked = serve(lambda req: {"success": True, "data": [
        {"url": "https://cdn2.steamgriddb.com/grid/a.png",
         "thumb": "https://cdn2.steamgriddb.com/thumb/a.jpg",
         "author": {"name": "someone"}},
        {"url": "https://cdn2.steamgriddb.com/grid/b.jpg"},
        {"thumb": "no url"}]})
    with patch:
        covers = steamgriddb.covers(5248, "secret")
    assert covers == [
        {"url": "https://cdn2.steamgriddb.com/grid/a.png",
         "thumb": "https://cdn2.steamgriddb.com/thumb/a.jpg", "author": "someone"},
        {"url": "https://cdn2.steamgriddb.com/grid/b.jpg",
         "thumb": "https://cdn2.steamgriddb.com/grid/b.jpg", "author": ""}]
    url = asked[0].full_url
    assert "/grids/game/5248?" in url
    assert "dimensions=600x900" in url and "nsfw=false" in url


def test_game_without_covers():
    patch, _asked = serve(http_error(404))
    with patch:
        assert steamgriddb.covers(1, "secret") == []


def test_fetch_only_from_steamgriddb():
    for url in ("https://evil.example/a.png", "http://cdn2.steamgriddb.com/a.png",
                "https://steamgriddb.com.evil.example/a.png", "file:///etc/passwd"):
        with pytest.raises(steamgriddb.Error):
            steamgriddb.fetch(url)


def test_download(tmp_path):
    patch, asked = serve(lambda req: b"\x89PNG picture")
    with patch:
        path = steamgriddb.download("https://cdn2.steamgriddb.com/grid/a.png",
                                    tmp_path / "covers" / "x.png")
    assert path.read_bytes() == b"\x89PNG picture"
    assert asked[0].get_header("Authorization") is None   # key stays with the API
    assert [p.name for p in path.parent.iterdir()] == ["x.png"]


def test_download_too_large(tmp_path, monkeypatch):
    patch, _asked = serve(lambda req: b"x" * 50)
    with patch, pytest.raises(steamgriddb.Error):
        steamgriddb.fetch("https://cdn2.steamgriddb.com/a.png", limit=10)


@pytest.mark.parametrize("a, b, same", [
    ("DiRT Rally 2.0™", "Dirt Rally 2.0", True),
    ("Pokémon", "pokemon", True),
    ("Diablo II", "Diablo II: Resurrected", False),
    ("", "", False),
])
def test_same_name(a, b, same):
    assert steamgriddb.same_name(a, b) is same


def test_find_cover_needs_exact_name():
    def answer(req):
        if "/search/" in req.full_url:
            return {"success": True, "data": [{"id": 2, "name": "Diablo II: Resurrected"},
                                              {"id": 1, "name": "Diablo II"}]}
        assert "/grids/game/1?" in req.full_url
        return {"success": True, "data": [{"url": "https://cdn2.steamgriddb.com/a.png"}]}
    patch, _asked = serve(answer)
    with patch:
        assert steamgriddb.find_cover("diablo ii", "k") == "https://cdn2.steamgriddb.com/a.png"
        assert steamgriddb.find_cover("Diablo", "k") is None


def test_find_cover_by_steam_id():
    patch, asked = serve(lambda req: {"success": True, "data": []})
    with patch:
        assert steamgriddb.find_cover("whatever", "k", steam_appid="1161040") is None
    assert "/grids/steam/1161040?" in asked[0].full_url


def test_find_cover_by_chosen_game():
    patch, asked = serve(lambda req: {"success": True, "data": [
        {"url": "https://cdn2.steamgriddb.com/a.png"}]})
    with patch:
        url = steamgriddb.find_cover("Renamed Tile", "k", game_id=5248)
    assert url == "https://cdn2.steamgriddb.com/a.png"
    assert len(asked) == 1 and "/grids/game/5248?" in asked[0].full_url


class ErrorBody(io.BytesIO):
    """The answer an HTTPError holds; fail: closing it raises that."""

    def __init__(self, fail=None):
        super().__init__(b"error")
        self.fail = fail

    def close(self):
        super().close()
        if self.fail:
            raise self.fail


RAISED = []          # kept, so only the code can close their answers


def http_error_with(code, body):
    def raise_(req):
        error = steamgriddb.urllib.error.HTTPError(req.full_url, code, "", {}, body)
        RAISED.append(error)
        raise error
    return raise_


@pytest.mark.parametrize("fail", [None, OSError("gone"),
                                  ValueError("I/O operation on closed file")])
@pytest.mark.parametrize("code, kind, message", [
    (401, "key", "API key refused (401)"), (403, "key", "API key refused (403)"),
    (500, "network", "HTTP 500"),
])
def test_an_http_error_is_closed(code, kind, message, fail):
    body = ErrorBody(fail)
    patch, _asked = serve(http_error_with(code, body))
    with patch, pytest.raises(steamgriddb.Error) as e:
        steamgriddb.search("Diablo", "secret")
    assert (e.value.kind, str(e.value)) == (kind, message)
    assert isinstance(e.value.__cause__, steamgriddb.urllib.error.HTTPError)
    assert body.closed


def test_not_found_is_closed_too():
    body = ErrorBody()
    patch, _asked = serve(http_error_with(404, body))
    with patch:
        assert steamgriddb.covers(5248, "secret") == []
    assert body.closed


@pytest.mark.parametrize("fail", [None, OSError("gone")])
def test_a_picture_http_error_is_closed(fail):
    body = ErrorBody(fail)
    patch, _asked = serve(http_error_with(404, body))
    with patch, pytest.raises(steamgriddb.Error) as e:
        steamgriddb.fetch("https://cdn2.steamgriddb.com/a.png")
    assert (e.value.kind, str(e.value)) == ("network", "HTTP Error 404: ")
    assert body.closed


# -- broken answers, from a real server on 127.0.0.1 --------------------------
# urllib and http.client read them as they do from SteamGridDB; only the
# address is the local one.
from test_fetch import Server  # noqa: E402

PICTURE = b"\x89PNG picture"


@pytest.fixture
def local():
    s = Server()
    yield s
    s.close()


def raw(data):
    """An answer written as it is, status line and headers included."""
    def answer(handler):
        handler.wfile.write(data)
    return answer


def picture_from(server, monkeypatch, **kwargs):
    """steamgriddb.fetch() of a SteamGridDB address, served by server."""
    real = steamgriddb.urllib.request.urlopen

    def urlopen(req, timeout):
        assert req.full_url == "https://cdn2.steamgriddb.com/grid/a.png"
        return real(f"http://{server.address}/a.png", timeout=timeout)
    monkeypatch.setattr(steamgriddb.urllib.request, "urlopen", urlopen)
    return steamgriddb.fetch("https://cdn2.steamgriddb.com/grid/a.png", **kwargs)


@pytest.mark.parametrize("answer", [
    lambda h: h.send_body(PICTURE),                     # with Content-Length
    lambda h: h.send_body(PICTURE, length=False),       # until the end
    raw(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"4\r\n\x89PNG\r\n8\r\n picture\r\n0\r\n\r\n"),
], ids=["length", "no-length", "chunked"])
def test_a_whole_picture_is_taken(local, monkeypatch, answer):
    local.answer = answer
    assert picture_from(local, monkeypatch) == PICTURE
    assert local.answered.wait(5)


@pytest.mark.parametrize("answer, message", [
    # The connection closes after 100 of 1000 bytes: read() of a size
    # doesn't notice, so this must not pass for a picture
    (raw(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + b"p" * 100),
     "100 of 1000 bytes came"),
    (raw(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
         b"3e8\r\n" + b"p" * 100), "IncompleteRead"),
    (raw(b"NONSENSE\r\n\r\n"), "NONSENSE"),             # no status line
], ids=["cut-short", "chunk-cut-short", "bad-status-line"])
def test_a_broken_picture_is_refused(local, monkeypatch, tmp_path, answer, message):
    local.answer = answer
    with pytest.raises(steamgriddb.Error) as e:
        picture_from(local, monkeypatch)
    assert e.value.kind == "network" and message in str(e.value)
    assert local.answered.wait(5)
    # download() keeps nothing of it
    with pytest.raises(steamgriddb.Error):
        steamgriddb.download("https://cdn2.steamgriddb.com/grid/a.png", tmp_path / "x.png")
    assert not list(tmp_path.iterdir())


def test_a_picture_too_large_stays_refused(local, monkeypatch):
    local.answer = lambda h: h.send_body(b"x" * 50)
    with pytest.raises(steamgriddb.Error, match="picture too large"):
        picture_from(local, monkeypatch, limit=10)


@pytest.mark.parametrize("answer", [
    raw(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + b'{"success": true'),
    raw(b"NONSENSE\r\n\r\n"),
], ids=["cut-short", "bad-status-line"])
def test_a_broken_api_answer_is_a_network_error(local, monkeypatch, answer):
    local.answer = answer
    monkeypatch.setattr(steamgriddb, "API", f"http://{local.address}/api")
    with pytest.raises(steamgriddb.Error) as e:
        steamgriddb.search("Diablo", "secret-key")
    assert e.value.kind == "network" and str(e.value)
    assert "secret-key" not in str(e.value)
    assert isinstance(e.value.__cause__, steamgriddb.http.client.HTTPException)
    (path, headers), = local.requests
    assert headers["Authorization"] == "Bearer secret-key"     # sent as before
    assert local.answered.wait(5)


def test_a_whole_api_answer_is_read(local, monkeypatch):
    local.answer = lambda h: h.send_body(json.dumps(
        {"success": True, "data": [{"id": 1, "name": "Diablo"}]}).encode())
    monkeypatch.setattr(steamgriddb, "API", f"http://{local.address}/api")
    assert steamgriddb.search("Diablo", "secret-key") == \
        [{"id": 1, "name": "Diablo", "year": None}]


# -- the API key and redirects -------------------------------------------------
# A second local server stands for another host: same 127.0.0.1, another
# port, so another origin to urllib.
KEY = "secret-key"


@pytest.fixture
def other():
    s = Server()
    yield s
    s.close()


def redirect(location, code=302):
    def answer(handler):
        handler.send_response(code)
        handler.send_header("Location", location)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
    return answer


def api_at(server, monkeypatch):
    monkeypatch.setattr(steamgriddb, "API", f"http://{server.address}/api")


def test_the_key_goes_only_to_the_https_api():
    assert steamgriddb.API.startswith("https://www.steamgriddb.com/")
    patch, asked = serve(lambda req: {"success": True, "data": []})
    with patch:
        steamgriddb.search("Diablo", KEY)
    req, = asked
    assert urllib.parse.urlsplit(req.full_url)[:2] == ("https", "www.steamgriddb.com")
    assert req.get_header("Authorization") == f"Bearer {KEY}"
    # and not among the headers urllib copies to a redirect
    assert "Authorization" not in req.headers


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("where", ["other-host", "same-host"])
def test_an_api_redirect_is_refused(local, other, monkeypatch, caplog, code, where):
    target = (f"http://{other.address}/api/search/autocomplete/Diablo"
              if where == "other-host" else "/api/v3/search/autocomplete/Diablo")
    local.answer = redirect(target, code)
    api_at(local, monkeypatch)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(steamgriddb.Error) as e:
        steamgriddb.search("Diablo", KEY)
    assert (e.value.kind, str(e.value)) == (
        "network", f"the API answered with a redirect (HTTP {code})")
    assert local.answered.wait(5)
    assert len(local.requests) == 1             # not followed, also not here
    assert other.requests == []                 # the other host got nothing
    for text in (str(e.value), repr(e.value), caplog.text):
        assert KEY not in text and other.address not in text


def test_a_redirect_chain_stops_at_its_first_step(local, other, monkeypatch):
    # /api/... -> /next -> the other host: nothing past the first answer
    def answer(handler):
        target = "/next" if handler.path.startswith("/api/") else \
            f"http://{other.address}/api/x"
        redirect(target)(handler)
    local.answer = answer
    api_at(local, monkeypatch)
    with pytest.raises(steamgriddb.Error, match="redirect"):
        steamgriddb.covers(5248, KEY)
    (path, _headers), = local.requests          # only the first step
    assert path.startswith("/api/grids/game/5248")
    assert other.requests == []


@pytest.mark.parametrize("fail", [None, OSError("gone")])
@pytest.mark.parametrize("newurl", [
    "http://www.steamgriddb.com/api/v2/search/autocomplete/Diablo",   # downgrade
    "https://evil.example/api/v2/search/autocomplete/Diablo",
])
def test_a_redirect_from_https_is_refused_and_closed(newurl, fail):
    # The step urllib takes for every 30x answer, without a TLS server:
    # an https:// request redirected to plain http:// or another host
    req = urllib.request.Request(steamgriddb.API + "/search/autocomplete/Diablo")
    req.add_unredirected_header("Authorization", f"Bearer {KEY}")
    body = ErrorBody(fail)
    with pytest.raises(steamgriddb.Error) as e:
        steamgriddb._NoRedirect().redirect_request(
            req, body, 302, "Found", {"Location": newurl}, newurl)
    assert e.value.kind == "network" and KEY not in str(e.value)
    assert "evil" not in str(e.value) and "http://" not in str(e.value)
    assert body.closed


@pytest.mark.parametrize("code, kind", [
    (401, "key"), (403, "key"), (404, None), (500, "network")])
def test_api_errors_from_a_real_answer(local, monkeypatch, code, kind):
    local.answer = lambda h: h.send_body(b'{"success": false}', status=code)
    api_at(local, monkeypatch)
    if kind is None:
        assert steamgriddb.covers(5248, KEY) == []
    else:
        with pytest.raises(steamgriddb.Error) as e:
            steamgriddb.search("Diablo", KEY)
        assert e.value.kind == kind and KEY not in str(e.value)
    assert local.answered.wait(5)
    (_path, headers), = local.requests
    assert headers["Authorization"] == f"Bearer {KEY}"


def test_a_picture_still_follows_a_redirect(local, monkeypatch):
    def answer(handler):
        if handler.path == "/a.png":
            redirect("/b.png")(handler)
        else:
            handler.send_body(PICTURE)
    local.answer = answer
    assert picture_from(local, monkeypatch) == PICTURE
    assert [path for path, _headers in local.requests] == ["/a.png", "/b.png"]
