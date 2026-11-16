"""Getting one file from a fixed address, against a local HTTP server on
127.0.0.1: nothing goes to the internet."""
import http.server
import json
import socket
import threading
import time

import pytest

from understeer import community, fetch, updates

BODY = b'{"ok": true}'


class Server:
    """A local server; answer(handler) answers each request."""

    def __init__(self):
        server = self
        self.requests = []
        self.answered = threading.Event()   # an answer has ended, also broken off
        self.answer = lambda h: h.send_body(BODY)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                server.requests.append((self.path, dict(self.headers)))
                try:
                    server.answer(self)
                finally:
                    server.answered.set()

            def send_body(self, body, status=200, length=True):
                self.send_response(status)
                if length:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class Quiet(http.server.ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                pass                # a client that hung up early, on purpose

        self.httpd = Quiet(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,),
                         daemon=True).start()
        self.address = f"127.0.0.1:{self.httpd.server_port}"
        self.url = f"http://{self.address}/file.json"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = Server()
    yield s
    s.close()


def get(url, limit=1000, timeout=5, deadline=10):
    return fetch.get(url, "application/json", limit, timeout, deadline)


def kind(url, **kwargs):
    with pytest.raises(fetch.Error) as e:
        get(url, **kwargs)
    return e.value.kind


def test_get(server):
    assert get(server.url) == BODY
    (path, headers), = server.requests
    assert path == "/file.json"
    assert headers["User-Agent"] == "Understeer"
    assert headers["Accept"] == "application/json"
    assert not {"Cookie", "Authorization"} & set(headers)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_is_not_followed(server, status):
    def redirect(h):
        h.send_response(status)
        h.send_header("Location", "https://example.org/evil.json")
        h.send_header("Content-Length", "0")
        h.end_headers()
    server.answer = redirect
    assert kind(server.url) == "redirect"
    assert len(server.requests) == 1          # nothing else was asked


def test_redirect_to_the_same_server_is_not_followed(server):
    def redirect(h):
        if h.path == "/file.json":
            h.send_response(302)
            h.send_header("Location", "/other.json")
            h.end_headers()
        else:
            h.send_body(BODY)
    server.answer = redirect
    assert kind(server.url) == "redirect"
    assert [p for p, _h in server.requests] == ["/file.json"]


@pytest.mark.parametrize("status", [201, 203, 204, 206, 404, 403, 500, 503])
def test_only_200_counts(server, status):
    server.answer = lambda h: h.send_body(BODY if status != 204 else b"", status)
    with pytest.raises(fetch.Error) as e:
        get(server.url)
    assert (e.value.kind, e.value.code) == ("http", status)


def test_another_final_address_counts_as_an_error(monkeypatch):
    class Answer:
        status, headers = 200, {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return "https://example.org/other.json"

        def read1(self, size):
            return BODY

    class Opener:
        def open(self, req, timeout):
            return Answer()

    monkeypatch.setattr(fetch, "_opener", Opener)
    with pytest.raises(fetch.Error) as e:
        get("https://example.org/file.json")
    assert e.value.kind == "http"


def test_too_big_as_announced(server):
    server.answer = lambda h: h.send_body(b"x" * 2000)
    assert kind(server.url, limit=1000) == "too_big"


def test_too_big_without_announcing_it(server):
    def unannounced(h):
        h.send_response(200)
        h.end_headers()
        h.wfile.write(b"x" * 200_000)
    server.answer = unannounced
    assert kind(server.url, limit=1000) == "too_big"


def test_exactly_the_limit(server):
    server.answer = lambda h: h.send_body(b"x" * 1000)
    assert get(server.url, limit=1000) == b"x" * 1000


def test_timeout(server):
    server.answer = lambda h: (time.sleep(1.5), h.send_body(BODY))
    assert kind(server.url, timeout=0.3) == "timeout"


TRICKLE = 15            # bytes, one each 0.2 s: 3 s in all
DEADLINE = 0.5


def trickle_body(header=None):
    def answer(h):
        h.send_response(200)
        if header:
            h.send_header(*header)
        h.end_headers()
        for _i in range(TRICKLE):
            h.wfile.write(b" ")
            h.wfile.flush()
            time.sleep(0.2)
    return answer


def chunked_trickle(h):
    h.send_response(200)
    h.send_header("Transfer-Encoding", "chunked")
    h.end_headers()
    for _i in range(TRICKLE):
        h.wfile.write(b"1\r\n \r\n")
        h.wfile.flush()
        time.sleep(0.2)
    h.wfile.write(b"0\r\n\r\n")


@pytest.mark.parametrize("answer", [
    trickle_body(), trickle_body(("Content-Length", str(TRICKLE))),
    chunked_trickle,
], ids=["no length", "with length", "chunked"])
def test_a_trickling_body_stops_at_the_deadline(server, answer):
    """Not after the whole 3 s: each read of the socket comes back."""
    server.answer = answer
    start = time.monotonic()
    assert kind(server.url, timeout=1, deadline=DEADLINE) == "timeout"
    assert time.monotonic() - start < DEADLINE + 0.6
    # the server notices that nobody listens any more and its thread ends
    assert server.answered.wait(5)


def test_trickling_headers_are_not_stopped_by_the_deadline(server):
    """A known limit: the deadline is checked while the body is read, not
    while the headers come; they are bound by the timeout of each read
    only. The request still fails, once they are all there."""
    def slow_headers(h):
        h.wfile.write(b"HTTP/1.1 200 OK\r\n")
        for _i in range(10):
            h.wfile.write(b"X-Slow: 1\r\n")
            h.wfile.flush()
            time.sleep(0.2)
        h.wfile.write(b"Content-Length: 2\r\n\r\n{}")
    server.answer = slow_headers
    start = time.monotonic()
    assert kind(server.url, timeout=1, deadline=DEADLINE) == "timeout"
    assert time.monotonic() - start > 1.5          # all headers were waited for
    assert server.answered.wait(5)


def test_a_slow_but_timely_answer_is_read(server):
    server.answer = trickle_body(("Content-Length", str(TRICKLE)))
    assert get(server.url, timeout=1, deadline=10) == b" " * TRICKLE


def test_chunked_answer(server):
    def chunked(h):
        h.send_response(200)
        h.send_header("Transfer-Encoding", "chunked")
        h.end_headers()
        h.wfile.write(b"5\r\n{\"a\":\r\n2\r\n1}\r\n0\r\n\r\n")
    server.answer = chunked
    assert get(server.url) == b'{"a":1}'


def test_too_big_while_chunked(server):
    def chunked(h):
        h.send_response(200)
        h.send_header("Transfer-Encoding", "chunked")
        h.end_headers()
        for _i in range(50):
            h.wfile.write(b"64\r\n" + b"x" * 100 + b"\r\n")
        h.wfile.write(b"0\r\n\r\n")
    server.answer = chunked
    assert kind(server.url, limit=1000) == "too_big"


def test_an_answer_that_ends_early(server):
    """Fewer bytes than announced: the connection broke, not a file."""
    def short(h):
        h.send_response(200)
        h.send_header("Content-Length", "100")
        h.end_headers()
        h.wfile.write(b'{"a": 1}')
        h.close_connection = True
    server.answer = short
    assert kind(server.url) == "offline"


def test_nobody_answers():
    with socket.socket() as s:                # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert kind(f"http://127.0.0.1:{port}/file.json") == "offline"


def test_tls_fails(server):
    """HTTPS to a server that speaks no TLS: an error, never the answer."""
    assert kind(f"https://{server.address}/file.json") == "offline"


def test_tls_certificate_is_checked():
    import ssl
    import urllib.request
    https = [h for h in fetch._opener().handlers
             if isinstance(h, urllib.request.HTTPSHandler)]
    assert https
    context = https[0]._context      # None, or Python's default context
    if context is not None:
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


# -- both callers use it, each for its own fixed address ----------------------

def test_community_asks_its_catalog(monkeypatch):
    asked = []
    monkeypatch.setattr(fetch, "get", lambda *a: asked.append(a) or b"x")
    assert community.download() == b"x"
    assert asked == [(community.CATALOG_URL, "application/json",
                      community.library.MAX_CATALOG_BYTES, community.TIMEOUT,
                      community.DEADLINE)]


@pytest.mark.parametrize("error_kind", ["offline", "timeout", "http",
                                        "redirect", "too_big"])
def test_community_keeps_its_kinds_of_error(monkeypatch, error_kind):
    def fail(*a):
        raise fetch.Error(error_kind, "x", 404 if error_kind == "http" else None)
    monkeypatch.setattr(fetch, "get", fail)
    with pytest.raises(community.Error) as e:
        community.download()
    assert e.value.kind == error_kind
    assert e.value.code == (404 if error_kind == "http" else None)


def test_beta_discovery_asks_github(monkeypatch):
    asked = []
    monkeypatch.setattr(fetch, "get", lambda *a: asked.append(a) or b"[]")
    assert updates.fetch_beta("2.3.3") is None
    assert asked == [(updates.BETAS_URL, "application/vnd.github+json",
                      updates.MAX_BETAS_ANSWER, 10, updates.BETAS_DEADLINE)]


@pytest.mark.parametrize("error_kind", ["offline", "timeout", "http",
                                        "redirect", "too_big"])
def test_beta_discovery_turns_errors_into_its_own(monkeypatch, error_kind):
    def fail(*a):
        raise fetch.Error(error_kind, "x")
    monkeypatch.setattr(fetch, "get", fail)
    with pytest.raises(updates.Error):
        updates.fetch_beta("2.3.3")


def test_the_stable_look_doesnt_use_it(monkeypatch):
    """The stable update check stays as it was: urlopen of LATEST_URL."""
    def refuse(*a):
        raise AssertionError("fetch.get used")
    monkeypatch.setattr(fetch, "get", refuse)

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, limit):
            return json.dumps({"tag_name": "v9.0.0", "prerelease": False,
                               "draft": False}).encode()

    seen = []
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda req, timeout: seen.append(req.full_url) or Answer())
    assert updates.fetch_latest()["version"] == "9.0.0"
    assert seen == [updates.LATEST_URL]


def test_no_hardware_here():
    assert not {"device", "inputs", "leds", "display", "capabilities"} \
        & set(vars(fetch))
