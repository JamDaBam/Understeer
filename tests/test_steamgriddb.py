import contextlib
import inspect
import io
import json
import logging
import time
import types
import urllib.parse
import urllib.request
from unittest import mock

import pytest

from understeer import fetch, steamgriddb


class Response:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {}           # as a real answer, here without a length

    def read(self, limit=None):
        return self.body if limit is None else self.body[:limit]

    def read1(self, limit):                    # as API answers are read
        piece, self.body = self.body[:limit], self.body[limit:]
        return piece

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


@pytest.mark.parametrize("answer, cause", [
    # fewer bytes than announced: told by fetch.read_body(), see there
    (raw(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + b'{"success": true'),
     fetch.Error),
    (raw(b"NONSENSE\r\n\r\n"), steamgriddb.http.client.HTTPException),
], ids=["cut-short", "bad-status-line"])
def test_a_broken_api_answer_is_a_network_error(local, monkeypatch, answer, cause):
    local.answer = answer
    monkeypatch.setattr(steamgriddb, "API", f"http://{local.address}/api")
    with pytest.raises(steamgriddb.Error) as e:
        steamgriddb.search("Diablo", "secret-key")
    assert e.value.kind == "network" and str(e.value)
    assert "secret-key" not in str(e.value)
    assert isinstance(e.value.__cause__, cause)
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


# -- size and time of API answers ----------------------------------------------
import test_fetch  # noqa: E402

answers = test_fetch.answers       # every HTTP answer opened, see there
MAX = 4 * 1024 * 1024
OK = {"success": True, "data": [{"id": 1, "name": "Diablo"}]}


def ok_json(size=None):
    """A good answer of a search, padded with spaces to size bytes."""
    data = json.dumps(OK).encode()
    return data if size is None else data[:-1] + b" " * (size - len(data)) + b"}"


def chunked(*pieces):
    def answer(handler):
        handler.wfile.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
        for piece in pieces:
            handler.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
        handler.wfile.write(b"0\r\n\r\n")
    return answer


def trickle(data, every):
    def answer(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        for i in range(len(data)):
            handler.wfile.write(data[i:i + 1])
            handler.wfile.flush()
            time.sleep(every)
    return answer


def api_error(server, monkeypatch):
    api_at(server, monkeypatch)
    with pytest.raises(steamgriddb.Error) as e:
        steamgriddb.search("Diablo", KEY)
    assert KEY not in str(e.value) and KEY not in repr(e.value)
    return e.value


def all_closed(answers):
    return bool(answers) and all(a.closed for a in answers)


def test_the_limits_of_api_answers():
    assert steamgriddb.MAX_API_ANSWER == MAX == 4194304
    assert steamgriddb.API_DEADLINE == 30
    assert inspect.signature(steamgriddb._get).parameters["timeout"].default == 15


@pytest.mark.parametrize("answer", [
    lambda h: h.send_body(json.dumps(OK).encode()),
    lambda h: h.send_body(json.dumps(OK).encode(), length=False),
    chunked(json.dumps(OK).encode()[:10], json.dumps(OK).encode()[10:]),
], ids=["length", "no-length", "chunked"])
def test_a_good_api_answer_is_read_and_closed(local, monkeypatch, answers, answer):
    local.answer = answer
    api_at(local, monkeypatch)
    assert steamgriddb.search("Diablo", KEY) == [{"id": 1, "name": "Diablo", "year": None}]
    (_path, headers), = local.requests
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert all_closed(answers)


def test_a_cover_list_is_read(local, monkeypatch, answers):
    local.answer = lambda h: h.send_body(json.dumps({"success": True, "data": [
        {"url": "https://cdn2.steamgriddb.com/grid/a.png", "author": {"name": "Ann"}}]}).encode())
    api_at(local, monkeypatch)
    assert steamgriddb.covers(5248, KEY) == [{
        "url": "https://cdn2.steamgriddb.com/grid/a.png",
        "thumb": "https://cdn2.steamgriddb.com/grid/a.png", "author": "Ann"}]
    assert local.requests[0][0].startswith("/api/grids/game/5248?")
    assert all_closed(answers)


@pytest.mark.parametrize("length", [True, False], ids=["length", "no-length"])
def test_an_api_answer_of_exactly_the_limit_is_read(local, monkeypatch, length):
    local.answer = lambda h: h.send_body(ok_json(MAX), length=length)
    api_at(local, monkeypatch)
    assert steamgriddb.search("Diablo", KEY) == [{"id": 1, "name": "Diablo", "year": None}]


@pytest.mark.parametrize("answer, message", [
    (lambda h: h.send_body(ok_json(MAX + 1)), f"{MAX + 1} bytes announced"),
    (lambda h: h.send_body(ok_json(MAX + 1), length=False), f"more than {MAX} bytes"),
    (chunked(*[b" " * 65536] * 64 + [b"x"]), f"more than {MAX} bytes"),
], ids=["announced", "unannounced", "chunked"])
def test_an_api_answer_too_big_is_refused(local, monkeypatch, answers, answer, message):
    local.answer = answer
    e = api_error(local, monkeypatch)
    assert (e.kind, str(e)) == ("network", message)
    assert isinstance(e.__cause__, fetch.Error)
    assert all_closed(answers)


def test_too_big_an_api_answer_is_not_read_to_its_end():
    """Of 5 MiB without a length, one byte past the limit is read."""
    read = []
    real = Response.read1

    def counting(self, limit):
        piece = real(self, limit)
        read.append(len(piece))
        return piece
    patch, _asked = serve(lambda req: b" " * (5 * 1024 * 1024))
    with mock.patch.object(Response, "read1", counting), patch, \
            pytest.raises(steamgriddb.Error, match=f"more than {MAX} bytes"):
        steamgriddb.search("Diablo", KEY)
    assert sum(read) == MAX + 1


def test_a_slow_api_answer_within_the_deadline_is_read(local, monkeypatch):
    monkeypatch.setattr(steamgriddb, "API_DEADLINE", 30)
    local.answer = trickle(json.dumps(OK, separators=(",", ":")).encode(), 0.01)
    api_at(local, monkeypatch)
    assert steamgriddb.search("Diablo", KEY) == [{"id": 1, "name": "Diablo", "year": None}]


def test_a_trickling_api_answer_stops_at_the_deadline(local, monkeypatch, answers):
    """A byte each 0.2 s would take more than 7 s in all; the deadline
    stops it after about 0.5 s (plus up to one read)."""
    monkeypatch.setattr(steamgriddb, "API_DEADLINE", 0.5)
    local.answer = trickle(json.dumps(OK, separators=(",", ":")).encode(), 0.2)
    start = time.monotonic()
    e = api_error(local, monkeypatch)
    assert (e.kind, str(e)) == ("network", "the download took too long")
    assert time.monotonic() - start < 4
    assert all_closed(answers)
    assert local.answered.wait(10)


@pytest.mark.parametrize("answer, cause", [
    (raw(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + json.dumps(OK).encode()),
     fetch.Error),
    (raw(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n100\r\n{\"su"),
     steamgriddb.http.client.IncompleteRead),
    (raw(b"NONSENSE\r\n\r\n"), steamgriddb.http.client.BadStatusLine),
], ids=["cut-short", "chunk-cut-short", "bad-status-line"])
def test_a_broken_api_answer_is_closed(local, monkeypatch, answers, answer, cause):
    """Cut short also when the JSON that came is whole: not taken."""
    local.answer = answer
    e = api_error(local, monkeypatch)
    assert e.kind == "network" and str(e) and isinstance(e.__cause__, cause)
    assert all(a.closed for a in answers)


@pytest.mark.parametrize("code, kind", [
    (401, "key"), (403, "key"), (404, None), (500, "network")])
def test_an_api_http_error_is_closed(local, monkeypatch, answers, code, kind):
    local.answer = lambda h: h.send_body(b'{"success": false}', status=code)
    api_at(local, monkeypatch)
    if kind is None:
        assert steamgriddb.search("Diablo", KEY) == []
    else:
        with pytest.raises(steamgriddb.Error) as e:
            steamgriddb.search("Diablo", KEY)
        assert e.value.kind == kind and KEY not in str(e.value)
    assert all_closed(answers)


def test_a_refused_api_redirect_is_closed(local, other, monkeypatch, answers):
    local.answer = redirect(f"http://{other.address}/api/x")
    e = api_error(local, monkeypatch)
    assert (e.kind, str(e)) == ("network", "the API answered with a redirect (HTTP 302)")
    assert other.requests == []
    assert all_closed(answers)


def test_json_nested_too_deeply_is_a_network_error(local, monkeypatch, answers):
    """Whether the parser gives up on deep JSON depends on the stack it
    has (see below), so here it is made to: the answer itself is real."""
    parsed = []

    def too_deep(text):
        parsed.append(text)
        raise RecursionError("maximum recursion depth exceeded")
    # only the reference this module uses, not json.loads for everyone
    monkeypatch.setattr(steamgriddb, "json", types.SimpleNamespace(loads=too_deep))
    local.answer = lambda h: h.send_body(json.dumps(OK).encode())
    e = api_error(local, monkeypatch)
    assert (e.kind, str(e)) == ("network", "maximum recursion depth exceeded")
    assert isinstance(e.__cause__, RecursionError)
    assert parsed == [json.dumps(OK)]           # the whole answer came
    (path, headers), = local.requests
    assert path.startswith("/api/search/autocomplete/")
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert all_closed(answers)


@pytest.mark.parametrize("data", [
    b"[" * 100000 + b"]" * 100000,
    b'{"a":' * 100000 + b"1" + b"}" * 100000,
], ids=["lists", "objects"])
def test_deeply_nested_api_json_is_a_network_error(local, monkeypatch, answers, data):
    """Python 3.14 parses this deep JSON or gives up with RecursionError,
    depending on the stack size of the system (e.g. ulimit -s). Either
    way it ends as a network error: it is no answer of the API."""
    assert len(data) < MAX                     # not stopped by the limit
    local.answer = lambda h: h.send_body(data)
    e = api_error(local, monkeypatch)
    assert e.kind == "network" and str(e)
    if isinstance(e.__cause__, RecursionError):
        pass                                   # gave up while parsing
    else:
        assert e.__cause__ is None and str(e) == "bad answer"   # parsed
    assert all_closed(answers)


@pytest.mark.parametrize("data", [b"", b"not json", b"\xff\xfe", b"[]", b"null"])
def test_no_json_is_still_a_network_error(local, monkeypatch, data):
    local.answer = lambda h: h.send_body(data)
    assert api_error(local, monkeypatch).kind == "network"
