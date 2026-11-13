"""Rev, RGB and flag LEDs for the LED diagnostics, without GTK.

Which LEDs there are comes from capabilities.detect(); this only adds what
each LED folder tells about itself (see the LED class of the kernel and
hid-ftecff.c / hid-ftecff-wheel.c of hid-fanatecff):

    brightness        0 .. max_brightness; 0 is off
    max_brightness    1 for the single colour rev LEDs, 255 for RGB LEDs
                      (1 on wheels with one RGB stripe, e.g. CSL P1)
    multi_index       the colour channels of an RGB LED, e.g. "red green blue"
    multi_intensity   one value per channel, 0 .. max_brightness

Reading gives what the driver last set: for single colour LEDs it asks the
driver, for RGB LEDs it is what was last written to the files. Nothing here
runs by itself; every write is a click in the dialog or a step of the short
test sequence, which the dialog drives and can cancel.
"""
import errno
import logging
import os
import re
from dataclasses import dataclass

from . import capabilities

BASE = "base"       # on the wheelbase
RIM = "rim"         # on the steering wheel
BOTH = "both"       # drivers before October 2026: one set lights both

REV = "rev"
LEFT_FLAG = "left"
RIGHT_FLAG = "right"

OK = "ok"
UNAVAILABLE = "unavailable"     # file gone: unplugged, wheel taken off?
DENIED = "denied"               # no permission
FAILED = "failed"               # refused, or another error

# Test colours by the channels they light, in the order offered
COLORS = (
    ("red", frozenset({"red"})),
    ("green", frozenset({"green"})),
    ("blue", frozenset({"blue"})),
    ("yellow", frozenset({"red", "green"})),
    ("cyan", frozenset({"green", "blue"})),
    ("magenta", frozenset({"red", "blue"})),
    ("white", frozenset({"red", "green", "blue"})),
)
COLOR_CHANNELS = dict(COLORS)
SWEEP_COLOR = "white"           # rev and flag LEDs in the test sequence

log = logging.getLogger("understeer.leds")


@dataclass(frozen=True)
class Led:
    folder: str
    location: str           # BASE, RIM or BOTH
    kind: str               # REV, LEFT_FLAG or RIGHT_FLAG
    number: int             # from its name, from 1
    max_brightness: int
    channels: tuple         # e.g. ("red", "green", "blue"); () if not RGB
    readable: bool
    writable: bool

    @property
    def rgb(self):
        return bool(self.channels)

    @property
    def name(self):
        return os.path.basename(self.folder)


@dataclass(frozen=True)
class State:
    brightness: int
    intensity: tuple | None = None   # per channel, for RGB LEDs


_NAME = re.compile(r"(RPM|RFLAG|LFLAG)(\d+)$")
_KINDS = {"RPM": REV, "RFLAG": RIGHT_FLAG, "LFLAG": LEFT_FLAG}


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _int(path, default=None):
    try:
        return int(_read(path))
    except (TypeError, ValueError):
        return default


def _folders(caps, feature):
    f = caps.get(feature) if caps else None
    return [os.path.dirname(p) for p in f.paths] if f else []


def find(caps):
    """The LEDs in caps, base rev LEDs first, then the wheel's rev LEDs,
    left and right flags; each in the order of its number."""
    if caps is None:
        return []
    base = _folders(caps, capabilities.BASE_REV_LEDS)
    rim = (_folders(caps, capabilities.RIM_REV_LEDS)
           + _folders(caps, capabilities.FLAG_LEDS))
    rgb_folders = set(_folders(caps, capabilities.RGB_LEDS))
    rgb_feature = caps.get(capabilities.RGB_LEDS)
    leds = []
    for folder in dict.fromkeys(base + rim):     # once, in order
        m = _NAME.search(os.path.basename(folder))
        if not m:
            continue
        in_base, in_rim = folder in base, folder in rim
        location = BOTH if in_base and in_rim else (BASE if in_base else RIM)
        feature = caps.get(capabilities.BASE_REV_LEDS if in_base else (
            capabilities.FLAG_LEDS if "FLAG" in m[1]
            else capabilities.RIM_REV_LEDS))
        readable, writable = feature.readable, feature.writable
        channels = ()
        if folder in rgb_folders:
            index = _read(os.path.join(folder, "multi_index"))
            channels = tuple(index.split()) if index else ()
            readable = readable and rgb_feature.readable
            writable = writable and rgb_feature.writable
        leds.append(Led(folder=folder, location=location, kind=_KINDS[m[1]],
                        number=int(m[2]),
                        max_brightness=max(1, _int(os.path.join(
                            folder, "max_brightness"), 1)),
                        channels=channels, readable=readable,
                        writable=writable))
    order = {BASE: 0, BOTH: 1, RIM: 2}
    kinds = {REV: 0, LEFT_FLAG: 1, RIGHT_FLAG: 2}
    return sorted(leds, key=lambda l: (kinds[l.kind], order[l.location],
                                       l.number))


def colors(leds):
    """Names of the COLORS the RGB LEDs among leds can show together."""
    have = set()
    for led in leds:
        have.update(led.channels)
    return [name for name, channels in COLORS if channels <= have]


def lit(led, color=SWEEP_COLOR):
    """The State of led switched on, RGB LEDs in color."""
    if not led.rgb:
        return State(led.max_brightness)
    wanted = COLOR_CHANNELS.get(color, COLOR_CHANNELS[SWEEP_COLOR])
    return State(led.max_brightness, tuple(
        led.max_brightness if ch in wanted else 0 for ch in led.channels))


def off(led):
    return State(0)


def read_state(led):
    """The State the driver reports, or None if it can't be read."""
    if not led.readable:
        return None
    brightness = _int(os.path.join(led.folder, "brightness"))
    if brightness is None:
        return None
    if not led.rgb:
        return State(brightness)
    text = _read(os.path.join(led.folder, "multi_intensity"))
    try:
        intensity = tuple(int(v) for v in text.split())
    except (AttributeError, ValueError):
        return None
    if len(intensity) != len(led.channels):
        return None
    return State(brightness, intensity)


def is_on(state):
    """Lit: a brightness and, for an RGB LED, a colour (the driver starts
    them at full brightness with every channel at 0)."""
    return bool(state and state.brightness
                and (state.intensity is None or any(state.intensity)))


def _failure(path, e):
    log.warning("Could not write LED %s: %s", path, e.strerror or e)
    if e.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO):
        return UNAVAILABLE
    if e.errno in (errno.EACCES, errno.EPERM):
        return DENIED
    return FAILED


def _write(path, text):
    try:
        # Without O_CREAT: a file that is gone is not made anew.
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    except OSError as e:
        return _failure(path, e)
    try:
        os.write(fd, text.encode("ascii"))
    except OSError as e:
        return _failure(path, e)
    finally:
        os.close(fd)
    return OK


def write_state(led, state):
    """Set led to state. The colour goes first: the driver sends it with
    the brightness. Returns OK, UNAVAILABLE, DENIED or FAILED; never raises."""
    if not os.path.isdir(led.folder):
        return UNAVAILABLE
    if led.rgb and state.intensity is not None:
        result = _write(os.path.join(led.folder, "multi_intensity"),
                        " ".join(str(v) for v in state.intensity))
        if result != OK:
            return result
    return _write(os.path.join(led.folder, "brightness"),
                  str(min(max(state.brightness, 0), led.max_brightness)))


def write_all(changes):
    """Write [(Led, State)]; stops at the first LED that is gone or may not
    be written, as the others would fail the same way. Returns OK or the
    first failure."""
    worst = OK
    for led, state in changes:
        result = write_state(led, state)
        if result in (UNAVAILABLE, DENIED):
            return result
        if result != OK:
            worst = result
    return worst


def all_off(leds):
    return write_all([(led, off(led)) for led in leds if led.writable])


def snapshot(leds):
    """{folder: State} of the LEDs whose state can be read back."""
    states = {}
    for led in leds:
        state = read_state(led)
        if state is not None:
            states[led.folder] = state
    return states


def restore(leds, states):
    """Set the LEDs back to states; LEDs that are gone meanwhile (unplugged,
    wheel taken off) are left out. Returns like write_all()."""
    return write_all([(led, states[led.folder]) for led in leds
                      if led.writable and led.folder in states
                      and os.path.isdir(led.folder)])


def sequence_steps(leds, color_names=None):
    """The short LED test: [[(Led, State)]], one list per step.

    All off; each group of rev LEDs lit one after another up to all; the
    left flags, then the right ones; every RGB LED in each colour of
    color_names (default: colors(leds)); all off again.
    """
    usable = [l for l in leds if l.writable]
    if not usable:
        return []
    everything_off = [(l, off(l)) for l in usable]
    steps = [everything_off]
    for location in (BASE, BOTH, RIM):
        group = [l for l in usable if l.kind == REV and l.location == location]
        for i in range(len(group)):
            steps.append([(l, lit(l)) for l in group[:i + 1]])
        if group:
            steps.append([(l, off(l)) for l in group])
    for kind in (LEFT_FLAG, RIGHT_FLAG):
        group = [l for l in usable if l.kind == kind]
        if group:
            steps.append([(l, lit(l)) for l in group])
            steps.append([(l, off(l)) for l in group])
    rgb = [l for l in usable if l.rgb]
    names = colors(rgb) if color_names is None else color_names
    for name in names if rgb else ():
        steps.append([(l, lit(l, name)) for l in rgb])
    if len(steps) > 1:
        steps.append(everything_off)
    return steps


class Sequence:
    """The test sequence, one step per advance(); the caller times it.

    The states before are read when it is made and set again by finish(),
    also after cancel() or an LED that went away.
    """

    def __init__(self, leds, color_names=None):
        self.leds = list(leds)
        self.steps = sequence_steps(self.leds, color_names)
        self.before = snapshot(self.leds)
        self.position = 0
        self.result = OK
        self.running = bool(self.steps)

    def advance(self):
        """Do the next step. Returns True while there are more."""
        if not self.running:
            return False
        result = write_all(self.steps[self.position])
        self.position += 1
        if result != OK:
            log.info("LED test stopped: %s", result)
            self.result = result
            self.running = False
        elif self.position >= len(self.steps):
            self.running = False
        return self.running

    def cancel(self):
        self.running = False

    def finish(self):
        """Stop and set the LEDs back as they were. Returns like restore()."""
        self.running = False
        return restore(self.leds, self.before)
