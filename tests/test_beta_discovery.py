"""Finding the newest beta on GitHub: only checked metadata of our own
prereleases, never a download. Every answer here is made up; nothing
asks the internet."""
import http.client
import json
import socket
import ssl
import urllib.error
import urllib.request

import pytest

from understeer import fetch, updates

PAGE = updates.RELEASES_URL + "/tag/"


def rel(tag, prerelease=None, draft=False, **more):
    """A release as GitHub lists it; a beta tag is a prerelease."""
    if prerelease is None:
        prerelease = "-beta." in tag
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft,
            "html_url": PAGE + tag, "body": "not read", **more}


def beta(version):
    return {"version": version, "url": PAGE + "v" + version}


# -- choosing the beta --------------------------------------------------------

def test_newest_beta():
    data = [rel("v2.4.0-beta.1"), rel("v2.3.3"), rel("v2.3.2")]
    assert updates.newest_beta(data) == beta("2.4.0-beta.1")
    assert updates.newest_beta(data, "2.3.3") == beta("2.4.0-beta.1")


def test_only_version_and_page_are_given():
    found = updates.newest_beta([rel("v2.4.0-beta.1", assets=[{
        "name": "Understeer-2.4.0-beta.1-x86_64.AppImage",
        "browser_download_url": updates.DOWNLOAD_URL + "x", "digest":
        "sha256:" + "a" * 64}])])
    assert set(found) == {"version", "url"}           # no asset: no download


def test_ordered_by_version_not_by_date():
    data = [rel("v2.4.0-beta.9"), rel("v2.4.0-beta.10"), rel("v2.4.0-beta.2")]
    assert updates.newest_beta(data) == beta("2.4.0-beta.10")
    # an older line's beta published later doesn't hide the newer one
    data = [rel("v2.3.4-beta.1"), rel("v2.4.0-beta.1")]
    assert updates.newest_beta(data) == beta("2.4.0-beta.1")


@pytest.mark.parametrize("data", [
    [rel("v2.4.0"), rel("v2.4.0-beta.2"), rel("v2.4.0-beta.1")],
    [rel("v2.4.1"), rel("v2.4.0-beta.2")],
    [rel("v2.4.0-beta.2"), rel("v2.4.0")],          # order doesn't matter
])
def test_a_stable_release_ends_its_betas(data):
    assert updates.newest_beta(data) is None


def test_an_older_stable_doesnt_end_a_newer_beta():
    data = [rel("v2.3.4"), rel("v2.4.0-beta.1")]
    assert updates.newest_beta(data) == beta("2.4.0-beta.1")


@pytest.mark.parametrize("current, expected", [
    ("2.3.3", "2.4.0-beta.2"),
    ("2.4.0-beta.1", "2.4.0-beta.2"),       # a beta user hears of the next
    ("v2.4.0-beta.1", "2.4.0-beta.2"),
    ("2.4.0-beta.2", None),                 # running it already
    ("2.4.0-beta.10", None),                # running a newer one
    ("2.4.0", None),                        # stable is newer than its betas
    ("2.5.0", None),
    ("2.3.3-3-gabc", None),                 # from git: can't be told
    ("garbage", None),
])
def test_only_newer_than_the_running_version(current, expected):
    data = [rel("v2.4.0-beta.2"), rel("v2.4.0-beta.1"), rel("v2.3.3")]
    found = updates.newest_beta(data, current)
    assert found == (beta(expected) if expected else None)


def test_stable_releases_alone_give_none():
    assert updates.newest_beta([rel("v2.3.3"), rel("v2.3.2")]) is None
    assert updates.newest_beta([]) is None


# -- what is refused ---------------------------------------------------------

@pytest.mark.parametrize("item", [
    rel("v2.4.0-beta.1", draft=True),                    # draft
    rel("v2.4.0-beta.1", draft=None),
    rel("v2.4.0-beta.1", draft="false"),
    {k: v for k, v in rel("v2.4.0-beta.1").items() if k != "draft"},
    rel("v2.4.0-beta.1", prerelease=False),              # beta marked stable
    rel("v2.4.0-beta.1", prerelease="true"),
    rel("v2.4.0-beta.1", prerelease=1),
    {k: v for k, v in rel("v2.4.0-beta.1").items() if k != "prerelease"},
    rel("v2.4.0", prerelease=True),                       # stable marked beta
    rel("v2.4.0-rc1", prerelease=True),                   # other test versions
    rel("v2.4.0-rc.1", prerelease=True),
    rel("v2.4.0-alpha.1", prerelease=True),
    rel("v2.4.0-beta", prerelease=True),
    rel("v2.4.0-beta1", prerelease=True),
    rel("v2.4.0-Beta.1", prerelease=True),
    rel("2.4.0-beta.1", prerelease=True),                 # malformed tags
    rel("v2.4-beta.1", prerelease=True),
    rel("v2.4.0-beta.1 ", prerelease=True),
    rel("v2.4.0-beta.1\n", prerelease=True),
    rel("v2.4.0-beta.٢", prerelease=True),               # Unicode digits
    rel("v２.4.0-beta.1", prerelease=True),
    rel("v2.4.0-beta.12345", prerelease=True),            # too long
    rel("v99999.0.0-beta.1", prerelease=True),
    rel("v2.4.0-beta." + "9" * 5000, prerelease=True),
    {**rel("v2.4.0-beta.1"), "tag_name": 5},
    {**rel("v2.4.0-beta.1"), "tag_name": None},
    None, "v2.4.0-beta.1", 5, [rel("v2.4.0-beta.1")],      # no release object
])
def test_refused(item):
    assert updates.newest_beta([item]) is None


@pytest.mark.parametrize("url", [
    None, 5, "",
    "http://github.com/JamDaBam/Understeer/releases/tag/v2.4.0-beta.1",
    "https://github.com/someone/Understeer/releases/tag/v2.4.0-beta.1",
    "https://github.com/JamDaBam/Understeer/releases/tag/v2.4.0-beta.2",
    "https://github.com/JamDaBam/Understeer/releases/tag/v2.4.0-beta.1/",
    "https://github.com/JamDaBam/Understeer/releases/tag/v2.4.0-beta.1?x",
    "https://github.com/JamDaBam/Understeer/releases/download/v2.4.0-beta.1/x",
    "https://github.com.evil.example/JamDaBam/Understeer/releases/tag/v2.4.0-beta.1",
    "https://evil.example/?https://github.com/JamDaBam/Understeer/releases/tag/v2.4.0-beta.1",
    "javascript:alert(1)",
])
def test_refused_without_its_own_page(url):
    assert updates.newest_beta([rel("v2.4.0-beta.1", html_url=url)]) is None


def test_a_refused_stable_release_ends_nothing():
    """Only checked stable releases end betas: a broken one is ignored."""
    data = [rel("v2.4.0", html_url="https://evil.example/"), rel("v2.4.0-beta.1")]
    assert updates.newest_beta(data) == beta("2.4.0-beta.1")


def test_only_one_page_is_read():
    data = [rel(f"v2.3.{n}") for n in range(updates.BETAS_PER_PAGE, 0, -1)]
    assert updates.newest_beta(data + [rel("v2.9.0-beta.1")]) is None
    assert updates.newest_beta(data[:-1] + [rel("v2.9.0-beta.1")]) == \
        beta("2.9.0-beta.1")


@pytest.mark.parametrize("data", [None, {}, {"message": "Not Found"}, "x", 5])
def test_no_list(data):
    with pytest.raises(updates.Error):
        updates.newest_beta(data)


# -- asking GitHub -----------------------------------------------------------

class Answer:
    """An HTTP answer of urllib: 200 OK from the address asked."""
    def __init__(self, body, status=200, url=None):
        self.body, self.pos, self.status = body, 0, status
        self.url = url or updates.BETAS_URL
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return self.url

    def read1(self, size):
        chunk = self.body[self.pos:self.pos + size]
        self.pos += len(chunk)
        return chunk


class Opener:
    """Stands in for urllib's opener; remembers what was asked."""
    def __init__(self, result):
        self.result, self.asked = result, []

    def open(self, req, timeout):
        self.asked.append((req, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        if isinstance(self.result, Answer):
            return self.result
        return Answer(self.result)


@pytest.fixture
def github(monkeypatch):
    def answer(result):
        opener = Opener(result)
        monkeypatch.setattr(fetch, "_opener", lambda: opener)
        return opener
    return answer


def no_download(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("must not download or install")
    for name in ("download", "install", "restart"):
        monkeypatch.setattr(updates, name, refuse)
    monkeypatch.setattr(updates.urllib.request, "urlopen", refuse)


def test_fetch_beta(github, monkeypatch):
    no_download(monkeypatch)
    opener = github(json.dumps([rel("v2.4.0-beta.1"), rel("v2.3.3")]).encode())
    assert updates.fetch_beta("2.3.3") == beta("2.4.0-beta.1")
    (req, timeout), = opener.asked                      # one request
    assert req.full_url == updates.BETAS_URL == (
        "https://api.github.com/repos/JamDaBam/Understeer/releases?per_page=10")
    assert req.get_method() == "GET" and req.data is None
    assert req.get_header("User-agent") == "Understeer"    # nothing about the user
    assert not req.has_header("Authorization")
    assert timeout == 10


def test_fetch_beta_none(github):
    github(json.dumps([rel("v2.3.3")]).encode())
    assert updates.fetch_beta("2.3.3") is None


@pytest.mark.parametrize("error", [
    urllib.error.URLError("no network"),
    socket.timeout("timed out"), TimeoutError("timed out"),
    ConnectionResetError("reset"), OSError("broken"),
    urllib.error.HTTPError(updates.BETAS_URL, 403, "rate limit", {}, None),
    urllib.error.HTTPError(updates.BETAS_URL, 301, "moved", {}, None),
    http.client.IncompleteRead(b"[", 100), http.client.BadStatusLine("x"),
    http.client.RemoteDisconnected("closed"), ValueError("bad"),
])
def test_fetch_beta_offline_or_refused(github, error):
    github(error)
    with pytest.raises(updates.Error):
        updates.fetch_beta("2.3.3")


@pytest.mark.parametrize("body", [
    b"", b"not json", b"[", b"\xff\xfe\x00garbage", b"<html>rate limit</html>",
    b"[" + b"9" * 5000 + b"]",                           # number too long
    b'{"message": "Not Found"}', b"null", b'"x"',
])
def test_fetch_beta_broken_answers(github, body):
    github(body)
    with pytest.raises(updates.Error):
        updates.fetch_beta("2.3.3")


NESTED = b"[" * 100000 + b"]" * 100000       # within the size limit


def test_deeply_nested_json_gives_no_beta(github):
    """Whether json.loads gives up on this depth (RecursionError, turned
    into updates.Error) or reads it as lists in lists differs between
    Python versions; either way there is no beta and no other exception."""
    github(NESTED)
    try:
        found = updates.fetch_beta("2.3.3")
    except updates.Error:
        return
    assert found is None


def test_json_nested_too_deeply_is_an_error(github, monkeypatch):
    """Where json.loads gives up with RecursionError, fetch_beta raises
    updates.Error, on any Python version."""
    def too_deep(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")
    github(b"[]")
    monkeypatch.setattr(updates.json, "loads", too_deep)
    with pytest.raises(updates.Error, match="no JSON"):
        updates.fetch_beta("2.3.3")


def test_fetch_beta_size_limit(github):
    item = json.dumps(rel("v2.4.0-beta.1"))
    small = ("[" + item + " " * 100 + "]").encode()
    github(small)
    assert updates.fetch_beta() == beta("2.4.0-beta.1")
    padding = " " * (updates.MAX_BETAS_ANSWER - len(item) - 2)
    exact = ("[" + item + padding + "]").encode()
    assert len(exact) == updates.MAX_BETAS_ANSWER
    github(exact)
    assert updates.fetch_beta() == beta("2.4.0-beta.1")
    github(exact + b" ")                                # one byte too many
    with pytest.raises(updates.Error, match="more than 1048576 bytes"):
        updates.fetch_beta()


def test_unicode_in_an_answer(github):
    data = [rel("v2.4.0-beta.1", name="Test ünïcödé ✓ ‮"),
            {**rel("v2.4.0-beta.2"), "tag_name": "v2.4.0-beta.٢"}]
    github(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    assert updates.fetch_beta() == beta("2.4.0-beta.1")


@pytest.mark.parametrize("answer", [
    Answer(b"[]", status=203), Answer(b"[]", status=206),
    Answer(b"", status=204),
    Answer(b"[]", url="https://api.github.com/repos/someone/else/releases"),
    Answer(b"[]", url=updates.BETAS_URL + "&page=2"),
])
def test_only_200_from_the_address_asked(github, answer):
    github(answer)
    with pytest.raises(updates.Error):
        updates.fetch_beta()


def test_no_redirects_and_checked_certificates():
    opener = fetch._opener()
    handlers = {type(h) for h in opener.handlers}
    assert fetch._NoRedirect in handlers
    assert urllib.request.HTTPRedirectHandler not in handlers
    with pytest.raises(fetch.Error) as e:
        fetch._NoRedirect().redirect_request(
            None, None, 301, "moved", {}, "https://evil.example/")
    assert e.value.kind == "redirect"
    https = [h for h in opener.handlers
             if isinstance(h, urllib.request.HTTPSHandler)]
    assert https
    context = https[0]._context      # None, or Python's default context
    if context is not None:
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_only_https_is_asked(monkeypatch):
    def refuse():
        raise AssertionError("asked")
    monkeypatch.setattr(fetch, "_opener", refuse)
    with pytest.raises(updates.Error):
        updates._get_json("http://api.github.com/x", 10, 1)


# -- the stable channel stays apart ------------------------------------------

def test_betas_stay_out_of_the_stable_channel(github):
    github(json.dumps([rel("v2.4.0-beta.1")]).encode())
    found = updates.fetch_beta("2.3.3")
    assert found == beta("2.4.0-beta.1")
    assert not updates.is_newer(found["version"], "2.3.3")
    for item in (rel("v2.4.0-beta.1"), rel("v2.4.0-beta.1", prerelease=False)):
        with pytest.raises(updates.Error):
            updates.parse_latest(item)


def test_the_stable_look_is_unchanged():
    assert updates.LATEST_URL == \
        "https://api.github.com/repos/JamDaBam/Understeer/releases/latest"
    assert updates.CHECK_EVERY == 3600


def test_no_hardware_here():
    """Looking for betas uses no device module."""
    assert not {"device", "inputs", "leds", "display", "capabilities"} \
        & set(vars(updates))
