import json

import pytest

from understeer import releases, updates


def test_only_a_released_appimage_looks():
    version = releases.latest()
    assert updates.can_check(version, {"APPIMAGE": "/a/Understeer.AppImage"})
    assert not updates.can_check(version, {})                      # from git
    assert not updates.can_check("1.9.5-3-gabc", {"APPIMAGE": "/a/U.AppImage"})


@pytest.mark.parametrize("last, now, expected", [
    (None, 1000, True),
    ("garbage", 1000, True),
    (1000, 1000 + updates.CHECK_EVERY - 1, False),
    (1000, 1000 + updates.CHECK_EVERY, True),
    (5000, 1000, True),                     # the clock went back
])
def test_due(last, now, expected):
    assert updates.due(last, now) is expected


def test_parse_latest():
    page = updates.RELEASES_URL + "/tag/v1.10.0"
    assert updates.parse_latest({"tag_name": "v1.10.0", "html_url": page}) == {
        "version": "1.10.0", "url": page}


@pytest.mark.parametrize("url", [None, "https://evil.example/x",
                                 "https://github.com/someone/else/releases/x"])
def test_parse_latest_opens_only_our_page(url):
    assert updates.parse_latest({"tag_name": "v2.0.0", "html_url": url})["url"] \
        == updates.RELEASES_URL


@pytest.mark.parametrize("data", [None, [], {}, {"tag_name": 5}])
def test_parse_latest_refuses(data):
    with pytest.raises(updates.Error):
        updates.parse_latest(data)


@pytest.mark.parametrize("latest, current, expected", [
    ("1.10.0", "1.9.5", True),
    ("1.9.5", "1.9.5", False),
    ("1.9.4", "1.9.5", False),
    ("2.0", "1.9.5", True),
    ("1.10.0-rc1", "1.9.5", False),
])
def test_is_newer(latest, current, expected):
    assert updates.is_newer(latest, current) is expected


def test_fetch_latest(monkeypatch):
    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, limit):
            return json.dumps({"tag_name": "v9.0.0"}).encode()

    seen = {}

    def urlopen(req, timeout):
        seen["agent"] = req.get_header("User-agent")
        return Answer()

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    assert updates.fetch_latest()["version"] == "9.0.0"
    assert seen["agent"] == "Understeer"           # nothing about the user


def test_fetch_latest_offline(monkeypatch):
    def urlopen(req, timeout):
        raise updates.urllib.error.URLError("no network")
    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    with pytest.raises(updates.Error):
        updates.fetch_latest()
