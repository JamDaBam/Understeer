"""Session log for finding problems.

Each start writes a new log file; the one of the previous start is kept
next to it, so a problem can still be looked at after restarting:

    ~/.local/state/Understeer/understeer.log           this session
    ~/.local/state/Understeer/understeer.previous.log  the one before

Works without GTK.
"""
import logging
import os
import pathlib
import platform
import subprocess
import sys
import threading

from . import DATA_DIR

_state = os.environ.get("XDG_STATE_HOME") or str(pathlib.Path.home() / ".local" / "state")
LOG_DIR = pathlib.Path(_state) / "Understeer"
LOG_FILE = LOG_DIR / "understeer.log"
PREVIOUS_LOG_FILE = LOG_DIR / "understeer.previous.log"
MAX_BYTES = 5 * 1024 * 1024         # per session; dragging a slider logs a lot
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

log = logging.getLogger("understeer")


class _CappedFileHandler(logging.FileHandler):
    """Stops writing once the file reached MAX_BYTES, with one last note."""

    def __init__(self, path, max_bytes=MAX_BYTES):
        super().__init__(path, mode="w", encoding="utf-8")
        self.max_bytes = max_bytes
        self.full = False

    def emit(self, record):
        if self.full:
            return
        if self.stream and self.stream.tell() >= self.max_bytes:
            self.full = True
            self.stream.write("... log full, further messages are dropped\n")
            self.flush()
            return
        super().emit(record)


def version():
    """Understeer's version: from the AppImage build, else from git."""
    try:
        return (DATA_DIR / "VERSION").read_text().strip()
    except OSError:
        pass
    try:
        out = subprocess.run(["git", "describe", "--tags", "--always", "--dirty"],
                             cwd=DATA_DIR, capture_output=True, text=True,
                             timeout=2)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _read(path):
    try:
        return pathlib.Path(path).read_text().strip()
    except OSError:
        return None


def _driver():
    return _read("/sys/module/hid_fanatec/version") or "version unknown"


def _distribution():
    for line in (_read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return "unknown"


def debug_info():
    """Versions and paths for a bug report, as plain text."""
    return "\n".join([
        f"Understeer {version()}",
        f"Python {platform.python_version()}",
        f"System: {_distribution()}, kernel {platform.release()}",
        f"hid-fanatecff driver: {_driver()}",
        f"Log file: {LOG_FILE}",
    ])


def setup(path=None):
    """Start the session log. Returns the log file, or None if it can't be written."""
    path = pathlib.Path(path) if path else LOG_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.replace(path.with_name(path.stem + ".previous" + path.suffix))
        handler = _CappedFileHandler(path)
    except OSError as e:
        print(f"Understeer: cannot write log file {path}: {e}", file=sys.stderr)
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    log.setLevel(logging.DEBUG)
    for old in list(log.handlers):         # setup() again, e.g. in tests
        log.removeHandler(old)
        old.close()
    log.addHandler(handler)
    log.propagate = False

    console = logging.StreamHandler()      # problems also go to the terminal
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("Understeer: %(levelname)s: %(message)s"))
    log.addHandler(console)

    _catch_crashes()
    log.info("Understeer %s started", version())
    log.info("Python %s, %s, kernel %s", platform.python_version(),
             _distribution(), platform.release())
    log.info("hid-fanatecff driver: %s", _driver())
    return path


def _catch_crashes():
    """Log errors that would otherwise only be printed to the terminal."""
    previous = sys.excepthook
    previous_thread = threading.excepthook

    def excepthook(kind, value, tb):
        log.error("Unexpected error", exc_info=(kind, value, tb))
        previous(kind, value, tb)

    def thread_excepthook(args):
        log.error("Unexpected error in thread %s",
                  args.thread.name if args.thread else "?",
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        previous_thread(args)

    if getattr(sys.excepthook, "_understeer", False):
        return
    excepthook._understeer = True
    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
