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


# What GitHub says of every published stable release
STABLE = {"prerelease": False, "draft": False}


def test_parse_latest():
    page = updates.RELEASES_URL + "/tag/v1.10.0"
    assert updates.parse_latest(
        {"tag_name": "v1.10.0", "html_url": page, **STABLE}) == {
            "version": "1.10.0", "url": page, "asset": None}


SHA = "a" * 64


def release(name="Understeer-1.10.0-x86_64.AppImage", url=None, digest="sha256:" + SHA):
    url = url or updates.DOWNLOAD_URL + "v1.10.0/" + name
    return {"tag_name": "v1.10.0", **STABLE, "assets": [
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
    assert updates.parse_latest(
        {"tag_name": "v2.0.0", "html_url": url, **STABLE})["url"] \
        == updates.RELEASES_URL


@pytest.mark.parametrize("data", [None, [], {}, {"tag_name": 5},
                                  {"tag_name": 5, **STABLE}, {**STABLE}])
def test_parse_latest_refuses(data):
    with pytest.raises(updates.Error):
        updates.parse_latest(data)


@pytest.mark.parametrize("tag", ["v2.3.3", "v1.10.0", "v10.0.12"])
def test_parse_latest_takes_stable_tags(tag):
    assert updates.parse_latest({"tag_name": tag, **STABLE})["version"] == tag[1:]


@pytest.mark.parametrize("flags", [
    {"prerelease": True, "draft": False},           # a beta, as it should be
    {"prerelease": False, "draft": True},
    {"prerelease": True, "draft": True},
    {"draft": False},                               # flags missing
    {"prerelease": False},
    {},
    {"prerelease": "false", "draft": False},        # flags of the wrong type
    {"prerelease": 0, "draft": False},
    {"prerelease": False, "draft": None},
    {"prerelease": None, "draft": False},
])
def test_parse_latest_refuses_all_but_published_stable(flags):
    with pytest.raises(updates.Error):
        updates.parse_latest({"tag_name": "v2.4.0", **flags})


@pytest.mark.parametrize("tag", [
    "v2.4.0-beta.1", "v2.4.0-beta.2", "v2.4.0-rc1", "v2.4.0-alpha",
    "v2.4.0-3-gabc", "v2.4.0+build", "2.4.0-beta.1",
])
def test_parse_latest_refuses_test_versions_marked_stable(tag):
    """GitHub marks a beta as stable by mistake: still not offered."""
    with pytest.raises(updates.Error):
        updates.parse_latest({"tag_name": tag, **STABLE})


@pytest.mark.parametrize("tag", [
    "", "v", "2.4.0", "V2.4.0", "v2.4", "v2", "v2.4.0.1", "vv2.4.0",
    " v2.4.0", "v2.4.0 ", "v2.4.0\n", "v2..0", "v-2.4.0", "v2.4.x",
    "v12345.0.0", "v٢.٤.٠", "latest", "../v2.4.0",
])
def test_parse_latest_refuses_malformed_tags(tag):
    with pytest.raises(updates.Error):
        updates.parse_latest({"tag_name": tag, **STABLE})


def test_a_beta_never_reaches_the_banner(monkeypatch):
    """Whatever GitHub answers for a beta, fetch_latest raises instead of
    returning a version that is_newer could compare."""
    for flags in ({"prerelease": True, "draft": False}, STABLE):
        body = json.dumps({"tag_name": "v2.4.0-beta.1", **flags,
                           "html_url": updates.RELEASES_URL + "/tag/v2.4.0-beta.1",
                           "assets": release()["assets"]}).encode()

        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, limit):
                return body

        monkeypatch.setattr(updates.urllib.request, "urlopen",
                            lambda req, timeout: Answer())
        with pytest.raises(updates.Error):
            updates.fetch_latest()


@pytest.mark.parametrize("latest, current, expected", [
    ("1.10.0", "1.9.5", True),
    ("1.9.5", "1.9.5", False),
    ("1.9.4", "1.9.5", False),
    ("2.0", "1.9.5", True),
    ("1.10.0-rc1", "1.9.5", False),
    ("2.4.0", "2.4.0-beta.1", True),        # a beta user gets the stable one
    ("2.4.0", "2.4.0-beta.10", True),
    ("2.4.1", "2.4.0-beta.2", True),
    ("2.3.3", "2.4.0-beta.1", False),       # not back to an older stable
    ("2.4.0-beta.1", "2.3.3", False),       # never a beta, also if newer
    ("2.4.0-beta.2", "2.4.0-beta.1", False),
    ("v2.4.0-beta.1", "v2.3.3", False),
    ("2.4.0", "2.4.0-beta.1-3-gabc", False),  # from git: never compared
    ("9" * 5000, "2.3.3", False),
    ("2.4.0", None, False),
    (None, "2.3.3", False),
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
            return json.dumps({"tag_name": "v9.0.0", **STABLE}).encode()

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


def test_restart_from_home(monkeypatch):
    seen = {}
    monkeypatch.setattr(updates.subprocess, "Popen",
                        lambda argv, **kw: seen.update(kw))
    updates.restart("/home/a/Understeer.AppImage")
    assert seen["cwd"] == os.path.expanduser("~") and seen["start_new_session"]


def test_a_saved_beta_is_no_update(monkeypatch):
    """An answer saved by an older Understeer can't bring a beta to the
    banner either: only parse_latest and is_newer decide, and both refuse."""
    for tag in ("v2.4.0-beta.1", "v2.4.0-beta.2"):
        with pytest.raises(updates.Error):
            updates.parse_latest({"tag_name": tag, **STABLE})
        assert not updates.is_newer(tag[1:], "2.3.3")
    assert updates.is_newer(
        updates.parse_latest({"tag_name": "v2.4.0", **STABLE})["version"],
        "2.4.0-beta.2")
