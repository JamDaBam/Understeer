"""Live input (steering angle, pedal travel) read from /dev/input/event*."""
import fcntl
import glob
import os
import struct

from gi.repository import GLib

from .i18n import N_

EV_ABS = 3
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
    range), for the diagnostics. Both share the same Axis objects."""

    def __init__(self, on_change, on_gone=None):
        self.on_change = on_change
        self.on_gone = on_gone
        self.axes = {}          # key -> Axis
        self.all_axes = {}      # key -> Axis
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
        self.all_axes, self.devices = {}, []

    def reset_measurements(self):
        """Forget the observed ranges of all axes (diagnostics)."""
        for axis in self.all_axes.values():
            axis.reset_observed()

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
            if name in self.devices:
                self.devices.remove(name)
            if self.on_gone:
                self.on_gone()
            return GLib.SOURCE_REMOVE
        changed = False
        for _s, _us, etype, code, value in EVENT.iter_unpack(
                data[:len(data) - len(data) % EVENT.size]):
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
