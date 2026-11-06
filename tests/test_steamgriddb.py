import json
from unittest import mock

import pytest

from understeer import steamgriddb


class Response:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, limit=None):
        return self.body if limit is None else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serve(answer):
    """Patch urlopen; answer(request) returns a body or raises."""
    asked = []

    def urlopen(req, timeout):
        asked.append(req)
        return Response(answer(req))
    return mock.patch.object(steamgriddb.urllib.request, "urlopen", urlopen), asked


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
