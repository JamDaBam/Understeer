"""Getting one small file from a fixed address, without GTK: the
community catalog and GitHub's list of releases.

Exactly the address asked is read: a redirect is never followed, the
answer must be "200 OK" from that same address, and its size is limited.
HTTPS works as urllib sets it up, so certificates and host names are
checked. Nothing is sent but the request itself. The callers only ask
fixed https:// addresses of their own.

Time: each step on the network (connecting, each read) has the timeout,
and while the body is read the deadline is checked before and after each
read of the socket. The deadline is no total limit for the request:
connecting and reading the status line and headers are only bounded by
the timeout of each read, so a server sending its headers a byte at a
time stays within neither. One read that blocks can also end up to the
timeout after the deadline.

read_body() is that reading of the body, also for callers that open the
answer themselves (e.g. following redirects).

open_https() opens such an answer for pictures: it follows redirects, but
only to https:// addresses (on any host), so a picture asked over TLS
never comes without it.
"""
import http.client
import time
import urllib.error
import urllib.parse
import urllib.request

from . import USER_AGENT

CHUNK = 64 * 1024


class Error(Exception):
    """The file couldn't be got. kind: "offline", "timeout", "http" (code
    is the HTTP status), "redirect" or "too_big"."""

    def __init__(self, kind, message, code=None):
        super().__init__(message)
        self.kind = kind
        self.code = code


def release(answer):
    """Close an answer that nobody else closes: the one an HTTPError holds,
    or a redirect that isn't followed. A failure while closing it is
    dropped, so it never hides the error the caller is raising; only
    what closing a file can raise is dropped. None: nothing to close."""
    if answer is None:
        return
    try:
        answer.close()
    except (OSError, ValueError):
        pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is not followed anywhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib closes fp itself only on its way to follow the redirect;
        # stopped here, the answer is never read and is ours to close
        release(fp)
        raise Error("redirect", f"redirected to {newurl}")


def _opener():
    return urllib.request.build_opener(_NoRedirect)


class _HttpsRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is followed only to an https:// address, on any host;
    one to http://, ftp:// or anything else is refused before its address
    is even looked up. urllib's own checks and limits (schemes, loops, at
    most 10 redirects) stay."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # newurl is absolute here (urllib joins a relative one first), and
        # urlsplit() gives its scheme in lower case
        if urllib.parse.urlsplit(newurl).scheme != "https":
            # Stopped here, the answer is never read and is ours to close.
            # Where it points is left out of the message: the server chose it.
            release(fp)
            raise Error("redirect", f"a redirect away from HTTPS (HTTP {code})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_https(request, timeout):
    """urlopen() that follows redirects to https:// only (_HttpsRedirect);
    TLS, proxies and the handling of HTTP errors are urllib's as before.
    Raises Error("redirect") for a refused redirect."""
    return urllib.request.build_opener(_HttpsRedirect).open(request, timeout=timeout)


def get(url, accept, limit, timeout, deadline):
    """The bytes at url, at most limit of them; raises Error.

    timeout: seconds for connecting and for each read; deadline: seconds
    from the start after which reading the body stops, so a server
    sending a byte now and then can't hold it up (see above for what it
    doesn't cover)."""
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": accept})
    start = time.monotonic()
    try:
        with _opener().open(request, timeout=timeout) as response:
            if response.status != 200 or response.geturl() != url:
                raise Error("http", f"HTTP {response.status}", response.status)
            data = read_body(response, limit, start + deadline)
    except Error:
        raise
    except urllib.error.HTTPError as e:
        release(e)                          # it holds the answer, unread
        raise Error("http", f"HTTP {e.code}", e.code) from None
    except TimeoutError as e:
        raise Error("timeout", str(e) or "timed out") from None
    except urllib.error.URLError as e:
        kind = "timeout" if isinstance(e.reason, TimeoutError) else "offline"
        raise Error(kind, str(e.reason)) from None
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise Error("offline", str(e) or type(e).__name__) from None
    return data


def iter_body(response, limit, deadline_at, clock=time.monotonic, reached=False):
    """The body of an open answer in pieces as they come, at most limit
    bytes in all, without keeping them; raises Error ("too_big",
    "timeout", or "offline" for an answer cut short).

    read1() asks for at most one read of the socket, so the deadline (a
    time of clock()) is checked before and after each of them, also after
    the one that finds the end; see above for what it doesn't cover. A
    Content-Length above limit is refused before anything is read. Never
    more than one byte past the limit is asked, which tells that there is
    more. Errors of the connection itself (OSError,
    http.client.HTTPException) are passed on as they are. Closing the
    answer is the caller's.

    reached: the deadline counts once reached, not only once past, so no
    read starts at it (the AppImage download asks for that)."""
    def late():
        now = clock()
        return now >= deadline_at if reached else now > deadline_at
    length = response.headers.get("Content-Length", "")
    if length.isdigit() and int(length) > limit:
        raise Error("too_big", f"{length} bytes announced")
    got = 0
    while True:
        if late():
            raise Error("timeout", "the download took too long")
        chunk = response.read1(min(CHUNK, limit + 1 - got))
        if chunk:
            got += len(chunk)
            if got > limit:
                raise Error("too_big", f"more than {limit} bytes")
        if late():
            raise Error("timeout", "the download took too long")
        if not chunk:
            break
        yield chunk
    # read1 doesn't tell when the connection ended early
    if length.isdigit() and got != int(length):
        raise Error("offline", f"{got} of {length} bytes came")


def read_body(response, limit, deadline_at, clock=time.monotonic):
    """The body of an open answer, at most limit bytes; raises Error
    ("too_big", "timeout", or "offline" for an answer cut short). See
    iter_body(), which reads it."""
    return b"".join(iter_body(response, limit, deadline_at, clock))
