"""What can be told about the connected wheelbase, without GTK.

Collects the device name, USB ids, models, firmware versions and the kernel
driver from sysfs, and formats them for a GitHub issue. It only reads.

Each item says why a value is missing:

    UNSUPPORTED  the installed hid-fanatecff doesn't offer it (no such file)
    UNAVAILABLE  nothing to show right now: no wheelbase, no steering
                 wheel on it, the driver isn't loaded or the file can't be read
    UNKNOWN      the driver gives something Understeer can't name or read,
                 e.g. a wheel id it doesn't know, or no version at all

Firmware versions are only what the driver reports; nothing is guessed.
Serial numbers (HID_UNIQ) and USB ports (HID_PHYS) are left out.
"""
import os
import platform
from dataclasses import dataclass

from . import capabilities, device
from . import log as session_log
from .report import sanitize

MODULE_DIR = "/sys/module/hid_fanatec"

VALUE = "value"
UNSUPPORTED = "unsupported"
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"

# Why a value is missing (note of an Item)
NOT_CONNECTED = "not_connected"   # no wheelbase found
NO_WHEEL = "no_wheel"             # no steering wheel reported
NOT_READABLE = "not_readable"     # file there, but can't be read
NOT_LOADED = "not_loaded"         # hid-fanatecff isn't loaded
NOT_LISTED = "not_listed"         # an id Understeer has no name for
NOT_REPORTED = "not_reported"     # the driver gives no version
INVALID = "invalid"               # something that isn't a number

# In this order, with their names for the copied report (not translated:
# the report goes to GitHub).
ITEMS = (
    ("device_name", "Device name"),
    ("vendor_id", "USB vendor ID"),
    ("product_id", "USB product ID"),
    ("wheelbase", "Wheelbase model"),
    ("wheelbase_firmware", "Wheelbase firmware"),
    ("rim", "Steering wheel model"),
    ("rim_firmware", "Steering wheel firmware"),
    ("driver", "Kernel driver"),
    ("driver_version", "Driver version"),
    ("driver_build", "Driver build (srcversion)"),
)

STATE_TEXT = {
    UNSUPPORTED: "not supported by the installed driver",
    UNAVAILABLE: "not available",
    UNKNOWN: "unknown",
}
NOTE_TEXT = {
    NOT_CONNECTED: "no wheelbase connected",
    NO_WHEEL: "no steering wheel reported",
    NOT_READABLE: "could not be read",
    NOT_LOADED: "hid-fanatecff not loaded",
    NOT_LISTED: "not in Understeer's list",
    NOT_REPORTED: "not reported by the driver",
    INVALID: "invalid value",
}


@dataclass(frozen=True)
class Item:
    """One piece of information: its value, or why there is none.

    value can be set for UNKNOWN too, e.g. the id of an unknown wheel.
    """
    state: str
    value: str | None = None
    note: str | None = None

    @property
    def known(self):
        return self.state == VALUE


def _read(path):
    """(text, None), or (None, why): UNSUPPORTED if there is no such file,
    NOT_READABLE if it can't be read."""
    try:
        with open(path) as f:
            return f.read().strip(), None
    except FileNotFoundError:
        return None, UNSUPPORTED
    except (OSError, UnicodeDecodeError):
        return None, NOT_READABLE


def _uevent(hid):
    """The keys of the HID device's uevent that are needed here."""
    text, _why = _read(os.path.join(hid, "uevent"))
    keys = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("HID_ID", "HID_NAME"):
            keys[key] = value.strip()
    return keys


def _ids(hid, device_path):
    """(vendor, product) as numbers, or (None, None)."""
    # HID_ID=0003:00000EB7:00000020; the folder name has them as well.
    parts = _uevent(hid).get("HID_ID", "").split(":")
    if len(parts) == 3:
        try:
            return int(parts[1], 16), int(parts[2], 16)
        except ValueError:
            pass
    vendor, _sep, product = device.model_id(device_path).partition(":")
    try:
        return int(vendor, 16), int(product, 16)
    except ValueError:
        return None, None


def _firmware(path):
    text, why = _read(path)
    if why == UNSUPPORTED:
        return Item(UNSUPPORTED)
    if why:
        return Item(UNAVAILABLE, note=why)
    try:
        return Item(VALUE, str(int(text)))
    except ValueError:
        return Item(UNKNOWN, note=INVALID)


def _rim(hid):
    """(Item for the model, rim id or None)."""
    has_rim_id = os.path.exists(os.path.join(hid, "rim_id"))
    if not has_rim_id and not os.path.exists(os.path.join(hid, "wheel_id")):
        return Item(UNSUPPORTED), None
    # rim_id in decimal, wheel_id of drivers before October 2026 as "0x0a"
    rim_id = capabilities._read_id(os.path.join(
        hid, "rim_id" if has_rim_id else "wheel_id"))
    if rim_id is None:
        return Item(UNAVAILABLE, note=NOT_READABLE), None
    if not rim_id:
        return Item(UNAVAILABLE, note=NO_WHEEL), None
    name = device.RIMS.get(rim_id)
    if name:
        return Item(VALUE, name), rim_id
    return Item(UNKNOWN, f"ID 0x{rim_id:02X}", NOT_LISTED), rim_id


def _driver(module_dir):
    """Items for driver, driver_version and driver_build."""
    if not os.path.isdir(module_dir):
        missing = Item(UNAVAILABLE, note=NOT_LOADED)
        return {"driver": missing, "driver_version": missing,
                "driver_build": missing}
    result = {"driver": Item(VALUE, os.path.basename(module_dir))}
    for key, name in (("driver_version", "version"),
                      ("driver_build", "srcversion")):
        text, why = _read(os.path.join(module_dir, name))
        if text:
            result[key] = Item(VALUE, text)
        elif why == NOT_READABLE:
            result[key] = Item(UNAVAILABLE, note=NOT_READABLE)
        else:
            # Builds from git have no version
            result[key] = Item(UNKNOWN, note=NOT_REPORTED)
    return result


def collect(device_path, module_dir=MODULE_DIR):
    """{key: Item} for every key of ITEMS, in that order.

    device_path is the tuning folder (device.find_device_path()); None or a
    folder that is gone counts as not connected.
    """
    info = {}
    hid = os.path.join(device_path, "device") if device_path else None
    if not hid or not os.path.isdir(hid):
        gone = Item(UNAVAILABLE, note=NOT_CONNECTED)
        info = {key: gone for key, _name in ITEMS}
    else:
        name = _uevent(hid).get("HID_NAME")
        info["device_name"] = (Item(VALUE, name) if name
                               else Item(UNKNOWN, note=NOT_READABLE))
        vendor, product = _ids(hid, device_path)
        if product is None:
            info["vendor_id"] = info["product_id"] = info["wheelbase"] = \
                Item(UNKNOWN, note=NOT_READABLE)
        else:
            info["vendor_id"] = Item(VALUE, f"0x{vendor:04X}")
            info["product_id"] = Item(VALUE, f"0x{product:04X}")
            base = device.WHEEL_BASES.get(product)
            info["wheelbase"] = (Item(VALUE, base) if base else
                                 Item(UNKNOWN, f"0x{vendor:04X}:0x{product:04X}",
                                      NOT_LISTED))
        info["wheelbase_firmware"] = _firmware(os.path.join(hid, "fw_version"))
        info["rim"], rim_id = _rim(hid)
        if rim_id:
            info["rim_firmware"] = _firmware(os.path.join(hid, "fw_version_rim"))
        elif info["rim"].state == UNSUPPORTED:
            info["rim_firmware"] = Item(UNSUPPORTED)
        else:
            info["rim_firmware"] = Item(UNAVAILABLE, note=NO_WHEEL)
    info.update(_driver(module_dir))
    return {key: info[key] for key, _name in ITEMS}


def item_text(item):
    """The value, or why there is none, in English."""
    if item.known:
        return item.value
    text = STATE_TEXT[item.state]
    details = [d for d in (item.value, NOTE_TEXT.get(item.note)) if d]
    return f"{text} ({', '.join(details)})" if details else text


def report_lines(info, skip=("driver", "driver_version", "driver_build")):
    """[(name, text)] for the system details of a problem report; skip
    leaves out what they tell already (the driver, its version and build)."""
    return [(name, item_text(info[key])) for key, name in ITEMS
            if key not in skip]


def _cell(text):
    """Text that can't break the Markdown table."""
    return text.replace("|", "\\|").replace("\n", " ")


def report_text(info, caps=None, version=None, kernel=None):
    """The information as Markdown for a GitHub issue."""
    lines = ["### Device information", "", "| | |", "|---|---|"]
    lines += [f"| {name} | {_cell(item_text(info[key]))} |"
              for key, name in ITEMS]
    lines += ["", f"- Understeer: {version or session_log.version()}",
              f"- Linux kernel: {kernel or platform.release()}"]
    if caps is not None and caps.wheelbase:
        lines.append(f"- Hardware functions: {capabilities.summary(caps)}")
    return sanitize("\n".join(lines))
