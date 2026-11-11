"""Starting Understeer at login, in the background. Works without GTK.

At login the desktop starts what has a .desktop file in ~/.config/autostart
(XDG Autostart, understood by KDE, GNOME and the others). Understeer then
runs with --background: no window, only the tray icon, so presets per game
work without opening it first.
"""
import os
import pathlib
import re
import sys

from . import APP_ID, DATA_DIR, config

BACKGROUND_OPTION = "--background"


def autostart_file():
    base = os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config"
    return pathlib.Path(base) / "autostart" / f"{APP_ID}.desktop"


def launch_command():
    """How to start this Understeer again: the AppImage, or the source."""
    appimage = os.environ.get("APPIMAGE")       # set by the AppImage runtime
    if appimage:
        return [appimage]
    return ["env", f"PYTHONPATH={DATA_DIR}", sys.executable, "-m", "understeer"]


_RESERVED = re.compile(r"""[\s"'\\><~|&;$*?#()`]""")


def _exec_quote(arg):
    """One argument for Exec= of a .desktop file.

    Arguments with special characters go in double quotes, with ", `, $
    and \\ escaped; then the value is escaped once more as a string (so a
    backslash is written four times) and % doubled.
    """
    if _RESERVED.search(arg):
        arg = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    return arg.replace("\\", "\\\\").replace("%", "%%")


def autostart_entry(command):
    """Contents of the autostart file for command (a list)."""
    exec_line = " ".join(_exec_quote(a) for a in [*command, BACKGROUND_OPTION])
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Understeer\n"
        "Comment=Applies the presets of your games in the background\n"
        f"Exec={exec_line}\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-KDE-autostart-after=panel\n")


def autostart_enabled():
    return autostart_file().is_file()


def set_autostart(enabled):
    """Write or remove the autostart file. Raises OSError."""
    path = autostart_file()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        config.write_file(path, autostart_entry(launch_command()))
    else:
        path.unlink(missing_ok=True)


def refresh_autostart():
    """Keep the autostart file pointing at this Understeer, e.g. after the
    AppImage was moved or replaced by an update. Returns False if it
    could not be written."""
    if not autostart_enabled():
        return True
    try:
        set_autostart(True)
        return True
    except OSError:
        return False
