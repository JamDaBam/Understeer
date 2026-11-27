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


# Taken over from the environment the tests run in, nothing else: no
# proxies, no Python or GTK hooks, no desktop session, no real buses.
PASSED_ON = ("PATH", "XDG_DATA_DIRS", "XDG_CONFIG_DIRS", "CI", "SMOKE_TRACE")

# A session bus of its own that starts no services: no portals, no
# notification daemon, no dconf; the tray icon only needs the bus.
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC
 "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:dir={folder}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


def isolated_env(tmp_path, display):
    """The environment of the smoke test: everything below tmp_path."""
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    for folder in (home, runtime, tmp_path / "tmp"):
        folder.mkdir()
    runtime.chmod(0o700)
    env = {k: os.environ[k] for k in PASSED_ON if k in os.environ}
    env.update({
        "DISPLAY": f":{display}", "GDK_BACKEND": "x11",
        "GSK_RENDERER": "cairo",        # no GPU needed
        "NO_AT_BRIDGE": "1",
        "LANG": "C.UTF-8",              # the checks expect English text
        # No desktop portals: on KDE they keep running after the test, one
        # more of them after every run.
        "ADW_DISABLE_PORTAL": "1", "GDK_DEBUG": "no-portals",
        "GSETTINGS_BACKEND": "memory",  # no dconf
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_RUNTIME_DIR": str(runtime),
        "TMPDIR": str(tmp_path / "tmp"),
        # A system bus that isn't there: nothing reaches the real one
        "DBUS_SYSTEM_BUS_ADDRESS": f"unix:path={runtime / 'no-system-bus'}",
        "PYTHONPATH": str(ROOT),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",     # nothing written to the repository
        # Only for the guard: the real home, which the test must not touch
        "UNDERSTEER_TEST_REAL_HOME": os.path.expanduser("~"),
    })
    return env


def test_app_runs_through(display, tmp_path):
    # Without its own bus the test would not be isolated: never run then
    if not shutil.which("dbus-run-session"):
        _missing("dbus-run-session not installed")
    env = isolated_env(tmp_path, display)
    # A Unix socket path has at most 107 bytes; the bus adds "/dbus-" and
    # ten characters to the folder
    assert len(os.fsencode(env["XDG_RUNTIME_DIR"])) < 90, \
        "the test folder's path is too long for the bus socket"
    bus_config = tmp_path / "session.conf"
    bus_config.write_text(BUS_CONFIG.format(folder=env["XDG_RUNTIME_DIR"]))
    command = ["dbus-run-session", f"--config-file={bus_config}", "--",
               sys.executable, str(SMOKE), str(tmp_path)]
    # Into files, not pipes: a service started on the bus would keep a pipe
    # open, and the test would wait for it long after the app has ended.
    out_path, err_path = tmp_path / "stdout.txt", tmp_path / "stderr.txt"
    with open(out_path, "w") as out, open(err_path, "w") as err:
        result = subprocess.run(command, env=env, cwd=tmp_path, stdout=out,
                                stderr=err, stdin=subprocess.DEVNULL,
                                timeout=150)
    stdout = out_path.read_text(errors="replace")
    output = stdout + err_path.read_text(errors="replace")
    assert result.returncode == 0, output
    assert "failures: 0" in stdout, output
    # The guard of isolation.py refused nothing; without the line the
    # smoke test didn't get to the end
    assert "\nguard violations: 0\n" in stdout, output
    # Text from others (preset names, the community catalog) is never read
    # as markup; GTK warns when it tries and fails.
    assert "from markup due to error" not in output, output
