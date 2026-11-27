import http.client
import inspect
import io
import json
import os
import pathlib
import socket
import time

import pytest

from understeer import releases, updates

# A file that looks like an AppImage (type 2): ELF, and "AI\x02" at byte 8.
# Only what the format check reads; nothing that would run.
APPIMAGE_HEAD = b"\x7fELF\x02\x01\x01\x00AI\x02"
OLD = APPIMAGE_HEAD + b"old"            # the AppImage before an update


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
    assert asset == {"version": "1.10.0", "name": "Understeer-1.10.0-x86_64.AppImage",
                     "url": updates.DOWNLOAD_URL
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
    headers = {}

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

    read1 = read


def fake_download(monkeypatch, body):
    monkeypatch.setattr(updates.fetch, "open_https",
                        lambda req, timeout: Download(body))


def asset_for(body, version="1.10.0"):
    """The asset of our release of version for this machine, with body."""
    import hashlib
    name = f"Understeer-{version}-{updates.platform.machine()}.AppImage"
    return {"version": version, "name": name,
            "url": f"{updates.DOWNLOAD_URL}v{version}/{name}", "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest()}


def test_download_and_install(tmp_path, monkeypatch):
    old = tmp_path / "Understeer.AppImage"
    old.write_bytes(OLD)
    body = b"new AppImage" * 1000
    fake_download(monkeypatch, body)
    shares = []
    new = updates.download(asset_for(body), str(old), progress=shares.append)
    assert old.read_bytes() == OLD               # untouched until installed
    assert shares[-1] == 1.0
    updates.install(new, str(old))
    assert old.read_bytes() == body
    assert os.access(old, os.X_OK)
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_wrong_checksum_changes_nothing(tmp_path, monkeypatch):
    old = tmp_path / "Understeer.AppImage"
    old.write_bytes(OLD)
    fake_download(monkeypatch, b"tampered")
    with pytest.raises(updates.Error, match="checksum") as e:
        updates.download(asset_for(b"genuine!"), str(old))      # the same size
    assert e.value.kind == updates.CHECKSUM
    assert old.read_bytes() == OLD
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_offline_download(tmp_path, monkeypatch):
    old = tmp_path / "Understeer.AppImage"
    old.write_bytes(OLD)

    def open_https(req, timeout):
        raise updates.urllib.error.URLError("no network")
    monkeypatch.setattr(updates.fetch, "open_https", open_https)
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(b"x"), str(old))
    assert e.value.kind == updates.DOWNLOAD
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_can_install(tmp_path):
    image = tmp_path / "Understeer.AppImage"
    assert not updates.can_install(str(image))      # not there
    image.write_bytes(b"x")
    assert not updates.can_install(str(image))      # no AppImage
    image.write_bytes(OLD)
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
    old = waiter = None
    try:
        old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
        argv, env = updates.restart_command(str(script), old.pid,
                                            {"PATH": os.environ["PATH"]})
        waiter = subprocess.Popen(argv, env=env)
        time.sleep(0.3)
        assert not mark.exists()                    # still waiting
        old.wait(timeout=5)
        # The waiter becomes the fake AppImage, which ends once it has
        # left its mark
        waiter.wait(timeout=5)
        assert mark.exists()
    finally:
        # Only the processes started here; each is reaped, also when the
        # test failed half-way
        for process in (waiter, old):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)


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
        _github(monkeypatch, answer=_Answer(body))
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
    seen = _github(monkeypatch, answer=_Answer(
        json.dumps({"tag_name": "v9.0.0", **STABLE}).encode()))
    assert updates.fetch_latest()["version"] == "9.0.0"
    assert seen[0].get_header("User-agent") == "Understeer"   # nothing about the user


def test_fetch_latest_offline(monkeypatch):
    _github(monkeypatch, error=updates.urllib.error.URLError("no network"))
    with pytest.raises(updates.Error) as e:
        updates.fetch_latest()
    assert e.value.kind == updates.UNREACHABLE


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


# -- fetch_latest with broken answers: always updates.Error -----------------

class _Answer:
    """An answer without Content-Length; read1 hands out its body."""
    headers = {}
    status = 200

    def geturl(self):
        return updates.LATEST_URL

    def __init__(self, body=b"", error=None):
        self.body, self.error = body, error

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read1(self, limit):
        if self.error is not None:
            raise self.error
        piece, self.body = self.body[:limit], self.body[limit:]
        return piece


def _github(monkeypatch, answer=None, error=None):
    """The opener of fetch.get() answers with answer, or raises error;
    beta discovery and downloading must not be touched. Returns the
    requests made."""
    requests = []

    class Opener:
        def open(self, req, timeout):
            assert req.full_url == updates.LATEST_URL
            requests.append(req)
            if error is not None:
                raise error
            return answer

    def refuse(*a, **k):
        raise AssertionError("not part of the stable look")
    monkeypatch.setattr(updates.fetch, "_opener", Opener)
    monkeypatch.setattr(updates.urllib.request, "urlopen", refuse)
    for name in ("fetch_beta", "newest_beta", "download", "install", "restart"):
        monkeypatch.setattr(updates, name, refuse)
    return requests


@pytest.mark.parametrize("error", [
    http.client.BadStatusLine("garbage"),
    http.client.LineTooLong("header line"),
    http.client.IncompleteRead(b"{", 100),
    http.client.HTTPException("broken"),
    http.client.RemoteDisconnected("closed"),
    updates.urllib.error.URLError("no network"),
    updates.urllib.error.HTTPError(updates.LATEST_URL, 500, "error", {}, None),
    socket.timeout("timed out"), TimeoutError(), ConnectionResetError(),
])
def test_fetch_latest_broken_connection(monkeypatch, error):
    _github(monkeypatch, error=error)
    with pytest.raises(updates.Error) as e:
        updates.fetch_latest()
    assert str(e.value)                     # something to log


@pytest.mark.parametrize("error", [
    http.client.IncompleteRead(b'{"tag_name": "v9', 100),
    TimeoutError("timed out"), ConnectionResetError("reset"),
])
def test_fetch_latest_broken_while_reading(monkeypatch, error):
    _github(monkeypatch, answer=_Answer(error=error))
    with pytest.raises(updates.Error):
        updates.fetch_latest()


@pytest.mark.parametrize("body", [
    b"[" * 100000 + b"]" * 100000,           # nested too deeply
    b'{"a":' * 50000 + b"1" + b"}" * 50000,
    b"[" + b"9" * 5000 + b"]",                # number too long
    b"\xff\xfe\x00 garbage", b"", b"not json", b"<html>rate limit</html>",
    b"null", b"[]", b'"v9.0.0"', b"5",
    b'{"tag_name": "v9.0.0", "prerelease": false, "draft": false' + b" " * 300000 + b"}",
], ids=["nested lists", "nested objects", "long number", "no UTF-8", "empty",
        "no json", "html", "null", "list", "text", "number", "cut at 256 KB"])
def test_fetch_latest_broken_answer(monkeypatch, body):
    _github(monkeypatch, answer=_Answer(body))
    with pytest.raises(updates.Error):
        updates.fetch_latest()


@pytest.mark.parametrize("flags", [
    {"prerelease": True, "draft": False}, {"prerelease": False, "draft": True},
])
def test_fetch_latest_refuses_prereleases_and_drafts(monkeypatch, flags):
    _github(monkeypatch, answer=_Answer(
        json.dumps({"tag_name": "v9.0.0", **flags}).encode()))
    with pytest.raises(updates.Error):
        updates.fetch_latest()


def test_fetch_latest_stable_release_with_asset(monkeypatch):
    data = {**release(), "html_url": updates.RELEASES_URL + "/tag/v1.10.0"}
    _github(monkeypatch, answer=_Answer(json.dumps(data).encode()))
    monkeypatch.setattr(updates.platform, "machine", lambda: "x86_64")
    assert updates.fetch_latest() == {
        "version": "1.10.0", "url": updates.RELEASES_URL + "/tag/v1.10.0",
        "asset": {"version": "1.10.0", "name": "Understeer-1.10.0-x86_64.AppImage",
                  "url": updates.DOWNLOAD_URL
                  + "v1.10.0/Understeer-1.10.0-x86_64.AppImage",
                  "sha256": SHA, "size": 5}}


@pytest.mark.parametrize("assets", [5, True, 1.5, "abc", {"x": 1}, None, [],
                                    [5, None, "x", []]])
def test_assets_that_are_no_list_of_files(assets):
    """No exception, just no AppImage to update from."""
    found = updates.parse_latest({"tag_name": "v2.3.3", **STABLE,
                                  "assets": assets}, "x86_64")
    assert found["version"] == "2.3.3" and found["asset"] is None


class _ErrorBody(io.BytesIO):
    """The answer an HTTPError holds; fail: closing it raises that."""

    def __init__(self, fail=None):
        super().__init__(b"error")
        self.fail = fail

    def close(self):
        super().close()
        if self.fail:
            raise self.fail


def _http_error(url, code, body):
    return updates.urllib.error.HTTPError(url, code, "Not Found", {}, body)


@pytest.mark.parametrize("fail", [None, OSError("gone"),
                                  ValueError("I/O operation on closed file")])
def test_fetch_latest_closes_an_http_error(monkeypatch, fail):
    body = _ErrorBody(fail)
    error = _http_error(updates.LATEST_URL, 404, body)
    _github(monkeypatch, error=error)
    with pytest.raises(updates.Error) as e:
        updates.fetch_latest()
    assert str(e.value) == "HTTP 404" and e.value.kind == updates.UNREACHABLE
    assert isinstance(e.value.__cause__, updates.fetch.Error)
    assert e.value.__cause__.code == 404
    assert body.closed


@pytest.mark.parametrize("fail", [None, OSError("gone"),
                                  ValueError("I/O operation on closed file")])
def test_download_closes_an_http_error(tmp_path, monkeypatch, fail):
    old = tmp_path / "Understeer.AppImage"
    old.write_bytes(OLD)
    body = _ErrorBody(fail)
    asset = asset_for(b"x")

    def open_https(req, timeout):
        raise _http_error(req.full_url, 404, body)
    monkeypatch.setattr(updates.fetch, "open_https", open_https)
    with pytest.raises(updates.Error) as e:
        updates.download(asset, str(old))
    assert str(e.value) == "HTTP Error 404: Not Found"
    assert isinstance(e.value.__cause__, updates.urllib.error.HTTPError)
    assert body.closed
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_download_closes_an_answer_refused_while_reading(tmp_path, monkeypatch):
    old = tmp_path / "Understeer.AppImage"
    old.write_bytes(OLD)
    closed = []

    class Answer(Download):
        def __exit__(self, *exc):
            closed.append(exc[0])
            return False
    monkeypatch.setattr(updates.fetch, "open_https",
                        lambda req, timeout: Answer(b"too big"))
    with pytest.raises(updates.Error, match="bigger") as e:
        updates.download(asset_for(b"x"), str(old))     # one byte said
    assert e.value.kind == updates.SIZE
    assert closed == [updates.Error]
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


# -- broken answers, from a real server on 127.0.0.1 --------------------------
import test_fetch  # noqa: E402

NEW_APPIMAGE = b"new AppImage" * 1000


@pytest.fixture
def local():
    s = Server()
    yield s
    s.close()


https_servers = test_fetch.https_servers   # https:// hosts served locally


@pytest.fixture
def github(https_servers):
    """A local server for https://github.com, where the real DOWNLOAD_URL
    points; no name is looked up, nothing leaves the computer."""
    return https_servers("github.com")


def asset_path(body=NEW_APPIMAGE):
    return updates.urllib.parse.urlsplit(asset_for(body)["url"]).path


def download_from(tmp_path, body=NEW_APPIMAGE):
    old = tmp_path / "Understeer.AppImage"
    old.write_bytes(OLD)
    return updates.download(asset_for(body), str(old)), old


@pytest.mark.parametrize("broken", [
    b"NONSENSE\r\n\r\n",                                        # no status line
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3e8\r\n" + b"n" * 100,
], ids=["bad-status-line", "chunk-cut-short"])
def test_a_broken_download_is_an_update_error(github, tmp_path, broken):
    github.answer = lambda h: h.wfile.write(broken)
    with pytest.raises(updates.Error) as e:
        download_from(tmp_path)
    assert str(e.value)                                 # shown in the dialog
    assert isinstance(e.value.__cause__, http.client.HTTPException)
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]   # no .part
    assert e.value.kind == updates.DOWNLOAD
    assert (tmp_path / "Understeer.AppImage").read_bytes() == OLD
    assert github.answered.wait(5)


def test_a_download_cut_short_is_refused(github, tmp_path):
    github.answer = lambda h: h.wfile.write(
        b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(NEW_APPIMAGE)
        + NEW_APPIMAGE[:100])
    with pytest.raises(updates.Error, match="100 of 12000 bytes") as e:
        download_from(tmp_path)
    assert e.value.kind == updates.SIZE
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


@pytest.mark.parametrize("length", [True, False], ids=["length", "no-length"])
def test_a_whole_download_is_taken(github, tmp_path, length):
    github.answer = lambda h: h.send_body(NEW_APPIMAGE, length=length)
    new, old = download_from(tmp_path)
    assert pathlib.Path(new.path).read_bytes() == NEW_APPIMAGE
    assert old.read_bytes() == OLD               # untouched until installed
    assert [p for p, _h in github.requests] == [asset_path()]


def test_an_https_redirect_is_followed(github, https_servers, tmp_path):
    """GitHub sends release files from another host; the checksum and the
    size of the fresh answer tell that the file is ours."""
    files = https_servers("release-assets.example")

    def answer(h):
        h.send_response(302)
        h.send_header("Location", "https://release-assets.example/stored")
        h.send_header("Content-Length", "0")
        h.end_headers()
    github.answer = answer
    files.answer = lambda h: h.send_body(NEW_APPIMAGE)
    new, _old = download_from(tmp_path)
    assert pathlib.Path(new.path).read_bytes() == NEW_APPIMAGE
    assert [p for p, _h in github.requests] == [asset_path()]
    assert [p for p, _h in files.requests] == ["/stored"]


@pytest.mark.parametrize("location", [
    "http://release-assets.example/stored", "ftp://release-assets.example/stored",
    "file:///etc/passwd"])
def test_a_redirect_away_from_https_is_refused(github, https_servers, tmp_path,
                                               location):
    files = https_servers("release-assets.example")

    def answer(h):
        h.send_response(302)
        h.send_header("Location", location)
        h.send_header("Content-Length", "0")
        h.end_headers()
    github.answer = answer
    with pytest.raises(updates.Error) as e:
        download_from(tmp_path)
    assert e.value.kind == updates.DOWNLOAD
    assert files.requests == []
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


@pytest.mark.parametrize("extra", [1, 64 * 1024, 5 * 1024 * 1024])
def test_a_download_bigger_than_said_stops_at_once(github, tmp_path, extra):
    """Refused one byte past the size GitHub gave, not read on to
    MAX_DOWNLOAD."""
    sent = []

    def answer(h):
        h.send_response(200)
        h.end_headers()
        try:
            for _i in range(0, len(NEW_APPIMAGE) + extra, 4096):
                h.wfile.write(b"x" * 4096)
                sent.append(4096)
        except OSError:
            pass                            # the download hung up
    github.answer = answer
    with pytest.raises(updates.Error, match="bigger") as e:
        download_from(tmp_path)
    assert e.value.kind == updates.SIZE
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


# -- fetch_latest from a real server on 127.0.0.1: size and time -------------
Server = test_fetch.Server

answers = test_fetch.answers       # every HTTP answer opened, see there


def latest_json(size=None):
    """The answer of a stable release, padded with spaces to size bytes."""
    body = json.dumps({"tag_name": "v9.0.0", **STABLE}).encode()
    return body if size is None else body[:-1] + b" " * (size - len(body)) + b"}"


def latest_from(server, monkeypatch):
    monkeypatch.setattr(updates, "LATEST_URL", f"http://{server.address}/latest")
    return updates.fetch_latest()


def latest_error(server, monkeypatch):
    with pytest.raises(updates.Error) as e:
        latest_from(server, monkeypatch)
    return e.value


def test_the_limits_of_the_stable_look():
    assert updates.MAX_LATEST_ANSWER == 262144
    assert updates.LATEST_DEADLINE == 20
    assert inspect.signature(updates.fetch_latest).parameters["timeout"].default == 10


@pytest.mark.parametrize("answer", [
    lambda h: h.send_body(latest_json()),
    lambda h: h.send_body(latest_json(), length=False),
    lambda h: (h.send_response(200), h.send_header("Transfer-Encoding", "chunked"),
               h.end_headers(), h.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n"
                                              % (len(latest_json()), latest_json()))),
], ids=["length", "no-length", "chunked"])
def test_a_whole_latest_answer_is_read_and_closed(local, monkeypatch, answers, answer):
    local.answer = answer
    assert latest_from(local, monkeypatch)["version"] == "9.0.0"
    assert len(answers) == 1 and answers[0].closed


@pytest.mark.parametrize("length", [True, False], ids=["length", "no-length"])
def test_a_latest_answer_of_exactly_the_limit_is_read(local, monkeypatch, length):
    local.answer = lambda h: h.send_body(latest_json(262144), length=length)
    assert latest_from(local, monkeypatch)["version"] == "9.0.0"


@pytest.mark.parametrize("length, message", [
    (True, "262145 bytes announced"), (False, "more than 262144 bytes"),
], ids=["length", "no-length"])
def test_a_latest_answer_too_big_is_refused(local, monkeypatch, answers, length, message):
    """Not cut at the limit and then taken for broken JSON."""
    local.answer = lambda h: h.send_body(latest_json(262145), length=length)
    e = latest_error(local, monkeypatch)
    assert str(e) == message and isinstance(e.__cause__, updates.fetch.Error)
    assert len(answers) == 1 and answers[0].closed


def test_a_latest_answer_cut_short_is_refused(local, monkeypatch, answers):
    def short(h):
        h.send_response(200)
        h.send_header("Content-Length", "1000")
        h.end_headers()
        h.wfile.write(latest_json())
        h.close_connection = True
    local.answer = short
    e = latest_error(local, monkeypatch)
    assert str(e) == f"{len(latest_json())} of 1000 bytes came"
    assert len(answers) == 1 and answers[0].closed


def trickle(body, every=0.2):
    def answer(h):
        h.send_response(200)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        for i in range(len(body)):
            h.wfile.write(body[i:i + 1])
            h.wfile.flush()
            time.sleep(every)
    return answer


def test_a_trickling_latest_answer_stops_at_the_deadline(local, monkeypatch, answers):
    """Without it, the whole answer (a byte each 0.2 s) would be waited for."""
    monkeypatch.setattr(updates, "LATEST_DEADLINE", 0.5)
    local.answer = trickle(latest_json())
    start = time.monotonic()
    e = latest_error(local, monkeypatch)
    # one read of the socket (here 0.2 s) can go past the deadline
    assert str(e) == "the download took too long"
    assert time.monotonic() - start < 0.5 + 0.6
    assert len(answers) == 1 and answers[0].closed
    assert local.answered.wait(5)


def test_a_slow_latest_answer_within_the_deadline_is_read(local, monkeypatch):
    monkeypatch.setattr(updates, "LATEST_DEADLINE", 10)
    body = json.dumps({"tag_name": "v9.0.0", **STABLE}, separators=(",", ":")).encode()
    local.answer = trickle(body, every=0.01)
    assert latest_from(local, monkeypatch)["version"] == "9.0.0"


@pytest.mark.parametrize("location", [
    "/moved",                                       # the same server
    "/repos/JamDaBam/Understeer/releases/latest",   # even the same path
    "https://api.github.com.evil.example/latest",   # another host
    "http://127.0.0.1:9/latest",                    # away from HTTPS
])
def test_the_stable_look_follows_no_redirect(local, monkeypatch, answers, location):
    """Only the fixed address of the API answers; a redirect is refused
    before anything is asked of where it points."""
    def answer(h):
        h.send_response(302)
        h.send_header("Location", location)
        h.send_header("Content-Length", "0")
        h.end_headers()
    local.answer = answer
    e = latest_error(local, monkeypatch)
    assert e.kind == updates.INVALID
    assert [path for path, _headers in local.requests] == ["/latest"]
    assert answers and all(a.closed for a in answers)


@pytest.mark.parametrize("status", [403, 404, 500])
def test_a_latest_http_error_is_closed(local, monkeypatch, answers, status):
    local.answer = lambda h: h.send_body(b"error", status)
    e = latest_error(local, monkeypatch)
    assert str(e) == f"HTTP {status}" and e.kind == updates.UNREACHABLE
    assert isinstance(e.__cause__, updates.fetch.Error) and e.__cause__.code == status
    assert len(answers) == 1 and answers[0].closed


def test_latest_reading_errors_stay_update_errors(local, monkeypatch, answers):
    local.answer = lambda h: h.wfile.write(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n10\r\nabc")
    e = latest_error(local, monkeypatch)
    assert isinstance(e.__cause__, updates.fetch.Error) and str(e)
    assert e.kind == updates.UNREACHABLE
    assert len(answers) == 1 and answers[0].closed


# -- U-006: only a fresh, checked answer of GitHub leads to an installation --
# Stored update data (app.json, maybe from an imported backup) can name any
# file together with its checksum: a checksum alone proves nothing then.
EVIL = b"not an Understeer"
EVIL_SHA = __import__("hashlib").sha256(EVIL).hexdigest()


def official(version="9.0.0", machine="x86_64"):
    name = f"Understeer-{version}-{machine}.AppImage"
    return name, f"{updates.DOWNLOAD_URL}v{version}/{name}"


@pytest.fixture
def opened(monkeypatch):
    """Every address a download opens; each answers EVIL. No request
    leaves: the old way of opening (urlopen) and the new one are replaced."""
    seen = []

    def answer(req, timeout=None):
        seen.append(getattr(req, "full_url", req))
        return Download(EVIL)
    monkeypatch.setattr(updates.urllib.request, "urlopen", answer)
    monkeypatch.setattr(updates.fetch, "open_https", answer)
    monkeypatch.setattr(updates.platform, "machine", lambda: "x86_64")
    return seen


@pytest.mark.parametrize("url", [
    "https://evil.example/Understeer-9.0.0-x86_64.AppImage",
    "http://github.com/JamDaBam/Understeer/releases/download/v9.0.0/"
    "Understeer-9.0.0-x86_64.AppImage",
    "file:///tmp/Understeer-9.0.0-x86_64.AppImage",
    updates.DOWNLOAD_URL + "v9.0.0/../../../../someone/else/releases/download/"
    "v9.0.0/Understeer-9.0.0-x86_64.AppImage",
    updates.DOWNLOAD_URL + "v9.0.0/Understeer-9.0.0-aarch64.AppImage",
    updates.DOWNLOAD_URL + "v8.0.0/Understeer-9.0.0-x86_64.AppImage",
], ids=["other-host", "http", "file", "dot-dot", "other-machine", "other-tag"])
def test_a_stored_asset_leads_to_no_download(tmp_path, opened, url):
    image = tmp_path / "Understeer.AppImage"
    image.write_bytes(OLD)
    asset = {"version": "9.0.0", "name": "Understeer-9.0.0-x86_64.AppImage",
             "url": url, "sha256": EVIL_SHA, "size": len(EVIL)}
    with pytest.raises(updates.Error):
        updates.install(updates.download(asset, str(image)), str(image))
    assert opened == []                             # refused before asking
    assert image.read_bytes() == OLD
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_a_file_and_its_own_checksum_are_not_installed(tmp_path, monkeypatch):
    """The attack as it would happen: stored data point at a file of the
    attacker's with the matching checksum."""
    monkeypatch.setattr(updates.platform, "machine", lambda: "x86_64")
    evil = tmp_path / "Understeer-9.0.0-x86_64.AppImage"
    evil.write_bytes(EVIL)
    image = tmp_path / "Understeer.AppImage"
    image.write_bytes(OLD)
    stored = {"version": "9.0.0", "url": updates.RELEASES_URL + "/tag/v9.0.0",
              "asset": {"url": evil.as_uri(), "sha256": EVIL_SHA, "size": len(EVIL)}}
    with pytest.raises(updates.Error):
        updates.install(updates.download(stored["asset"], str(image)), str(image))
    assert image.read_bytes() == OLD


def fresh(version="9.0.0", body=NEW_APPIMAGE, machine="x86_64"):
    """What fetch_latest gives for a checked stable release with its file."""
    import hashlib
    name, url = official(version, machine)
    return {"version": version, "url": f"{updates.RELEASES_URL}/tag/v{version}",
            "asset": {"version": version, "name": name, "url": url,
                      "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}}


@pytest.fixture
def updating(tmp_path, monkeypatch):
    """update_to() with GitHub and the download replaced: answer is what the
    fresh look gives (or raises), downloads lists the assets downloaded."""
    monkeypatch.setattr(updates.platform, "machine", lambda: "x86_64")
    image = tmp_path / "Understeer.AppImage"
    image.write_bytes(OLD)
    state = {"answer": fresh(), "downloads": [], "image": image}

    def fetch_latest(timeout=10):
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"]

    def download(asset, appimage, timeout=30, progress=None, cancelled=None):
        state["downloads"].append(asset)
        new = tmp_path / ".new"
        new.write_bytes(NEW_APPIMAGE)
        st = new.stat()
        return updates.Downloaded(str(new), st.st_dev, st.st_ino,
                                  updates._target(appimage))
    monkeypatch.setattr(updates, "fetch_latest", fetch_latest)
    monkeypatch.setattr(updates, "download", download)
    return state


def test_only_the_fresh_answer_is_downloaded(updating):
    result = updates.update_to("9.0.0", "2.3.3", str(updating["image"]))
    assert updating["downloads"] == [fresh()["asset"]]
    assert result["version"] == "9.0.0"
    assert updating["image"].read_bytes() == NEW_APPIMAGE


def test_no_download_when_github_cant_be_asked(updating):
    updating["answer"] = updates.Error("offline", updates.UNREACHABLE)
    with pytest.raises(updates.Error) as e:
        updates.update_to("9.0.0", "2.3.3", str(updating["image"]))
    assert e.value.kind == updates.UNREACHABLE
    assert updating["downloads"] == []
    assert updating["image"].read_bytes() == OLD


@pytest.mark.parametrize("size", [True, "5", 0, -1, 200 * 1024 * 1024 + 1, None, 1.5])
def test_an_asset_needs_a_real_size(size):
    data = release()
    data["assets"][1]["size"] = size
    assert updates.parse_latest(data, "x86_64")["asset"] is None


def test_a_download_stops_past_its_size(tmp_path, monkeypatch):
    """Not read on up to MAX_DOWNLOAD once more than the size came."""
    monkeypatch.setattr(updates.platform, "machine", lambda: "x86_64")
    image = tmp_path / "Understeer.AppImage"
    image.write_bytes(OLD)
    big = Download(b"x" * (2 * 1024 * 1024))
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda req, timeout: big)
    monkeypatch.setattr(updates.fetch, "open_https", lambda req, timeout: big)
    with pytest.raises(updates.Error):
        updates.download(fresh(body=b"x" * 10)["asset"], str(image))
    assert big.at <= 64 * 1024
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_stored_update_data_are_only_shown():
    name, url = official()
    stored = {"version": "9.0.0", "url": updates.RELEASES_URL + "/tag/v9.0.0",
              "asset": {"url": url, "sha256": EVIL_SHA, "size": 5}}
    assert updates.checked_latest(stored) == {
        "version": "9.0.0", "url": updates.RELEASES_URL + "/tag/v9.0.0",
        "has_asset": True}


# -- what is kept of a look, and what it is good for -------------------------
PAGE = updates.RELEASES_URL + "/tag/v9.0.0"


@pytest.mark.parametrize("stored, expected", [
    ({"version": "9.0.0", "url": PAGE, "has_asset": True},
     {"version": "9.0.0", "url": PAGE, "has_asset": True}),
    ({"version": "9.0.0", "url": PAGE, "has_asset": False},
     {"version": "9.0.0", "url": PAGE, "has_asset": False}),
    # as older versions kept it, with the whole asset: only shown
    ({"version": "9.0.0", "url": PAGE, "asset": {"url": "https://evil.example/x"}},
     {"version": "9.0.0", "url": PAGE, "has_asset": True}),
    ({"version": "9.0.0", "url": PAGE, "asset": None},
     {"version": "9.0.0", "url": PAGE, "has_asset": False}),
    # another page than the release's own: the list of releases instead
    ({"version": "9.0.0", "url": "https://evil.example/", "has_asset": True},
     {"version": "9.0.0", "url": updates.RELEASES_URL, "has_asset": True}),
    ({"version": "9.0.0", "url": "http://github.com/JamDaBam/Understeer/releases/tag/v9.0.0"},
     {"version": "9.0.0", "url": updates.RELEASES_URL, "has_asset": False}),
    ({"version": "9.0.0", "url": "file:///etc/passwd"},
     {"version": "9.0.0", "url": updates.RELEASES_URL, "has_asset": False}),
    ({"version": "9.0.0", "url": updates.RELEASES_URL + "/tag/v8.0.0"},
     {"version": "9.0.0", "url": updates.RELEASES_URL, "has_asset": False}),
    ({"version": "9.0.0", "url": PAGE + "/../../../../evil"},
     {"version": "9.0.0", "url": updates.RELEASES_URL, "has_asset": False}),
    ({"version": "9.0.0", "url": 5, "has_asset": "yes"},
     {"version": "9.0.0", "url": updates.RELEASES_URL, "has_asset": False}),
])
def test_stored_update_data_as_shown(stored, expected):
    assert updates.checked_latest(stored) == expected


@pytest.mark.parametrize("stored", [
    None, [], "9.0.0", 5, {}, {"version": 9}, {"version": "9.0"},
    {"version": "9.0.0-beta.1"}, {"version": "v9.0.0"}, {"version": "../9.0.0"},
    {"version": "9.0.0\n"}, {"version": "٩.0.0"},
])
def test_broken_stored_update_data_are_ignored(stored):
    assert updates.checked_latest(stored) is None


def test_what_is_kept_of_a_look():
    assert updates.for_display(fresh()) == {"version": "9.0.0", "url": PAGE,
                                            "has_asset": True}
    assert updates.for_display({**fresh(), "asset": None})["has_asset"] is False


# -- the fresh look decides ---------------------------------------------------
@pytest.mark.parametrize("answer, kind", [
    (fresh("9.0.0"), None),
    (fresh("2.3.3"), updates.NOT_NEWER),            # no update any more
    (fresh("2.0.0"), updates.NOT_NEWER),
    (fresh("9.1.0"), updates.CHANGED),              # another one is newest now
    (fresh("8.0.0"), updates.CHANGED),
    ({**fresh(), "asset": None}, updates.NO_ASSET),
    (fresh(machine="aarch64"), updates.NO_ASSET),   # not for this computer
    ({**fresh(), "asset": {**fresh()["asset"], "url": "https://evil.example/x"}},
     updates.NO_ASSET),
    ({**fresh(), "asset": {**fresh()["asset"], "size": True}}, updates.NO_ASSET),
    ({**fresh(), "asset": fresh("8.0.0")["asset"]}, updates.NO_ASSET),
])
def test_prepare_update(monkeypatch, answer, kind):
    monkeypatch.setattr(updates.platform, "machine", lambda: "x86_64")
    if kind is None:
        assert updates.prepare_update(answer, "9.0.0", "2.3.3") == fresh()["asset"]
        return
    with pytest.raises(updates.Error) as e:
        updates.prepare_update(answer, "9.0.0", "2.3.3")
    assert e.value.kind == kind and e.value.latest is answer


@pytest.mark.parametrize("answer, kind", [
    (updates.Error("offline", updates.UNREACHABLE), updates.UNREACHABLE),
    (updates.Error("the answer is no JSON"), updates.INVALID),
    (updates.Error("the release is no published stable release"), updates.INVALID),
    (fresh("2.3.3"), updates.NOT_NEWER),
    (fresh("9.1.0"), updates.CHANGED),
    ({**fresh(), "asset": None}, updates.NO_ASSET),
])
def test_no_download_unless_the_fresh_look_allows_it(updating, answer, kind):
    """An answer that can't be read is never taken for one that says there
    is no update: INVALID and UNREACHABLE stay apart from NOT_NEWER."""
    updating["answer"] = answer
    with pytest.raises(updates.Error) as e:
        updates.update_to("9.0.0", "2.3.3", str(updating["image"]))
    assert e.value.kind == kind
    assert updating["downloads"] == []
    assert updating["image"].read_bytes() == OLD


def test_kinds_of_a_failed_stable_look(monkeypatch):
    _github(monkeypatch, answer=_Answer(b"not json"))
    with pytest.raises(updates.Error) as e:
        updates.fetch_latest()
    assert e.value.kind == updates.INVALID
    _github(monkeypatch, answer=_Answer(json.dumps(
        {"tag_name": "v9.0.0", "prerelease": True, "draft": False}).encode()))
    with pytest.raises(updates.Error) as e:
        updates.fetch_latest()
    assert e.value.kind == updates.INVALID
    _github(monkeypatch, answer=_Answer(b" " * (updates.MAX_LATEST_ANSWER + 1)))
    with pytest.raises(updates.Error) as e:
        updates.fetch_latest()
    assert e.value.kind == updates.INVALID


@pytest.mark.parametrize("size", [True, False, "5", 0, -1, 1.5, None,
                                  200 * 1024 * 1024 + 1])
def test_no_download_without_a_real_size(tmp_path, opened, size):
    image = tmp_path / "Understeer.AppImage"
    image.write_bytes(OLD)
    with pytest.raises(updates.Error) as e:
        updates.download({**fresh()["asset"], "size": size}, str(image))
    assert e.value.kind == updates.NO_ASSET and opened == []


def test_called_off_before_the_download(updating):
    with pytest.raises(updates.Error) as e:
        updates.update_to("9.0.0", "2.3.3", str(updating["image"]),
                          cancelled=lambda: True)
    assert e.value.kind == updates.CANCELLED and updating["downloads"] == []
    assert updating["image"].read_bytes() == OLD


def test_called_off_after_the_download(updating, tmp_path):
    asks = []

    def cancelled():
        asks.append(1)
        return len(asks) > 1            # switched off while downloading
    with pytest.raises(updates.Error) as e:
        updates.update_to("9.0.0", "2.3.3", str(updating["image"]), cancelled=cancelled)
    assert e.value.kind == updates.CANCELLED
    assert updating["image"].read_bytes() == OLD
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_called_off_while_downloading(tmp_path, monkeypatch):
    fake_download(monkeypatch, NEW_APPIMAGE)
    image = tmp_path / "Understeer.AppImage"
    image.write_bytes(OLD)
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(image), cancelled=lambda: True)
    assert e.value.kind == updates.CANCELLED
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_a_failed_installation(tmp_path, monkeypatch):
    new = tmp_path / "new"
    new.write_bytes(NEW_APPIMAGE)
    st = new.stat()
    image = old_image(tmp_path)

    def broken(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(updates.os, "replace", broken)
    with pytest.raises(updates.Error) as e:
        updates.install(updates.Downloaded(str(new), st.st_dev, st.st_ino,
                                           updates._target(str(image))), str(image))
    assert e.value.kind == updates.INSTALL and not new.exists()
    assert image.read_bytes() == OLD


def test_nothing_to_install_over_a_folder(tmp_path, monkeypatch):
    target = tmp_path / "Understeer.AppImage"
    target.mkdir()                              # a folder can't be replaced
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(target), clock=Clock())
    assert e.value.kind == updates.INSTALL and target.is_dir()
    assert sorted(os.listdir(tmp_path)) == [target.name]


# -- 10.6: a deadline for the download, a temporary file of its own ------------
class Pieces:
    """An answer handing out pieces, one per read1 (never read); on_read is
    called before each, e.g. to move a clock or look at the folder."""

    def __init__(self, pieces, length=None, on_read=None):
        self.pieces, self.asked, self.on_read = list(pieces), [], on_read
        self.headers = {} if length is None else {"Content-Length": str(length)}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        raise AssertionError("read() blocks until n bytes came: read1() only")

    def read1(self, n):
        self.asked.append(n)
        if self.on_read:
            self.on_read(self)
        return self.pieces.pop(0) if self.pieces else b""


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def answering(monkeypatch, answer):
    monkeypatch.setattr(updates.fetch, "open_https", lambda req, timeout: answer)


def old_image(tmp_path):
    image = tmp_path / "Understeer.AppImage"
    image.write_bytes(OLD)
    return image


def test_the_download_deadline():
    assert updates.DOWNLOAD_DEADLINE == 600


def test_a_trickling_download_stops_at_the_deadline(github, tmp_path, monkeypatch):
    """A byte now and then, each well within the socket timeout."""
    monkeypatch.setattr(updates, "DOWNLOAD_DEADLINE", 0.5)
    body = b"x" * 40

    def trickle(h):
        h.send_response(200)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        try:
            for i in range(len(body)):
                h.wfile.write(body[i:i + 1])
                h.wfile.flush()
                time.sleep(0.1)
        except OSError:
            pass
    github.answer = trickle
    start = time.monotonic()
    with pytest.raises(updates.Error) as e:
        download_from(tmp_path, body)
    assert e.value.kind == updates.TIMEOUT
    assert time.monotonic() - start < 3        # not the 4 s the whole takes
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_past_the_deadline_before_reading(tmp_path, monkeypatch):
    clock = Clock()
    answer = Pieces([NEW_APPIMAGE], on_read=None)

    def open_late(req, timeout):
        clock.now = updates.DOWNLOAD_DEADLINE + 1    # connecting took that long
        return answer
    monkeypatch.setattr(updates.fetch, "open_https", open_late)
    image = old_image(tmp_path)
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(image), clock=clock)
    assert e.value.kind == updates.TIMEOUT and answer.asked == []
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_past_the_deadline_at_the_end(tmp_path, monkeypatch):
    """Also the read that finds the end is checked: no late success."""
    clock = Clock()

    def late_at_the_end(answer):
        if not answer.pieces:
            clock.now = updates.DOWNLOAD_DEADLINE + 1
    answering(monkeypatch, Pieces([NEW_APPIMAGE], on_read=late_at_the_end))
    image = old_image(tmp_path)
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(image), clock=clock)
    assert e.value.kind == updates.TIMEOUT
    assert image.read_bytes() == OLD
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_the_deadline_reached_exactly_is_a_timeout(tmp_path, monkeypatch):
    clock = Clock()

    def reach(answer):
        clock.now = updates.DOWNLOAD_DEADLINE           # not past, but reached
    answering(monkeypatch, Pieces([NEW_APPIMAGE], on_read=reach))
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(old_image(tmp_path)), clock=clock)
    assert e.value.kind == updates.TIMEOUT


def test_a_download_reads_with_read1_in_pieces(tmp_path, monkeypatch):
    answer = Pieces([NEW_APPIMAGE[:5000], NEW_APPIMAGE[5000:]], len(NEW_APPIMAGE))
    answering(monkeypatch, answer)
    image = old_image(tmp_path)
    new = updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock())
    assert pathlib.Path(new.path).read_bytes() == NEW_APPIMAGE
    # never more than one byte past the size is asked
    assert answer.asked == [len(NEW_APPIMAGE) + 1, len(NEW_APPIMAGE) - 5000 + 1, 1]


def test_an_announced_size_too_big_is_refused_before_reading(tmp_path, monkeypatch):
    answer = Pieces([NEW_APPIMAGE + b"x"], len(NEW_APPIMAGE) + 1)
    answering(monkeypatch, answer)
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(old_image(tmp_path)), clock=Clock())
    assert e.value.kind == updates.SIZE and answer.asked == []
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_called_off_between_two_reads(tmp_path, monkeypatch):
    answer = Pieces([NEW_APPIMAGE[:10], NEW_APPIMAGE[10:]])
    answering(monkeypatch, answer)
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(old_image(tmp_path)),
                         cancelled=lambda: bool(answer.asked), clock=Clock())
    assert e.value.kind == updates.CANCELLED and len(answer.asked) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


OLD_PART = ".Understeer-update.AppImage.part"     # the name older versions used


def test_the_temporary_file_is_new_and_private(tmp_path, monkeypatch):
    seen = []

    def look(answer):
        parts = [p for p in tmp_path.iterdir() if p.name.endswith(".part")]
        seen.extend((p.name, p.stat().st_mode & 0o777) for p in parts)
    answering(monkeypatch, Pieces([NEW_APPIMAGE], on_read=look))
    new = updates.download(asset_for(NEW_APPIMAGE), str(old_image(tmp_path)), clock=Clock())
    name = pathlib.Path(new.path).name
    assert name.startswith(".Understeer-update-") and name.endswith(".AppImage.part")
    assert name != OLD_PART and pathlib.Path(new.path).parent == tmp_path
    assert seen and all(mode == 0o600 for _n, mode in seen)     # while downloading
    assert pathlib.Path(new.path).stat().st_mode & 0o777 == 0o755   # once checked
    st = os.lstat(new.path)
    assert (new.dev, new.ino) == (st.st_dev, st.st_ino)


def test_two_downloads_get_two_names(tmp_path, monkeypatch):
    names = set()
    for _i in range(2):
        answering(monkeypatch, Pieces([NEW_APPIMAGE]))
        names.add(updates.download(asset_for(NEW_APPIMAGE), str(old_image(tmp_path)),
                                   clock=Clock()).path)
    assert len(names) == 2


def test_a_link_under_the_old_name_is_not_followed(tmp_path, monkeypatch):
    victim = tmp_path / "victim"
    victim.write_bytes(b"precious")
    victim.chmod(0o600)
    (tmp_path / OLD_PART).symlink_to(victim)
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    image = old_image(tmp_path)
    new = updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock())
    updates.install(new, str(image))
    assert victim.read_bytes() == b"precious" and victim.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / OLD_PART).is_symlink()           # left alone
    assert image.read_bytes() == NEW_APPIMAGE


def test_an_old_part_file_is_left_alone(tmp_path, monkeypatch):
    (tmp_path / OLD_PART).write_bytes(b"from an older version")
    answering(monkeypatch, Pieces([b"broken"]))           # wrong size: fails
    with pytest.raises(updates.Error):
        updates.download(asset_for(NEW_APPIMAGE), str(old_image(tmp_path)), clock=Clock())
    assert (tmp_path / OLD_PART).read_bytes() == b"from an older version"


def test_flushed_made_runnable_and_synced_through_the_descriptor(tmp_path, monkeypatch):
    calls = []
    real_fchmod, real_fsync = os.fchmod, os.fsync
    monkeypatch.setattr(updates.os, "fchmod",
                        lambda fd, mode: calls.append(("fchmod", mode)) or real_fchmod(fd, mode))
    monkeypatch.setattr(updates.os, "fsync",
                        lambda fd: calls.append(("fsync",)) or real_fsync(fd))
    monkeypatch.setattr(updates.os, "chmod",
                        lambda *a, **k: calls.append(("chmod by name",)))
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    image = old_image(tmp_path)
    updates.install(updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock()),
                    str(image))
    assert calls == [("fchmod", 0o755), ("fsync",)]
    assert image.read_bytes() == NEW_APPIMAGE


def test_a_failed_sync_installs_nothing(tmp_path, monkeypatch):
    def broken(fd):
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(updates.os, "fsync", broken)
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    image = old_image(tmp_path)
    with pytest.raises(updates.Error):
        updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock())
    assert image.read_bytes() == OLD
    assert [p.name for p in tmp_path.iterdir()] == ["Understeer.AppImage"]


def test_a_replaced_download_is_not_installed(tmp_path, monkeypatch):
    """Swapped between the check and the installation: refused, and the
    other file is neither installed, changed nor removed."""
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    image = old_image(tmp_path)
    new = updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock())
    other = tmp_path / "other"
    other.write_bytes(EVIL)
    os.replace(other, new.path)
    with pytest.raises(updates.Error) as e:
        updates.install(new, str(image))
    assert e.value.kind == updates.INSTALL
    assert image.read_bytes() == OLD
    assert pathlib.Path(new.path).read_bytes() == EVIL      # not ours: left alone


def test_a_replaced_download_is_not_removed_when_called_off(updating, tmp_path, monkeypatch):
    import tempfile

    def download(asset, appimage, **_kw):
        fd, path = tempfile.mkstemp(dir=tmp_path, suffix=".part")
        st = os.fstat(fd)
        os.close(fd)
        other = tmp_path / "other"
        other.write_bytes(EVIL)
        os.replace(other, path)                        # swapped meanwhile
        return updates.Downloaded(path, st.st_dev, st.st_ino, updates._target(appimage))
    monkeypatch.setattr(updates, "download", download)
    asks = []
    with pytest.raises(updates.Error) as e:
        updates.update_to("9.0.0", "2.3.3", str(updating["image"]),
                          cancelled=lambda: asks.append(1) or len(asks) > 1)
    assert e.value.kind == updates.CANCELLED
    left = [p for p in tmp_path.iterdir() if p.name.endswith(".part")]
    assert len(left) == 1 and left[0].read_bytes() == EVIL


@pytest.mark.parametrize("mode", [0o775, 0o757, 0o777])
def test_no_update_of_itself_in_a_folder_others_can_write(tmp_path, monkeypatch, mode):
    folder = tmp_path / "apps"
    folder.mkdir()
    image = old_image(folder)
    folder.chmod(mode)
    try:
        assert not updates.can_install(str(image))
        answering(monkeypatch, Pieces([NEW_APPIMAGE]))
        with pytest.raises(updates.Error) as e:
            updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock())
        assert e.value.kind == updates.INSTALL
        assert image.read_bytes() == OLD and sorted(os.listdir(folder)) == [image.name]
    finally:
        folder.chmod(0o700)


def test_no_update_of_itself_below_a_folder_others_can_write(tmp_path):
    shared = tmp_path / "shared"
    folder = shared / "apps"
    folder.mkdir(parents=True)
    image = old_image(folder)
    shared.chmod(0o777)                  # others could move "apps" away
    try:
        assert not updates.can_install(str(image))
    finally:
        shared.chmod(0o700)


def test_a_sticky_folder_above_is_fine(tmp_path):
    shared = tmp_path / "shared"
    folder = shared / "apps"
    folder.mkdir(parents=True)
    image = old_image(folder)
    shared.chmod(0o1777)                 # like /tmp: others can't move "apps"
    try:
        assert updates.can_install(str(image))
    finally:
        shared.chmod(0o700)


def test_no_update_of_itself_in_a_folder_of_someone_else(tmp_path, monkeypatch):
    image = old_image(tmp_path)
    uid = os.getuid()
    monkeypatch.setattr(updates.os, "getuid", lambda: uid + 12345)
    assert not updates.can_install(str(image))


def test_an_appimage_reached_through_links(tmp_path, monkeypatch):
    """A linked folder or a link to the AppImage: the real file is updated
    in its real folder, the links stay as they are."""
    real = tmp_path / "real"
    real.mkdir()
    image = old_image(real)
    (tmp_path / "linked").symlink_to(real)
    (tmp_path / "Understeer").symlink_to(image)
    for path in (tmp_path / "linked" / image.name, tmp_path / "Understeer"):
        image.write_bytes(OLD)
        assert updates.can_install(str(path))
        answering(monkeypatch, Pieces([NEW_APPIMAGE]))
        new = updates.download(asset_for(NEW_APPIMAGE), str(path), clock=Clock())
        assert pathlib.Path(new.path).parent == real
        updates.install(new, str(path))
        assert image.read_bytes() == NEW_APPIMAGE
    assert (tmp_path / "Understeer").is_symlink() and (tmp_path / "linked").is_symlink()


def test_no_update_of_something_else_than_a_file(tmp_path):
    folder = tmp_path / "Understeer.AppImage"
    folder.mkdir()
    assert not updates.can_install(str(folder))
    assert not updates.can_install(str(tmp_path / "missing.AppImage"))


# -- 10.6 C.2: the target is bound, the temporary file checked again ---------
def downloaded_into(tmp_path, monkeypatch, image, on_read=None):
    answering(monkeypatch, Pieces([NEW_APPIMAGE], on_read=on_read))
    return updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock())


@pytest.fixture
def linked_image(tmp_path):
    """home/Understeer.AppImage, reached through links/Understeer.AppImage;
    home/notes.txt is a file of the user's next to it."""
    home, links = tmp_path / "home", tmp_path / "links"
    for folder in (home, links):
        folder.mkdir()
    real = home / "Understeer.AppImage"
    real.write_bytes(OLD)
    notes = home / "notes.txt"
    notes.write_bytes(b"precious notes")
    link = links / "Understeer.AppImage"
    link.symlink_to(real)
    return {"real": real, "notes": notes, "link": link, "home": home}


def test_a_link_bent_during_the_download_installs_nothing(linked_image, tmp_path,
                                                          monkeypatch):
    """R1: the target checked before the download is the one replaced, or
    none; never what the link points to afterwards."""
    link, real, notes = linked_image["link"], linked_image["real"], linked_image["notes"]
    new = downloaded_into(tmp_path, monkeypatch, link)
    link.unlink()
    link.symlink_to(notes)                          # bent meanwhile
    with pytest.raises(updates.Error) as e:
        updates.install(new, str(link))
    assert e.value.kind == updates.INSTALL
    assert notes.read_bytes() == b"precious notes" and real.read_bytes() == OLD
    assert not pathlib.Path(new.path).exists()          # our own file: removed


def test_a_link_to_something_else_than_an_appimage(linked_image, tmp_path, monkeypatch):
    link, notes = linked_image["link"], linked_image["notes"]
    link.unlink()
    link.symlink_to(notes)
    assert not updates.can_install(str(link))
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(link), clock=Clock())
    assert e.value.kind == updates.INSTALL
    assert notes.read_bytes() == b"precious notes"
    assert sorted(p.name for p in linked_image["home"].iterdir()) == [
        "Understeer.AppImage", "notes.txt"]


@pytest.mark.parametrize("content", [
    b"", b"\x7fELF", APPIMAGE_HEAD[:10], b"\x7fELF\x02\x01\x01\x00AI\x01old",
    b"#!/bin/sh\nexec true\n", b"MZ" + b"\x00" * 20,
], ids=["empty", "elf-only", "cut-short", "type-1", "script", "other"])
def test_only_an_appimage_is_replaced(tmp_path, monkeypatch, content):
    image = tmp_path / "Understeer.AppImage"
    image.write_bytes(content)
    assert not updates.can_install(str(image))
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock())
    assert e.value.kind == updates.INSTALL and image.read_bytes() == content


def test_a_folder_put_in_place_of_the_folder(tmp_path, monkeypatch):
    folder = tmp_path / "apps"
    folder.mkdir()
    image = old_image(folder)
    new = downloaded_into(tmp_path, monkeypatch, image)
    folder.rename(tmp_path / "moved")
    folder.mkdir()
    (folder / image.name).write_bytes(OLD)          # looks the same
    os.link(tmp_path / "moved" / pathlib.Path(new.path).name,
            folder / pathlib.Path(new.path).name)  # so does the download
    with pytest.raises(updates.Error) as e:
        updates.install(new, str(image))
    assert e.value.kind == updates.INSTALL
    assert (folder / image.name).read_bytes() == OLD
    assert (tmp_path / "moved" / image.name).read_bytes() == OLD


def test_the_appimage_put_in_place_of_meanwhile(tmp_path, monkeypatch):
    image = old_image(tmp_path)
    new = downloaded_into(tmp_path, monkeypatch, image)
    other = tmp_path / "other"
    other.write_bytes(APPIMAGE_HEAD + b"another one")
    os.replace(other, image)                        # another file, as AppImage too
    with pytest.raises(updates.Error) as e:
        updates.install(new, str(image))
    assert e.value.kind == updates.INSTALL
    assert image.read_bytes() == APPIMAGE_HEAD + b"another one"


def test_an_unsafe_folder_when_installing(tmp_path, monkeypatch):
    folder = tmp_path / "apps"
    folder.mkdir()
    image = old_image(folder)
    new = downloaded_into(tmp_path, monkeypatch, image)
    folder.chmod(0o777)
    try:
        with pytest.raises(updates.Error) as e:
            updates.install(new, str(image))
    finally:
        folder.chmod(0o700)
    assert e.value.kind == updates.INSTALL and image.read_bytes() == OLD
    assert sorted(os.listdir(folder)) == [image.name]        # our file gone


def test_an_extra_hardlink_to_the_download_installs_nothing(tmp_path, monkeypatch):
    """R3: another name for the file would still reach the installed
    AppImage; the extra name itself is left alone."""
    image = old_image(tmp_path)
    extra = tmp_path / "extra"

    def link_it(answer):
        if not extra.exists():
            part = next(p for p in tmp_path.iterdir() if p.name.endswith(".part"))
            os.link(part, extra)
    new = downloaded_into(tmp_path, monkeypatch, image, on_read=link_it)
    with pytest.raises(updates.Error) as e:
        updates.install(new, str(image))
    assert e.value.kind == updates.INSTALL and image.read_bytes() == OLD
    assert extra.read_bytes() == NEW_APPIMAGE


def test_an_old_part_file_as_hardlink_is_left_alone(tmp_path, monkeypatch):
    victim = tmp_path / "victim"
    victim.write_bytes(b"precious")
    os.link(victim, tmp_path / OLD_PART)
    image = old_image(tmp_path)
    updates.install(downloaded_into(tmp_path, monkeypatch, image), str(image))
    assert victim.read_bytes() == b"precious"
    assert (tmp_path / OLD_PART).read_bytes() == b"precious"
    assert image.read_bytes() == NEW_APPIMAGE


def open_fds():
    return set(os.listdir("/proc/self/fd"))


@pytest.mark.parametrize("broken", ["fstat", "fdopen"])
def test_the_temporary_file_cant_be_set_up(tmp_path, monkeypatch, caplog, broken):
    """R2: an updates.Error, the descriptor closed, and a file whose identity
    isn't known left behind (and said so) rather than removed by name."""
    image = old_image(tmp_path)
    real, real_mkstemp = getattr(os, broken), updates.tempfile.mkstemp

    def fail(*a, **k):
        raise OSError(5, "Input/output error")

    def mkstemp(**kwargs):              # breaks only once the file is made
        made = real_mkstemp(**kwargs)
        monkeypatch.setattr(updates.os, broken, fail)
        return made
    monkeypatch.setattr(updates.tempfile, "mkstemp", mkstemp)
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    before = open_fds()
    with caplog.at_level("WARNING", logger="understeer.updates"), \
            pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock())
    monkeypatch.setattr(updates.os, broken, real)
    assert e.value.kind == updates.INSTALL
    assert open_fds() == before
    parts = [p for p in tmp_path.iterdir() if p.name.endswith(".part")]
    if broken == "fstat":
        assert len(parts) == 1 and any("Left" in m for m in caplog.messages)
    else:
        assert parts == []                  # known to be ours: removed
    assert image.read_bytes() == OLD


def test_the_temporary_file_cant_be_made(tmp_path, monkeypatch):
    image = old_image(tmp_path)

    def fail(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(updates.tempfile, "mkstemp", fail)
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock())
    assert e.value.kind == updates.INSTALL
    assert sorted(os.listdir(tmp_path)) == [image.name] and image.read_bytes() == OLD


class Failing:
    """A file object that fails at one step."""

    def __init__(self, real, step):
        self.real, self.step = real, step

    def __getattr__(self, name):
        return getattr(self.real, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _maybe(self, name):
        if self.step == name:
            raise OSError(28, "No space left on device")

    def write(self, data):
        self._maybe("write")
        return self.real.write(data)

    def flush(self):
        self._maybe("flush")
        return self.real.flush()

    def close(self):
        self.real.close()
        self._maybe("close")


@pytest.mark.parametrize("step", ["write", "flush", "close", "fchmod", "fsync"])
def test_a_failing_step_installs_nothing_and_leaks_nothing(tmp_path, monkeypatch, step):
    image = old_image(tmp_path)
    real_fdopen = os.fdopen
    if step in ("write", "flush", "close"):
        monkeypatch.setattr(updates.os, "fdopen",
                            lambda fd, mode: Failing(real_fdopen(fd, mode), step))
    else:
        def fail(*a, **k):
            raise OSError(5, "Input/output error")
        monkeypatch.setattr(updates.os, step, fail)
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    before = open_fds()
    with pytest.raises(updates.Error):
        updates.download(asset_for(NEW_APPIMAGE), str(image), clock=Clock())
    assert open_fds() == before
    assert sorted(os.listdir(tmp_path)) == [image.name] and image.read_bytes() == OLD


def test_no_descriptor_is_left_open(tmp_path, monkeypatch):
    image = old_image(tmp_path)
    before = open_fds()
    updates.install(downloaded_into(tmp_path, monkeypatch, image), str(image))
    assert open_fds() == before and image.read_bytes() == NEW_APPIMAGE


def test_a_deadline_reached_exactly_starts_no_read(tmp_path, monkeypatch):
    """R4: reached is as good as past: no further read1()."""
    clock = Clock()
    answer = Pieces([NEW_APPIMAGE])

    def open_at_the_deadline(req, timeout):
        clock.now = updates.DOWNLOAD_DEADLINE
        return answer
    monkeypatch.setattr(updates.fetch, "open_https", open_at_the_deadline)
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(old_image(tmp_path)), clock=clock)
    assert e.value.kind == updates.TIMEOUT and answer.asked == []


def test_a_deadline_reached_exactly_at_the_end(tmp_path, monkeypatch):
    clock = Clock()

    def at_the_end(answer):
        if not answer.pieces:
            clock.now = updates.DOWNLOAD_DEADLINE
    answering(monkeypatch, Pieces([NEW_APPIMAGE], on_read=at_the_end))
    image = old_image(tmp_path)
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(image), clock=clock)
    assert e.value.kind == updates.TIMEOUT and image.read_bytes() == OLD


# -- 10.6 R5: the deadline is looked at once more right before replacing ------
def no_part_left(folder):
    return [p.name for p in folder.iterdir() if p.name.endswith(".part")] == []


def test_the_deadline_reached_while_syncing(tmp_path, monkeypatch):
    clock, real_fsync = Clock(), os.fsync

    def slow_fsync(fd):
        real_fsync(fd)
        clock.now = updates.DOWNLOAD_DEADLINE           # it took that long
    monkeypatch.setattr(updates.os, "fsync", slow_fsync)
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    image = old_image(tmp_path)
    before = open_fds()
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(image), clock=clock)
    assert e.value.kind == updates.TIMEOUT
    assert image.read_bytes() == OLD and no_part_left(tmp_path)
    assert open_fds() == before


def test_the_deadline_reached_while_closing(tmp_path, monkeypatch):
    clock, real_fdopen = Clock(), os.fdopen

    class SlowClose(Failing):
        def close(self):
            self.real.close()
            clock.now = updates.DOWNLOAD_DEADLINE
    monkeypatch.setattr(updates.os, "fdopen",
                        lambda fd, mode: SlowClose(real_fdopen(fd, mode), None))
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    image = old_image(tmp_path)
    before = open_fds()
    with pytest.raises(updates.Error) as e:
        updates.download(asset_for(NEW_APPIMAGE), str(image), clock=clock)
    assert e.value.kind == updates.TIMEOUT
    assert image.read_bytes() == OLD and no_part_left(tmp_path)
    assert open_fds() == before


def test_the_deadline_reached_while_getting_ready_to_install(tmp_path, monkeypatch):
    """The last look is right before os.replace, after the checks of the
    target and of the file, which take time too."""
    clock = Clock()
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    image = old_image(tmp_path)
    other = tmp_path / "other file"
    other.write_bytes(b"not ours")
    new = updates.download(asset_for(NEW_APPIMAGE), str(image), clock=clock)
    real_still = updates._still

    def slow_still(target):
        real_still(target)
        clock.now = updates.DOWNLOAD_DEADLINE
    monkeypatch.setattr(updates, "_still", slow_still)
    before = open_fds()
    with pytest.raises(updates.Error) as e:
        updates.install(new, str(image))
    assert e.value.kind == updates.TIMEOUT
    assert image.read_bytes() == OLD and no_part_left(tmp_path)
    assert other.read_bytes() == b"not ours" and open_fds() == before


def test_an_installation_done_is_no_timeout(tmp_path, monkeypatch):
    """Once replaced, it is installed: a deadline passing afterwards
    doesn't make it a failed update."""
    clock, real_replace = Clock(), os.replace

    def replace_then_late(src, dst):
        real_replace(src, dst)
        clock.now = updates.DOWNLOAD_DEADLINE + 1
    answering(monkeypatch, Pieces([NEW_APPIMAGE]))
    image = old_image(tmp_path)
    new = updates.download(asset_for(NEW_APPIMAGE), str(image), clock=clock)
    monkeypatch.setattr(updates.os, "replace", replace_then_late)
    updates.install(new, str(image))
    assert image.read_bytes() == NEW_APPIMAGE
