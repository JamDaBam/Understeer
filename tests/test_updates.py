import json
import os

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
        "version": "1.10.0", "url": page, "asset": None}


SHA = "a" * 64


def release(name="Understeer-1.10.0-x86_64.AppImage", url=None, digest="sha256:" + SHA):
    url = url or updates.DOWNLOAD_URL + "v1.10.0/" + name
    return {"tag_name": "v1.10.0", "assets": [
        {"name": name + ".zsync", "browser_download_url": url + ".zsync",
         "digest": digest},
        {"name": name, "browser_download_url": url, "digest": digest, "size": 5}]}


def test_asset_of_this_machine():
    asset = updates.parse_latest(release(), "x86_64")["asset"]
    assert asset == {"url": updates.DOWNLOAD_URL
                     + "v1.10.0/Understeer-1.10.0-x86_64.AppImage",
                     "sha256": SHA, "size": 5}
    assert updates.parse_latest(release(), "aarch64")["asset"] is None


@pytest.mark.parametrize("data", [
    release(url="https://evil.example/Understeer-1.10.0-x86_64.AppImage"),
    release(digest=None), release(digest="md5:abc"), release(digest="sha256:xyz"),
])
def test_no_asset_without_our_url_and_checksum(data):
    assert updates.parse_latest(data, "x86_64")["asset"] is None


class Download:
    def __init__(self, body):
        self.body, self.at = body, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        chunk = self.body[self.at:self.at + n]
        self.at += n
        return chunk


def fake_download(monkeypatch, body):
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda req, timeout: Download(body))


def asset_for(body):
    import hashlib
    return {"url": updates.DOWNLOAD_URL + "x", "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest()}


def test_download_and_install(tmp_path, monkeypatch):
    old = tmp_path / "Understeer.AppImage"
    old.write_bytes(b"old")
    body = b"new AppImage" * 1000
    fake_download(monkeypatch, body)
    shares = []
    new = updates.download(asset_for(body), str(old), progress=shares.append)
    assert old.read_bytes() == b"old"               # untouched until installed
    assert shares[-1] == 1.0
    updates.install(new, str(old))
    assert old.read_bytes() == body
    assert os.access(old, os.X_OK)
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_wrong_checksum_changes_nothing(tmp_path, monkeypatch):
    old = tmp_path / "Understeer.AppImage"
    old.write_bytes(b"old")
    fake_download(monkeypatch, b"tampered")
    with pytest.raises(updates.Error):
        updates.download(asset_for(b"genuine"), str(old))
    assert old.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_offline_download(tmp_path, monkeypatch):
    old = tmp_path / "Understeer.AppImage"
    old.write_bytes(b"old")

    def urlopen(req, timeout):
        raise updates.urllib.error.URLError("no network")
    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    with pytest.raises(updates.Error):
        updates.download(asset_for(b"x"), str(old))
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_can_install(tmp_path):
    image = tmp_path / "Understeer.AppImage"
    assert not updates.can_install(str(image))      # not there
    image.write_bytes(b"x")
    assert updates.can_install(str(image))
    assert not updates.can_install(None)


def test_restart_command():
    argv, env = updates.restart_command(
        "/home/a/Apps/Understeer.AppImage", 4242,
        {"APPDIR": "/tmp/.mount_X", "APPIMAGE": "/home/a/Apps/Understeer.AppImage",
         "PYTHONPATH": "/tmp/.mount_X/usr/share/understeer:/home/a/lib",
         "HOME": "/home/a"})
    assert argv[:2] == ["sh", "-c"] and argv[-2:] == ["4242",
                                                     "/home/a/Apps/Understeer.AppImage"]
    assert env == {"PYTHONPATH": "/home/a/lib", "HOME": "/home/a"}


def test_restart_waits_for_the_old_process(tmp_path):
    """The new Understeer starts only after the old one has ended."""
    import subprocess
    import sys
    import time
    mark = tmp_path / "started"
    script = tmp_path / "fake.AppImage"
    script.write_text(f"#!/bin/sh\ntouch {mark}\n")
    script.chmod(0o755)
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
    argv, env = updates.restart_command(str(script), old.pid, {"PATH": os.environ["PATH"]})
    subprocess.Popen(argv, env=env)
    time.sleep(0.3)
    assert not mark.exists()                        # still waiting
    old.wait()
    for _i in range(50):
        if mark.exists():
            break
        time.sleep(0.1)
    assert mark.exists()


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
