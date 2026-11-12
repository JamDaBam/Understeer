"""Background work hands its result back on the main loop, also when it
fails unexpectedly, so nothing waits for ever."""
import pytest

pytest.importorskip("gi")
from gi.repository import GLib  # noqa: E402

from understeer.ui import tasks  # noqa: E402

# PyGObject on Python 3.14 warns about asyncio internals when the test turns
# the main loop by hand; that is not Understeer's code.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning:gi.events")


def run(work, expected=()):
    loop, got = GLib.MainLoop(), []

    def done(result, error):
        got.append((result, error))
        loop.quit()
    tasks.in_thread(work, done, expected)
    GLib.timeout_add_seconds(5, loop.quit)
    loop.run()
    return got


def test_result():
    assert run(lambda: 42) == [(42, None)]


def test_expected_error():
    [(result, error)] = run(lambda: int("x"), ValueError)
    assert result is None and isinstance(error, ValueError)


def test_unexpected_error_still_ends(caplog):
    def broken():
        raise RuntimeError("bug")
    [(result, error)] = run(broken, ValueError)
    assert result is None and isinstance(error, RuntimeError)
    assert "Unexpected error" in caplog.text
