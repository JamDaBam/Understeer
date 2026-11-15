"""Whether a community preset was made on the same wheelbase model as the
connected one, without GTK.

Only the model is compared: the connected wheelbase is known by the USB
product id the driver puts in the name of its folder (device.model_id and
device.WHEEL_BASES), a preset by the name in its "wheelbase" field. The
steering wheel on it plays no part. A model match says nothing about
whether every value suits the wheelbase: variants of one model (e.g. with
another power supply) can differ, so nothing here claims compatibility.

Nothing is read from or written to the device: the product id comes from
the folder name alone.
"""
import re
import unicodedata
from collections import namedtuple

from . import device


SAME = "same"               # the preset names the model of the connected one
DIFFERENT = "different"     # it names another model Understeer knows
UNKNOWN = "unknown"         # anything else

# Why the result is UNKNOWN
NO_DEVICE = "no_device"             # no wheelbase connected
UNKNOWN_DEVICE = "unknown_device"   # connected, but not a model Understeer knows
NO_MODEL = "no_model"               # the preset names no wheelbase
UNKNOWN_MODEL = "unknown_model"     # it names one Understeer doesn't know

# model: the name from device.WHEEL_BASES or None; product: the USB
# product id or None; drive: "direct_drive" when known to be one, else None
Device = namedtuple("Device", "model product drive")
# level: SAME, DIFFERENT or UNKNOWN; reason: why UNKNOWN, else None;
# preset_model: the known model the preset names, or None; same_drive:
# True/False when both drives are known, else None
Result = namedtuple("Result", "level reason preset_model same_drive")


def _key(name):
    """A model name to compare: without case, accents, spaces and
    punctuation, e.g. "CSL-DD" and "csl dd" are the same."""
    name = unicodedata.normalize("NFKD", name).casefold()
    return re.sub(r"[\W_]", "", name)


_MODELS = {_key(name): name for name in device.WHEEL_BASES.values()}


def known_model(text):
    """The wheelbase name Understeer uses for text, if text names exactly
    one model it knows; else None (also for a part of a name, e.g. "CSL
    Elite", which fits more than one)."""
    if not text:
        return None
    return _MODELS.get(_key(text))


def connected(device_path):
    """The connected wheelbase as a Device, or None when none is."""
    if not device_path:
        return None
    try:
        product = int(device.model_id(device_path).partition(":")[2], 16)
    except ValueError:
        return Device(None, None, None)
    return Device(device.WHEEL_BASES.get(product), product,
                  "direct_drive" if product in device.DIRECT_DRIVE else None)


def classify(meta, wheelbase):
    """Result for a preset's metadata (library.Meta) and the connected
    wheelbase (a Device or None)."""
    if wheelbase is None:
        return Result(UNKNOWN, NO_DEVICE, known_model(meta.wheelbase), None)
    if wheelbase.model is None:
        return Result(UNKNOWN, UNKNOWN_DEVICE, known_model(meta.wheelbase), None)
    if not meta.wheelbase:
        return Result(UNKNOWN, NO_MODEL, None, None)
    model = known_model(meta.wheelbase)
    if model is None:
        return Result(UNKNOWN, UNKNOWN_MODEL, None, None)
    if model == wheelbase.model:
        return Result(SAME, None, model, None)
    same_drive = None
    if meta.drive and wheelbase.drive:
        same_drive = meta.drive == wheelbase.drive
    return Result(DIFFERENT, None, model, same_drive)
