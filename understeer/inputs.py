"""Live input (steering angle, pedal travel) read from /dev/input/event*."""
import fcntl
import glob
import os
import struct

from gi.repository import GLib

from .i18n import N_

EV_ABS = 3
ABS_X = 0
ABS_NAMES = {0: "X", 1: "Y", 2: "Z", 3: "RX", 4: "RY", 5: "RZ",
             6: "Throttle", 7: "Rudder", 8: "Wheel", 9: "Gas", 10: "Brake"}
EVENT = struct.Struct("llHHi")      # struct input_event on 64-bit systems
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


class Axis:
    def __init__(self, device, code, lo, hi, value):
        self.device, self.code = device, code
        self.lo, self.hi, self.value = lo, hi, value
        # Some pedals rest at the maximum value; treat the end nearest to
        # the first reading as "released".
        self.inverted = abs(value - hi) < abs(value - lo)
        self.peak = 0.0

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
    on_gone() is called when a device was unplugged and its axes are gone."""

    def __init__(self, on_change, on_gone=None):
        self.on_change = on_change
        self.on_gone = on_gone
        self.axes = {}          # key -> Axis
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

    def stop(self):
        for fd, watch in self._open:
            GLib.source_remove(watch)
            os.close(fd)
        self._open, self.axes, self.denied, self._keys = [], {}, [], {}

    def _read_axes(self, fd, name):
        bits = bytearray(8)
        fcntl.ioctl(fd, _ioc_read(0x20 + EV_ABS, len(bits)), bits, True)
        mask = int.from_bytes(bits, "little")
        for code in range(0x10):        # 0x10+ are hats (d-pad), skip them
            if not mask & (1 << code):
                continue
            info = bytearray(24)
            fcntl.ioctl(fd, _ioc_read(0x40 + code, 24), info, True)
            value, lo, hi = struct.unpack("6i", info)[:3]
            if hi > lo:
                axis = Axis(name, code, lo, hi, value)
                self.axes[axis.key] = axis
                self._keys.setdefault(fd, []).append(axis.key)

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
            if self.on_gone:
                self.on_gone()
            return GLib.SOURCE_REMOVE
        changed = False
        for _s, _us, etype, code, value in EVENT.iter_unpack(
                data[:len(data) - len(data) % EVENT.size]):
            axis = self.axes.get(f"{name}:{code}") if etype == EV_ABS else None
            if axis:
                axis.value = value
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
