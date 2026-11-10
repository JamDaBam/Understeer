"""Starting at login: the autostart file."""
import sys

import pytest

from understeer import DATA_DIR, background


@pytest.fixture
def autostart(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("APPIMAGE", raising=False)
    return tmp_path / "autostart" / "io.github.JamDaBam.Understeer.desktop"


def exec_line(text):
    return next(line for line in text.splitlines() if line.startswith("Exec="))


def test_exec_quoting():
    q = background._exec_quote
    assert q("/opt/Understeer.AppImage") == "/opt/Understeer.AppImage"
    assert q("/home/a/My Apps/x") == '"/home/a/My Apps/x"'
    # $ escaped inside quotes, then the backslash escaped as a string; % doubled
    assert q("/a b/Under$teer 100%") == '"/a b/Under\\\\$teer 100%%"'
    assert q('say "hi"') == '"say \\\\"hi\\\\""'


def test_autostart_for_the_appimage(autostart, monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/home/a/Apps/Understeer-1.9.0-x86_64.AppImage")
    assert not background.autostart_enabled()
    background.set_autostart(True)
    assert background.autostart_enabled() and autostart.is_file()
    text = autostart.read_text()
    assert exec_line(text) == \
        "Exec=/home/a/Apps/Understeer-1.9.0-x86_64.AppImage --background"
    assert "Icon=io.github.JamDaBam.Understeer" in text
    background.set_autostart(False)
    assert not autostart.exists() and not background.autostart_enabled()
    background.set_autostart(False)                 # already off: fine


def test_autostart_from_source(autostart):
    background.set_autostart(True)
    line = exec_line(autostart.read_text())
    assert f"PYTHONPATH={DATA_DIR}" in line
    assert sys.executable in line
    assert line.endswith("-m understeer --background")


def test_refresh_follows_a_moved_appimage(autostart, monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/old/Understeer.AppImage")
    assert background.refresh_autostart()           # off: nothing written
    assert not autostart.exists()
    background.set_autostart(True)
    monkeypatch.setenv("APPIMAGE", "/new/Understeer.AppImage")
    assert background.refresh_autostart()
    assert exec_line(autostart.read_text()) == "Exec=/new/Understeer.AppImage --background"
