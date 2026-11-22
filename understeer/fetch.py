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
"""
import http.client
import time
import urllib.error
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


def read_body(response, limit, deadline_at, clock=time.monotonic):
    """The body of an open answer, at most limit bytes; raises Error
    ("too_big", "timeout", or "offline" for an answer cut short).

    deadline_at: the time of clock() after which reading stops, checked
    before and after each read of the socket; see above for what it
    doesn't cover. Errors of the connection itself (OSError,
    http.client.HTTPException) are passed on as they are. Closing the
    answer is the caller's."""
    length = response.headers.get("Content-Length", "")
    if length.isdigit() and int(length) > limit:
        raise Error("too_big", f"{length} bytes announced")
    data = bytearray()
    # read1: at most one read of the socket, so the deadline is checked
    # after every piece that comes, however small. Never more than one
    # byte past the limit is asked, which tells that there is more.
    while True:
        if clock() > deadline_at:
            raise Error("timeout", "the download took too long")
        chunk = response.read1(min(CHUNK, limit + 1 - len(data)))
        if not chunk:
            break
        data += chunk
        if len(data) > limit:
            raise Error("too_big", f"more than {limit} bytes")
        if clock() > deadline_at:
            raise Error("timeout", "the download took too long")
    # read1 doesn't tell when the connection ended early
    if length.isdigit() and len(data) != int(length):
        raise Error("offline", f"{len(data)} of {length} bytes came")
    return bytes(data)
