"""Live input (steering angle, pedal travel) read from /dev/input/event*."""
import fcntl
import glob
import os
import struct

from gi.repository import GLib

from .i18n import N_

EV_KEY = 1
EV_ABS = 3
KEY_MAX = 0x2ff
BTN_JOYSTICK = 0x120
EVIOCGKEY = 0x18        # ioctl nr: the keys currently held down
ABS_X = 0
ABS_MAX = 0x3f
ABS_HAT0X = 0x10        # 0x10+ are hats (d-pad) and others, not pedals
ABS_NAMES = {0: "X", 1: "Y", 2: "Z", 3: "RX", 4: "RY", 5: "RZ",
             6: "Throttle", 7: "Rudder", 8: "Wheel", 9: "Gas", 10: "Brake",
             16: "Hat0X", 17: "Hat0Y", 18: "Hat1X", 19: "Hat1Y",
             20: "Hat2X", 21: "Hat2Y", 22: "Hat3X", 23: "Hat3Y",
             24: "Pressure", 25: "Distance", 26: "TiltX", 27: "TiltY",
             28: "ToolWidth", 32: "Volume"}
EVENT = struct.Struct("llHHi")      # struct input_event on 64-bit systems
ABSINFO = struct.Struct("6i")       # struct input_absinfo
FULL_RANGE_TOLERANCE = 0.01         # share of the range counted as its end
FANATEC_VENDOR = "0eb7"

AXIS_ROLES = [
    ("", N_("Not assigned")),
    ("throttle", N_("Throttle")),
    ("brake", N_("Brake")),
    ("clutch", N_("Clutch")),
    ("handbrake", N_("Handbrake")),
    ("hidden", N_("Hidden")),
]


def _ioc_read(nr, size):
    return (2 << 30) | (size << 16) | (ord("E") << 8) | nr


def find_input_devices():
    """Return [(devnode, name)] for all Fanatec evdev nodes."""
    result = []
    for ev in sorted(glob.glob("/sys/class/input/event*")):
        try:
            with open(os.path.join(ev, "device/id/vendor")) as f:
                vendor = f.read().strip()
            with open(os.path.join(ev, "device/name")) as f:
                name = f.read().strip()
        except OSError:
            continue
        if vendor.lower() == FANATEC_VENDOR:
            result.append(("/dev/input/" + os.path.basename(ev), name))
    return result


# Kernel names (ABS_*) of codes without a short name above
KERNEL_NAMES = {0x21: "ABS_PROFILE", 0x28: "ABS_MISC"}


def kernel_name(code):
    """The kernel's name of an absolute axis, e.g. ABS_THROTTLE: generic,
    it says nothing about what the axis is on a Fanatec device."""
    if code in KERNEL_NAMES:
        return KERNEL_NAMES[code]
    if code in ABS_NAMES:
        return "ABS_" + ABS_NAMES[code].upper()
    return f"ABS_{code:#04x}"


def _key_names():
    """Kernel names of the key and button codes a wheel, a button box or
    pedals may report (input-event-codes.h)."""
    names = {0x3b + i: f"KEY_F{i + 1}" for i in range(10)}
    names.update({0x57: "KEY_F11", 0x58: "KEY_F12"})
    names.update({0x1d2 + i: f"KEY_FN_F{i + 1}" for i in range(12)})
    names.update({0x100 + i: f"BTN_{i}" for i in range(10)})
    for first, group in (
            (0x110, "LEFT RIGHT MIDDLE SIDE EXTRA FORWARD BACK TASK"),
            (0x120, "TRIGGER THUMB THUMB2 TOP TOP2 PINKIE BASE BASE2 BASE3 "
                    "BASE4 BASE5 BASE6"),
            (0x130, "SOUTH EAST C NORTH WEST Z TL TR TL2 TR2 SELECT START "
                    "MODE THUMBL THUMBR"),
            (0x150, "GEAR_DOWN GEAR_UP"),
            (0x220, "DPAD_UP DPAD_DOWN DPAD_LEFT DPAD_RIGHT GRIPL GRIPR "
                    "GRIPL2 GRIPR2")):
        names.update({first + i: "BTN_" + n
                      for i, n in enumerate(group.split())})
    names[0x12f] = "BTN_DEAD"
    names.update({0x290 + i: f"KEY_MACRO{i + 1}" for i in range(30)})
    names.update({0x2c0 + i: f"BTN_TRIGGER_HAPPY{i + 1}" for i in range(40)})
    return names


KEY_NAMES = _key_names()


def key_name(code):
    """The kernel's name of a key or button code, e.g. BTN_SOUTH; generic,
    it says nothing about where the button is on the device."""
    if code in KEY_NAMES:
        return KEY_NAMES[code]
    return f"{'BTN' if code >= 0x100 else 'KEY'}_{code:#05x}"


def sdl_order(code):
    """Sort key for the buttons of a device in the order SDL numbers them
    (and with it most games on Linux and Proton): BTN_JOYSTICK and up
    first, the codes below after them, each by code."""
    return (code < BTN_JOYSTICK, code)


def bits_set(data):
    """The numbers of the bits set in an evdev bit mask (little endian)."""
    mask = int.from_bytes(bytes(data), "little")
    return [i for i in range(mask.bit_length()) if mask >> i & 1]


class Button:
    """A key or button of an input device, for the button test: whether it
    is held down now and how often it was pressed since the last reset."""

    def __init__(self, device, code, pressed=False):
        self.device, self.code = device, code
        self.pressed = pressed
        self.presses = 0

    def update(self, value):
        """An EV_KEY event: 1 pressed, 0 released, 2 held (auto repeat,
        not a new press). True if it was a new press."""
        new = value == 1 and not self.pressed
        if new:
            self.presses += 1
        if value in (0, 1):
            self.pressed = value == 1
        return new

    def reset(self):
        """Untested again; a button held down stays shown as held."""
        self.presses = 0

    @property
    def tested(self):
        return self.presses > 0

    @property
    def key(self):
        return f"{self.device}:key:{self.code}"

    @property
    def name(self):
        return key_name(self.code)


def parse_absinfo(data):
    """struct input_absinfo as a dict: value, minimum, maximum, fuzz,
    flat, resolution."""
    return dict(zip(("value", "minimum", "maximum", "fuzz", "flat",
                     "resolution"), ABSINFO.unpack(bytes(data))))


class Axis:
    def __init__(self, device, code, lo, hi, value, fuzz=0, flat=0,
                 resolution=0):
        self.device, self.code = device, code
        self.lo, self.hi, self.value = lo, hi, value
        # Reported by the device (EVIOCGABS), for the diagnostics only
        self.fuzz, self.flat, self.resolution = fuzz, flat, resolution
        # Some pedals rest at the maximum value; treat the end nearest to
        # the first reading as "released".
        self.inverted = abs(value - hi) < abs(value - lo)
        self.peak = 0.0
        self.reset_observed()

    def update(self, value):
        """A new value from the device."""
        self.value = value
        self.observed_min = min(self.observed_min, value)
        self.observed_max = max(self.observed_max, value)

    def reset_observed(self):
        """Start the observed range anew from the current value."""
        self.observed_min = self.observed_max = self.value

    def normalized(self):
        """Raw value 0..1 within the reported range, not inverted."""
        span = (self.hi - self.lo) or 1
        return min(max((self.value - self.lo) / span, 0.0), 1.0)

    def coverage(self):
        """Share 0..1 of the reported range observed since the reset."""
        if self.hi <= self.lo:
            return 0.0
        seen = (min(self.observed_max, self.hi) - max(self.observed_min, self.lo))
        return min(max(seen / (self.hi - self.lo), 0.0), 1.0)

    def reached_min(self):
        tolerance = (self.hi - self.lo) * FULL_RANGE_TOLERANCE
        return self.observed_min <= self.lo + tolerance

    def reached_max(self):
        tolerance = (self.hi - self.lo) * FULL_RANGE_TOLERANCE
        return self.observed_max >= self.hi - tolerance

    def moved(self):
        return self.observed_max > self.observed_min

    @property
    def key(self):
        return f"{self.device}:{self.code}"

    @property
    def name(self):
        return ABS_NAMES.get(self.code, str(self.code))

    def fraction(self):
        """Pedal travel 0..1 from the released position."""
        span = (self.hi - self.lo) or 1
        f = (self.value - self.lo) / span
        return min(max(1 - f if self.inverted else f, 0.0), 1.0)

    def centered(self):
        """Steering position -1..1 around the centre."""
        span = (self.hi - self.lo) or 1
        return min(max((self.value - self.lo) / span * 2 - 1, -1.0), 1.0)


class InputMonitor:
    """Watches Fanatec evdev nodes and reports absolute axis changes;
    on_gone() is called when a device was unplugged and its axes are gone.

    axes holds the steering and pedal axes of the live display; all_axes
    every absolute axis the devices report (also hats and axes without a
    range), for the diagnostics. Both share the same Axis objects.
    buttons holds every key and button the devices report, for the button
    test; they are only read, never remapped."""

    def __init__(self, on_change, on_gone=None):
        self.on_change = on_change
        self.on_gone = on_gone
        self.axes = {}          # key -> Axis
        self.all_axes = {}      # key -> Axis
        self.buttons = {}       # key -> Button
        self.last_button = None  # key of the button pressed last
        self.devices = []       # names of the opened devices, in order
        self.denied = []        # devnodes without read permission
        self._open = []         # (fd, watch id)
        self._keys = {}         # fd -> keys of its axes

    def start(self):
        self.stop()
        for devnode, name in find_input_devices():
            try:
                fd = os.open(devnode, os.O_RDONLY | os.O_NONBLOCK)
            except PermissionError:
                self.denied.append(devnode)
                continue
            except OSError:
                continue
            try:
                self._read_axes(fd, name)
            except OSError:
                os.close(fd)
                continue
            try:
                self._read_buttons(fd, name)
            except OSError:
                pass            # the axes still work without the buttons
            watch = GLib.unix_fd_add_full(
                GLib.PRIORITY_DEFAULT, fd,
                GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
                self._on_readable, name)
            self._open.append((fd, watch))
            self.devices.append(name)

    def stop(self):
        for fd, watch in self._open:
            GLib.source_remove(watch)
            os.close(fd)
        self._open, self.axes, self.denied, self._keys = [], {}, [], {}
        self.all_axes, self.devices, self.buttons = {}, [], {}
        self.last_button = None

    def reset_measurements(self):
        """Forget the observed ranges of all axes (diagnostics)."""
        for axis in self.all_axes.values():
            axis.reset_observed()

    def reset_buttons(self):
        """Start the button test anew: no button counts as tested."""
        for button in self.buttons.values():
            button.reset()
        self.last_button = None

    def _read_buttons(self, fd, name):
        """The keys and buttons the device reports and which are held now."""
        size = KEY_MAX // 8 + 1
        bits = bytearray(size)
        fcntl.ioctl(fd, _ioc_read(0x20 + EV_KEY, size), bits, True)
        held = bytearray(size)
        try:
            fcntl.ioctl(fd, _ioc_read(EVIOCGKEY, size), held, True)
        except OSError:
            pass            # all shown as released until the first event
        down = set(bits_set(held))
        for code in bits_set(bits):
            button = Button(name, code, code in down)
            self.buttons[button.key] = button
            self._keys.setdefault(fd, []).append(button.key)

    def _read_axes(self, fd, name):
        bits = bytearray(8)
        fcntl.ioctl(fd, _ioc_read(0x20 + EV_ABS, len(bits)), bits, True)
        mask = int.from_bytes(bits, "little")
        for code in range(ABS_MAX + 1):
            if not mask & (1 << code):
                continue
            info = bytearray(ABSINFO.size)
            fcntl.ioctl(fd, _ioc_read(0x40 + code, ABSINFO.size), info, True)
            i = parse_absinfo(info)
            axis = Axis(name, code, i["minimum"], i["maximum"], i["value"],
                        i["fuzz"], i["flat"], i["resolution"])
            self.all_axes[axis.key] = axis
            self._keys.setdefault(fd, []).append(axis.key)
            # The live display: no hats, only axes with a range
            if code < ABS_HAT0X and axis.hi > axis.lo:
                self.axes[axis.key] = axis

    def _on_readable(self, fd, condition, name):
        try:
            data = os.read(fd, EVENT.size * 64)
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError:
            data = b""
        if not data:            # device unplugged
            self._open = [(f, w) for f, w in self._open if f != fd]
            os.close(fd)
            for key in self._keys.pop(fd, []):
                self.axes.pop(key, None)
                self.all_axes.pop(key, None)
                self.buttons.pop(key, None)
                if key == self.last_button:
                    self.last_button = None
            if name in self.devices:
                self.devices.remove(name)
            if self.on_gone:
                self.on_gone()
            return GLib.SOURCE_REMOVE
        changed = False
        for _s, _us, etype, code, value in EVENT.iter_unpack(
                data[:len(data) - len(data) % EVENT.size]):
            if etype == EV_KEY:
                button = self.buttons.get(f"{name}:key:{code}")
                if button:
                    if button.update(value):
                        self.last_button = button.key
                    changed = True
                continue
            key = f"{name}:{code}"
            axis = self.all_axes.get(key) if etype == EV_ABS else None
            if axis:
                axis.update(value)
                if key in self.axes:
                    axis.peak = max(axis.peak, axis.fraction())
                changed = True
        if changed:
            self.on_change()
        return GLib.SOURCE_CONTINUE

    def steering_axis(self):
        for axis in self.axes.values():
            if axis.code == ABS_X and "pedal" not in axis.device.lower():
                return axis
        return None
