import errno
import os
from unittest import mock

import pytest

from understeer import capabilities, display


@pytest.fixture
def display_file(tmp_path):
    """A write-only display file, as hid-fanatecff makes it."""
    path = tmp_path / "display"
    path.write_text("")
    path.chmod(0o220)
    return path


def written(path):
    path.chmod(0o640)
    return path.read_text()


# -- which text the display can show -----------------------------------------

@pytest.mark.parametrize("text", [
    "1", "12", "123", "abc", "ABC", "P1", "-_-", "[ ]", "1.2.3.", "1,5",
    "9.", " .", "  1", "Hi", "zyu",
])
def test_text_the_display_can_show(text):
    assert display.check_text(text) is None


@pytest.mark.parametrize("text, problem", [
    ("", (display.EMPTY, None)),
    ("   ", (display.EMPTY, None)),
    ("k", (display.UNSUPPORTED, "k")),          # left dark by the driver
    ("aMx", (display.UNSUPPORTED, "M")),
    ("1+2", (display.UNSUPPORTED, "+")),
    ("ä", (display.UNSUPPORTED, "ä")),          # not ASCII
    ("a\n", (display.UNSUPPORTED, "\n")),
    (".5", (display.POINT, ".")),               # follows no character
    ("1..", (display.POINT, ".")),              # two in a row overflow
    ("1.,", (display.POINT, ",")),
    ("1234", (display.TOO_LONG, 4)),
    ("1.2.3.4", (display.TOO_LONG, 4)),
])
def test_text_the_display_cant_show(text, problem):
    assert display.check_text(text) == problem


def test_longest_text_fits_the_driver():
    """Three positions with a point each: 6 bytes, the driver takes 7."""
    text = "8.8.8."
    assert display.check_text(text) is None
    assert len(text.encode("ascii")) <= display.MAX_BYTES


# -- writing ------------------------------------------------------------------

def test_write_sends_the_text_in_one_write(display_file):
    with mock.patch("os.write", wraps=os.write) as write:
        assert display.write(str(display_file), "P1.") == display.OK
    write.assert_called_once()
    assert written(display_file) == "P1."      # no newline


def test_clear_turns_off_every_position(display_file):
    assert display.clear(str(display_file)) == display.OK
    text = written(display_file)
    assert text == "   " and display.check_text(text) == (display.EMPTY, None)


def test_missing_display_is_not_made(tmp_path):
    path = tmp_path / "display"
    assert display.write(str(path), "1") == display.UNAVAILABLE
    assert not path.exists()


def test_no_permission(display_file):
    error = PermissionError(errno.EACCES, "Permission denied")
    with mock.patch("os.open", side_effect=error):
        assert display.write(str(display_file), "1") == display.DENIED
    with mock.patch("os.open", side_effect=error):
        assert display.clear(str(display_file)) == display.DENIED


def test_unplugged_while_writing(display_file):
    """The kernel answers ENODEV once the wheelbase is gone; the file is
    closed all the same."""
    with mock.patch("os.write", side_effect=OSError(errno.ENODEV, "No such device")), \
         mock.patch("os.close", wraps=os.close) as close:
        assert display.write(str(display_file), "1") == display.UNAVAILABLE
    close.assert_called_once()


def test_refused_by_the_driver(display_file):
    with mock.patch("os.write", side_effect=OSError(errno.EINVAL, "Invalid argument")):
        assert display.write(str(display_file), "1") == display.FAILED


def test_partly_written(display_file):
    with mock.patch("os.write", return_value=1):
        assert display.write(str(display_file), "123") == display.FAILED


def test_too_long_is_not_written(display_file):
    with mock.patch("os.open") as opened:
        assert display.write(str(display_file), "12345678") == display.FAILED
    opened.assert_not_called()


# -- whether the display is offered ----------------------------------------------

@pytest.fixture
def hid(tmp_path, monkeypatch):
    """A wheelbase HID folder without tuning folder, found by "range"."""
    monkeypatch.setattr(capabilities, "HID_GLOB", str(tmp_path / "hid" / "*"))
    monkeypatch.setattr(capabilities, "LEDS_GLOB", str(tmp_path / "none" / "*"))
    monkeypatch.setattr(capabilities, "WHEEL_GLOB", str(tmp_path / "none" / "*"))
    monkeypatch.setattr(capabilities.device, "SYSFS_GLOB", str(tmp_path / "none" / "*"))
    monkeypatch.setattr(capabilities.device, "RUMBLE_GLOB", str(tmp_path / "none" / "*"))
    folder = tmp_path / "hid" / "0003:0EB7:0020.0005"
    folder.mkdir(parents=True)
    (folder / "range").write_text("900\n")
    return folder


def test_display_available(hid):
    (hid / "display").write_text("")
    (hid / "display").chmod(0o220)
    caps = capabilities.detect()
    assert caps.can_write(capabilities.DISPLAY)
    assert not caps.can_read(capabilities.DISPLAY)


def test_display_unavailable(hid):
    assert not capabilities.detect().has(capabilities.DISPLAY)


@pytest.mark.skipif(os.geteuid() == 0, reason="root may write anything")
def test_display_not_writable(hid):
    (hid / "display").write_text("")
    (hid / "display").chmod(0o444)
    caps = capabilities.detect()
    assert caps.has(capabilities.DISPLAY)
    assert not caps.can_write(capabilities.DISPLAY)
