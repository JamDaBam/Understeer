"""Sharing a single preset: as a file, as JSON or as readable text.

The readable text lists the values and ends with the JSON in a code block,
on one line, so it reads well in a chat or forum, also when quoted, and
Understeer can read it back. Importing finds every preset in a text, also
many of them among other words, e.g. in a copied forum thread.
"""
import json
import re
from collections import namedtuple

from .config import clean_folder
from .i18n import _
from .settings import SETTINGS, SETTINGS_BY_ID, format_value

FORMAT = 1                  # "understeer_preset" in shared presets
MAX_NAME = 80               # longer names are cut
MAX_NAME_BYTES = 200        # also in UTF-8, for the file name
MAX_SIZE = 1024 * 1024      # bigger files and texts are no presets
MAX_PRESETS = 50            # taken from one text at most
SCAN_BUDGET = 8 * MAX_SIZE  # characters read while looking for presets
SCAN_WINDOW = 64 * 1024     # read at most this far for one JSON object

_BY_ID = SETTINGS_BY_ID
_OBJECT_START = re.compile(r'\{\s*["}]')     # where a JSON object can begin


class Error(ValueError):
    """Not a preset; the message can be shown to the user."""


def _data(name, values, folder=None):
    ordered = {s["id"]: values[s["id"]] for s in SETTINGS if s["id"] in values}
    data = {"understeer_preset": FORMAT, "name": name}
    if clean_folder(folder):
        data["folder"] = clean_folder(folder)
    data["values"] = ordered
    return data


def preset_json(name, values, folder=None):
    """The preset as JSON, the same as an exported preset file."""
    return json.dumps(_data(name, values, folder), indent=2,
                      ensure_ascii=False) + "\n"


def preset_text(name, values, folder=None):
    """The values to read, then the JSON in a code block."""
    lines = [_("Understeer preset “{name}”").format(name=name)]
    for setting in SETTINGS:
        if setting["id"] in values:
            lines.append(f"{_(setting['label'])}: "
                         f"{format_value(setting, values[setting['id']])}")
    data = json.dumps(_data(name, values, folder), ensure_ascii=False)
    return "\n".join(lines) + f"\n\n```json\n{data}\n```\n"


def _valid(attr_id, value):
    setting = _BY_ID.get(attr_id)
    if setting is None or isinstance(value, bool) or not isinstance(value, int):
        return False
    if setting.get("kind") == "choice":
        return value in dict(setting["choices"])
    if setting.get("auto") and value == 0:
        return True                         # Auto
    return setting["lo"] <= value <= setting["hi"]


def clean_values(values):
    """The known settings with valid values; the rest is left out."""
    return {a: v for a, v in values.items() if _valid(a, v)}


# folder: the folder it was in when shared, or None
Found = namedtuple("Found", "name values version folder", defaults=(None,))
Result = namedtuple("Result", "presets skipped cut")


def _preset_dicts(data, depth=0):
    """Dicts with a "values" dict, also inside lists and other dicts."""
    if depth > 4:
        return
    if isinstance(data, dict):
        if isinstance(data.get("values"), dict):
            yield data
            return
        data = data.values()
    elif not isinstance(data, list):
        return
    for item in data:
        yield from _preset_dicts(item, depth + 1)


def _json_objects(text):
    """Every JSON object in text, also among other words. Each try reads a
    window of the text only, and all tries together a limited amount, so
    text with thousands of broken brackets doesn't take long."""
    decoder = json.JSONDecoder()
    budget = SCAN_BUDGET
    start = _OBJECT_START.search(text)
    while start and budget > 0:
        pos = start.start()
        window = text[pos:pos + SCAN_WINDOW]
        try:
            data, end = decoder.raw_decode(window)
        except json.JSONDecodeError as e:
            budget -= max(e.pos, 1) + 64
            start = _OBJECT_START.search(text, pos + 1)
            continue
        except RecursionError:
            budget -= SCAN_WINDOW
            start = _OBJECT_START.search(text, pos + 1)
            continue
        budget -= end + 64
        yield data
        start = _OBJECT_START.search(text, pos + end)


def _name(data, fallback_name):
    name = data.get("name")
    if not isinstance(name, str):
        name = ""
    name = "".join(c for c in " ".join(name.split()) if c.isprintable())
    name = name[:MAX_NAME]
    while len(name.encode()) > MAX_NAME_BYTES:     # a file is named after it
        name = name[:-1]
    return name.strip() or fallback_name or _("Imported Preset")


def find_presets(text, fallback_name=""):
    """All shared presets in text: JSON objects, lists of them, or the text
    of preset_text(), also many of them among other words, as in a forum
    thread. Equal ones count once; a name with other values again gets a
    higher version. Raises Error when there is none."""
    if len(text) > MAX_SIZE:
        raise Error(_("This is too long to hold presets."))
    presets, seen, versions = [], set(), {}
    skipped = cut = 0
    for data in _json_objects(text):
        for preset in _preset_dicts(data):
            values = clean_values(preset["values"])
            if not values:
                skipped += 1
                continue
            name = _name(preset, fallback_name)
            key = (name, tuple(sorted(values.items())))
            if key in seen:
                continue
            seen.add(key)
            if len(presets) >= MAX_PRESETS:
                cut += 1
                continue
            versions[name] = versions.get(name, 0) + 1
            presets.append(Found(name, values, versions[name],
                                 clean_folder(preset.get("folder"))))
    if not presets:
        if skipped:
            raise Error(_("The presets hold no settings Understeer knows."))
        raise Error(_("No preset found. Copy the whole text of a shared "
                      "preset, or its JSON."))
    return Result(presets, skipped, cut)


def free_name(name, taken):
    """name, or "name (2)", "name (3)", … if taken(name) is true."""
    if not taken(name):
        return name
    n = 2
    while taken(f"{name} ({n})"):
        n += 1
    return f"{name} ({n})"
