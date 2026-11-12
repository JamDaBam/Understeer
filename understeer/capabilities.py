"""Which hardware functions the connected devices offer, without GTK.

hid-fanatecff creates its sysfs files depending on the wheelbase, the wheel
on it, the pedals and its own version, so this looks at the files that are
there instead of at model lists. It only reads; nothing is written.

A function found means the driver offers it. Whether and how the hardware
reacts can't be seen there: the driver accepts LED values silently (a
Podium Steering Wheel DD GT gets RPM1..9, but only RPM2, RPM5 and RPM8
light one of its three lamps).

Where the driver puts them (see its hid-ftec.c, hid-ftecff.c,
hid-ftecff-tuning.c and hid-ftecff-wheel.c), with HID the folder of the HID
device, e.g. /sys/bus/hid/devices/0003:0EB7:0020.0005:

    advanced_mode   /sys/class/ftec_tuning/HID/advanced_mode
    display         HID/display (write only)
    base_rev_leds   /sys/class/leds/HID::RPM<n>/brightness
    rim_rev_leds    /sys/class/leds/HID:<rim id>:RPM<n>/brightness,
                    or HID:<rim id>:RGB:RPM<n> on wheels with RGB LEDs
    rgb_leds        .../multi_intensity of those RGB LEDs
    flag_leds       /sys/class/leds/HID:<rim id>:RGB:RFLAG<n> and LFLAG<n>
    pedal_load      HID/load of pedals connected by USB
    pedal_rumble    HID/rumble of ClubSport Pedals V3 (USB) or of a CSL
                    Elite wheelbase for the pedals on it (write only)

Drivers before October 2026 differ: the wheel is in HID/wheel_id ("0x0a")
instead of HID/rim_id ("10"), there is no ftec_wheel folder, and every
wheelbase gets HID::RPM1..9. Those light the rev LEDs of the wheel on it,
on a CSL Elite its own as well.

Run  python3 -m understeer.capabilities  to see what was found.
"""
import glob
import logging
import os
import re
from dataclasses import dataclass, field

from . import device

HID_GLOB = "/sys/bus/hid/devices/*"
LEDS_GLOB = "/sys/class/leds/*"
WHEEL_GLOB = "/sys/class/ftec_wheel/*"

ADVANCED_MODE = "advanced_mode"   # standard or advanced tuning menu
DISPLAY = "display"               # text on the display
BASE_REV_LEDS = "base_rev_leds"   # rev LEDs on the wheelbase
RIM_REV_LEDS = "rim_rev_leds"     # rev LEDs on the steering wheel
RGB_LEDS = "rgb_leds"             # LEDs of the steering wheel with a colour
FLAG_LEDS = "flag_leds"           # flag LEDs left and right of its display
PEDAL_LOAD = "pedal_load"         # load cell setting of USB pedals
PEDAL_RUMBLE = "pedal_rumble"     # rumble motors in the pedals
# Wheelbases with rev LEDs of their own (FTEC_WHEELBASE_LEDS in hid-ftec.c):
# CSL Elite and CSL Elite for PS4. Only needed for drivers before October
# 2026, whose files don't tell it.
OLD_DRIVER_BASE_LEDS = {0x0E03, 0x0005}

FEATURES = (ADVANCED_MODE, DISPLAY, BASE_REV_LEDS, RIM_REV_LEDS, RGB_LEDS,
            FLAG_LEDS, PEDAL_LOAD, PEDAL_RUMBLE)

log = logging.getLogger("understeer.capabilities")


@dataclass(frozen=True)
class Feature:
    """A hardware function and the sysfs files that control it.

    readable and writable tell whether this user may read or write every one
    of them; a file the driver made write only (display, rumble) is never
    readable, one it made read only never writable.
    """
    paths: tuple
    readable: bool
    writable: bool


@dataclass(frozen=True)
class Capabilities:
    """What detect() found. Compare two to notice a change."""
    tuning: str | None = None      # tuning folder, as device.find_device_path()
    wheelbase: str | None = None   # HID device folder of the wheelbase
    rim_id: int | None = None      # wheel on it, None if none was reported
    old_driver: bool = False       # hid-fanatecff before October 2026 (wheel_id)
    rim: str | None = None         # its ftec_wheel folder, once the driver knows it
    pedals: tuple = ()             # HID folders of other devices with load or rumble
    features: dict = field(default_factory=dict)   # name -> Feature

    def get(self, name):
        """The Feature, or None if it isn't available."""
        return self.features.get(name)

    def has(self, name):
        return name in self.features

    def can_read(self, name):
        f = self.features.get(name)
        return bool(f and f.readable)

    def can_write(self, name):
        f = self.features.get(name)
        return bool(f and f.writable)


def _access(path):
    """(readable, writable) for this user, or None if path is no file."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path):
        return None
    # The mode says what the driver allows (also for root), os.access what
    # this user may do.
    return (bool(st.st_mode & 0o444) and os.access(path, os.R_OK),
            bool(st.st_mode & 0o222) and os.access(path, os.W_OK))


def _feature(paths):
    """A Feature of the files that exist among paths, or None if none does."""
    found = [(p, a) for p in paths if (a := _access(p))]
    if not found:
        return None
    return Feature(paths=tuple(p for p, _a in found),
                   readable=all(a[0] for _p, a in found),
                   writable=all(a[1] for _p, a in found))


def _usb_device(hid):
    """The USB device a HID device folder belongs to.

    A wheelbase or pedals can show up as several HID devices: one per USB
    interface, and after plugging in again for a moment the old one too.
    They all sit below the same USB device (HID -> interface -> device).
    """
    return os.path.dirname(os.path.dirname(os.path.realpath(hid)))


def _registration(hid):
    """Number the kernel gave the HID device; higher is newer."""
    try:
        return int(os.path.basename(hid).rpartition(".")[2], 16)
    except ValueError:
        return -1


def _one_per_device(hids):
    """The newest HID folder of each USB device, in a stable order, with
    the links in /sys/bus/hid/devices resolved."""
    newest = {}
    for hid in map(os.path.realpath, hids):
        key = _usb_device(hid)
        if key not in newest or _registration(hid) > _registration(newest[key]):
            newest[key] = hid
    return sorted(newest.values())


def _with_file(name):
    """HID device folders that have the file name, one per USB device."""
    pattern = os.path.join(HID_GLOB, name)
    return _one_per_device(os.path.dirname(p) for p in glob.glob(pattern)
                           if os.path.isfile(p))


def _wheelbase(tuning):
    """HID device folder of the wheelbase.

    The tuning folder links to it; wheelbases without a tuning menu (e.g.
    ClubSport V2) are found by "range", which the driver gives every wheelbase.
    """
    if tuning:
        hid = os.path.realpath(os.path.join(tuning, "device"))
        if os.path.isdir(hid):
            return hid
    found = _with_file("range")
    return found[-1] if found else None


_LED_NUMBER = re.compile(r"(\d+)$")


def _leds(pattern):
    """LED folders whose name matches pattern, sorted by their number."""
    leds = [p for p in glob.glob(LEDS_GLOB)
            if pattern.fullmatch(os.path.basename(p))]

    def order(p):
        name = os.path.basename(p)
        m = _LED_NUMBER.search(name)
        return (name.rstrip("0123456789"), int(m[1]) if m else 0)
    return sorted(leds, key=order)


def detect():
    """Look at sysfs and return the Capabilities. Never raises for missing
    devices, files or permissions; a device unplugged meanwhile simply
    shows fewer functions."""
    try:
        return _detect()
    except OSError as e:
        log.warning("Could not look at the hardware functions: %s", e)
        return Capabilities()


def _read_id(path):
    """A number the driver writes in decimal or, like wheel_id, as "0x0a"."""
    try:
        with open(path) as f:
            return int(f.read().strip(), 0)
    except (OSError, ValueError):
        return None


def _detect():
    features = {}
    old_driver = False
    tuning = device.find_device_path()
    hid = _wheelbase(tuning)
    name = os.path.basename(hid) if hid else None

    if tuning:
        features[ADVANCED_MODE] = _feature([os.path.join(tuning, "advanced_mode")])
    rim_id = rim = None
    if hid:
        features[DISPLAY] = _feature([os.path.join(hid, DISPLAY)])
        prefix = re.escape(name)
        base = _leds(re.compile(prefix + r"::RPM\d+"))

        rim_id = _read_id(os.path.join(hid, "rim_id"))
        if rim_id is None and os.path.isfile(os.path.join(hid, "wheel_id")):
            old_driver = True
            rim_id = _read_id(os.path.join(hid, "wheel_id"))
        rim_id = rim_id or None         # 0: no wheel reported yet
        folder = os.path.join(os.path.dirname(WHEEL_GLOB), name)
        rim = folder if os.path.isdir(folder) else None
        # The driver names them after the wheel on it, so a wheel taken off
        # takes its LEDs along.
        rim_leds = _leds(re.compile(
            prefix + r":[0-9A-Fa-f]{2}:(?:RGB:)?(?:RPM|RFLAG|LFLAG)\d+"))
        rgb = [p for p in rim_leds
               if os.path.isfile(os.path.join(p, "multi_intensity"))]
        rev = [p for p in rim_leds if ":RPM" in os.path.basename(p)]
        if old_driver:
            rev = base
            try:
                product = int(device.model_id(hid).partition(":")[2], 16)
            except ValueError:
                product = None
            if product not in OLD_DRIVER_BASE_LEDS:
                base = []
        features[BASE_REV_LEDS] = _feature(
            [os.path.join(p, "brightness") for p in base])
        flags = [p for p in rim_leds if "FLAG" in os.path.basename(p)]
        features[RIM_REV_LEDS] = _feature(
            [os.path.join(p, "brightness") for p in rev])
        features[RGB_LEDS] = _feature(
            [os.path.join(p, "multi_intensity") for p in rgb])
        features[FLAG_LEDS] = _feature(
            [os.path.join(p, "brightness") for p in flags])

    load = _with_file("load")
    rumble = _one_per_device(os.path.dirname(p)
                             for p in glob.glob(device.RUMBLE_GLOB)
                             if os.path.isfile(p))
    features[PEDAL_LOAD] = _feature([os.path.join(p, "load") for p in load])
    features[PEDAL_RUMBLE] = _feature([os.path.join(p, "rumble") for p in rumble])
    base_usb = _usb_device(hid) if hid else None
    pedals = tuple(sorted({p for p in load + rumble
                           if _usb_device(p) != base_usb}))

    return Capabilities(
        tuning=tuning, wheelbase=hid, rim_id=rim_id, old_driver=old_driver,
        rim=rim, pedals=pedals,
        features={k: v for k, v in features.items() if v is not None})


def _access_text(f):
    if f is None:
        return "-"
    return ("r" if f.readable else "") + ("w" if f.writable else "") or "no access"


def summary(caps):
    """One line for the log and problem reports, without paths, e.g.
    "advanced_mode rw, display w, rim_rev_leds rw (9), ..." (r = readable,
    w = writable, - = not available, a number for several files)."""
    def one(name):
        f = caps.get(name)
        count = f" ({len(f.paths)})" if f and len(f.paths) > 1 else ""
        return f"{name} {_access_text(f)}{count}"
    return ", ".join(one(name) for name in FEATURES)


def driver_text(caps):
    """Which kind of hid-fanatecff the files show."""
    if caps.old_driver:
        return "before October 2026 (wheel_id)"
    return "October 2026 or newer (rim_id)" if caps.wheelbase else "-"


def describe(caps):
    """Several lines for looking at what was found by hand."""
    lines = [
        f"Tuning folder:  {caps.tuning or 'none'}",
        f"Wheelbase:      {caps.wheelbase or 'none'}",
        "Steering wheel: " + (
            f"id {caps.rim_id} (0x{caps.rim_id:02x}, "
            f"{device.RIMS.get(caps.rim_id, 'unknown')})" if caps.rim_id
            else "none") + (f", {caps.rim}" if caps.rim else ""),
        f"Driver:         {driver_text(caps)}",
        f"Pedals:         {', '.join(caps.pedals) or 'none'}",
        "",
        "Function        Access  Files   (r = readable, w = writable, - = not available)",
    ]
    for name in FEATURES:
        f = caps.get(name)
        files = len(f.paths) if f else 0
        lines.append(f"{name:<15} {_access_text(f):<7} {files}")
        for p in (f.paths if f else ()):
            lines.append(f"    {p}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe(detect()))
