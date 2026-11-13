"""Text on the LED display of the steering wheel, without GTK.

hid-fanatecff gives every wheelbase a write-only file HID/display
(ftec_set_display and seg_bits in hid-ftecff.c). What it does with it:

- More than 7 bytes are refused with EINVAL.
- The display has three seven-segment positions. Each character takes one;
  a "." or "," right after a character lights the point of that position
  instead of taking one of its own. Characters beyond the third position
  are ignored, shorter text is moved to the right.
- Shown are 0-9, the letters a-z in either case except k, m, v, w and x,
  "-", "_", "[" and "]". Anything else, those five letters included, leaves
  its position dark, so a space is a dark position.
- A point that doesn't follow a character lights only the point, but two
  points in a row overflow to a dark position.

The driver sends the text to the wheelbase and reports success whether or
not the steering wheel on it has a display; that can't be seen anywhere.
Nor can it be seen whether a game shows something on the display through
the Fanatec SDK: then whoever writes last wins. So this only writes when
asked to and never by itself.
"""
import errno
import logging
import os
import unicodedata

log = logging.getLogger("understeer.display")

POSITIONS = 3               # seven-segment positions on the display
MAX_BYTES = 7               # the driver refuses more
POINTS = ".,"               # light the point of the character before
CHARACTERS = frozenset(
    "0123456789abcdefghijlnopqrstuyzABCDEFGHIJLNOPQRSTUYZ -_[]")
CLEAR_TEXT = " " * POSITIONS    # every position dark

# Why a text can't be shown, see check_text()
EMPTY = "empty"                 # nothing but spaces: that is Clear
UNSUPPORTED = "unsupported"     # a character the display can't show
POINT = "point"                 # a point or comma that follows no character
TOO_LONG = "too_long"           # more than POSITIONS positions

# How write() ended
OK = "ok"
UNAVAILABLE = "unavailable"     # no display file (any more): unplugged?
DENIED = "denied"               # no permission
FAILED = "failed"               # refused, or another error


def check_text(text):
    """None if text can be shown as it is, else (reason, detail): EMPTY,
    (UNSUPPORTED, the character), POINT, or (TOO_LONG, positions used)."""
    if not text.strip():
        return EMPTY, None
    positions = 0
    after_character = False
    for c in text:
        if c in POINTS:
            if not after_character:
                return POINT, c
            after_character = False     # only one point per position
        elif c in CHARACTERS:
            positions += 1
            after_character = True
        else:
            return UNSUPPORTED, c
    if positions > POSITIONS:
        return TOO_LONG, positions
    return None


def name_text(name):
    """What the display shows for a name, e.g. of a preset, or None if
    nothing of it can be shown. Always the same for the same name:

    - accents are dropped ("é" -> "e"),
    - characters the display can't show are left out, and so are spaces,
      so that the positions show as much of the name as they can,
    - a point or comma stays where it follows a character that is kept,
    - the first POSITIONS characters are kept, with a point after the last.

    E.g. "Rally 1" -> "Ral", "F1 2024" -> "F12", "Wet" -> "et", "1.5" -> "1.5".
    """
    shown = []
    for c in unicodedata.normalize("NFKD", name):
        if c in POINTS:
            if shown and shown[-1][-1] not in POINTS:
                shown[-1] += c
        elif c in CHARACTERS and c != " ":
            if len(shown) == POSITIONS:
                break
            shown.append(c)
    return "".join(shown) or None


def write(path, text):
    """Send text, which check_text() accepted, or CLEAR_TEXT, to the display
    file path. Returns OK, UNAVAILABLE, DENIED or FAILED; never raises."""
    data = text.encode("ascii")
    if len(data) > MAX_BYTES:
        return FAILED
    try:
        # Without O_CREAT: a display file that is gone is not made anew.
        # O_TRUNC, as "echo >" uses, means nothing to sysfs.
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    except OSError as e:
        return _failure(path, e)
    try:
        # One write: the driver takes each write as a whole text.
        written = os.write(fd, data)
    except OSError as e:
        return _failure(path, e)
    finally:
        os.close(fd)
    if written != len(data):
        log.warning("Display took only %d of %d bytes", written, len(data))
        return FAILED
    log.info("Display: wrote %r", text)
    return OK


def _failure(path, e):
    log.warning("Could not write to the display %s: %s", path, e.strerror or e)
    if e.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO):
        return UNAVAILABLE      # ENODEV: the wheelbase went while writing
    if e.errno in (errno.EACCES, errno.EPERM):
        return DENIED
    return FAILED


def clear(path):
    """Turn off every position of the display. Returns like write()."""
    return write(path, CLEAR_TEXT)
