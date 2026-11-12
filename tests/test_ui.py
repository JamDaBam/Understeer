"""The whole app on an invisible screen: tests/ui_smoke.py under Xvfb.

Skipped without Xvfb or without GTK 4 and Libadwaita for Python. Xvfb is
started here, so no window shows up on the desktop while testing.
"""
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SMOKE = ROOT / "tests" / "ui_smoke.py"


def _have_gtk():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        return True
    except (ImportError, ValueError):
        return False


def _missing(reason):
    """Skip; but on CI (GitHub sets CI=true) the UI test must run."""
    if os.environ.get("CI"):
        pytest.fail(f"UI test can't run on CI: {reason}")
    pytest.skip(reason)


@pytest.fixture
def display():
    """Number of a fresh Xvfb display, stopped after the test."""
    if not shutil.which("Xvfb"):
        _missing("Xvfb not installed")
    if not _have_gtk():
        _missing("GTK 4 / Libadwaita for Python not installed")
    read, write = os.pipe()
    xvfb = subprocess.Popen(
        ["Xvfb", "-displayfd", str(write), "-screen", "0", "1280x900x24",
         "-nolisten", "tcp"],
        pass_fds=(write,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write)
    with os.fdopen(read) as f:
        number = f.readline().strip()       # Xvfb writes it once it is ready
    if not number:
        xvfb.kill()
        _missing("Xvfb did not start")
    yield number
    xvfb.terminate()
    xvfb.wait(timeout=10)


def test_app_runs_through(display, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in ("WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                        "LANGUAGE", "LC_ALL", "LC_MESSAGES")}
    env.update({
        "DISPLAY": f":{display}", "GDK_BACKEND": "x11",
        "GSK_RENDERER": "cairo",        # no GPU needed
        "NO_AT_BRIDGE": "1",
        "LANG": "C.UTF-8",              # the checks expect English text
        # No desktop portals: on KDE they keep running after the test, one
        # more of them after every run.
        "ADW_DISABLE_PORTAL": "1", "GDK_DEBUG": "no-portals",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "PYTHONPATH": str(ROOT),
    })
    command = [sys.executable, str(SMOKE), str(tmp_path)]
    if shutil.which("dbus-run-session"):    # a session bus for the tray icon
        command = ["dbus-run-session", "--", *command]
    # Into files, not pipes: services the session bus starts (on KDE one
    # waits a minute for a notification daemon) would keep a pipe open, and
    # the test would wait for them long after the app has ended.
    out_path, err_path = tmp_path / "stdout.txt", tmp_path / "stderr.txt"
    with open(out_path, "w") as out, open(err_path, "w") as err:
        result = subprocess.run(command, env=env, cwd=ROOT, stdout=out,
                                stderr=err, stdin=subprocess.DEVNULL,
                                timeout=150)
    stdout = out_path.read_text(errors="replace")
    output = stdout + err_path.read_text(errors="replace")
    assert result.returncode == 0, output
    assert "failures: 0" in stdout, output
