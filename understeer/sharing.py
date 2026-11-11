"""Sharing a single preset: as a file, as JSON or as readable text.

The readable text lists the values and ends with the JSON in a code block,
so it reads well in a chat or forum and Understeer can read it back. Both
the JSON and the text (also with words around it) are understood when
importing.
"""
import json
import re

from .i18n import _
from .settings import SETTINGS, SETTINGS_BY_ID, format_value

FORMAT = 1                  # "understeer_preset" in shared presets
MAX_NAME = 80               # longer names are cut
MAX_SIZE = 256 * 1024       # bigger files and texts are no presets

_BY_ID = SETTINGS_BY_ID
_CODE_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


class Error(ValueError):
    """Not a preset; the message can be shown to the user."""


def _data(name, values):
    ordered = {s["id"]: values[s["id"]] for s in SETTINGS if s["id"] in values}
    return {"understeer_preset": FORMAT, "name": name, "values": ordered}


def preset_json(name, values):
    """The preset as JSON, the same as an exported preset file."""
    return json.dumps(_data(name, values), indent=2, ensure_ascii=False) + "\n"


def preset_text(name, values):
    """The values to read, then the JSON in a code block."""
    lines = [_("Understeer preset “{name}”").format(name=name)]
    for setting in SETTINGS:
        if setting["id"] in values:
            lines.append(f"{_(setting['label'])}: "
                         f"{format_value(setting, values[setting['id']])}")
    data = json.dumps(_data(name, values), ensure_ascii=False)
    return "\n".join(lines) + f"\n\n```json\n{data}\n```\n"


def _valid(attr_id, value):
    setting = _BY_ID.get(attr_id)
    if setting is None or isinstance(value, bool) or not isinstance(value, int):
        return False
    if setting.get("kind") == "choice":
        return value in dict(setting["choices"])
    if attr_id == "SEN" and value == 0:
        return True                         # Auto
    return setting["lo"] <= value <= setting["hi"]


def clean_values(values):
    """The known settings with valid values; the rest is left out."""
    return {a: v for a, v in values.items() if _valid(a, v)}


def _candidates(text):
    yield text.strip()
    yield from (m.group(1) for m in _CODE_BLOCK.finditer(text))
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        yield text[start:end + 1]


def parse(text, fallback_name=""):
    """(name, values) of a shared preset, from JSON or from the text of
    preset_text(), also with words around it. Raises Error."""
    if len(text) > MAX_SIZE:
        raise Error(_("This is too long to be a preset."))
    for candidate in _candidates(text):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("values"), dict):
            break
    else:
        raise Error(_("No preset found. Copy the whole text of a shared "
                      "preset, or its JSON."))
    values = clean_values(data["values"])
    if not values:
        raise Error(_("The preset holds no settings Understeer knows."))
    name = data.get("name")
    if not isinstance(name, str):
        name = ""
    name = "".join(c for c in " ".join(name.split()) if c.isprintable())
    name = name[:MAX_NAME].strip() or fallback_name or _("Imported Preset")
    return name, values


def free_name(name, taken):
    """name, or "name (2)", "name (3)", … if taken(name) is true."""
    if not taken(name):
        return name
    n = 2
    while taken(f"{name} ({n})"):
        n += 1
    return f"{name} ({n})"
