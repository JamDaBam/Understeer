"""Work that takes a while (network, downloads) in a thread, with the
result handed back on the main loop."""
import logging
import threading

from gi.repository import GLib

log = logging.getLogger("understeer.tasks")


def in_thread(work, done, expected=()):
    """Run work() in a thread, then done(result, error) on the main loop;
    error is None or what work() raised. Errors of the expected types are
    the caller's to show; any other is logged as a bug as well, and done()
    is called all the same, so nothing waits for ever (e.g. a spinner)."""
    def run():
        try:
            result, error = work(), None
        except expected as e:
            result, error = None, e
        except Exception as e:
            log.exception("Unexpected error in a background task")
            result, error = None, e
        GLib.idle_add(lambda: done(result, error) and False)
    threading.Thread(target=run, daemon=True).start()
