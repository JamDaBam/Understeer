"""The community preset library: its catalog format and how it is checked.

A catalog is one JSON file (docs/preset-library.md describes it in full):

    {"understeer_library": 1,
     "presets": [
       {"id": "acc-gt3-endurance",
        "meta": {"author": "…", "game": "…", "wheelbase": "CSL DD",
                 "drive": "direct_drive", "torque_nm": 8,
                 "description": "…", "tags": ["gt3"]},
        "preset": {"understeer_preset": 1, "name": "GT3 Endurance",
                   "values": {"SEN": 540, "FF": 75}}}]}

"preset" is exactly what Understeer exports for one preset (see sharing),
so the tuning values stay apart from the community metadata in "meta".

A catalog comes from the internet, so everything in it is untrusted: it is
only read as data, never run, never taken as a path, a URL or markup, and
nothing here writes to the wheelbase or to the user's presets. Errors are
in English for the log; the UI shows its own translated message.
"""
import hashlib
import json
import re
from collections import namedtuple

from . import sharing
from .names import one_line
from .settings import SETTINGS, SETTINGS_BY_ID

LIBRARY_FORMAT = 1            # "understeer_library" this version reads
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 500             # presets in one catalog
MAX_VALUES = 64               # settings in one preset, known or not
MAX_AUTHOR = 40
MAX_GAME = 80
MAX_WHEELBASE = 60
MAX_DESCRIPTION = 1000
MAX_DESCRIPTION_LINES = 20
MAX_TAGS = 10
MAX_TORQUE_NM = 40
DRIVES = ("belt", "direct_drive")

ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
TAG_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?")
SETTING_ID_RE = re.compile(r"[A-Za-z]{1,8}")   # also of settings unknown here

class Error(ValueError):
    """The whole catalog can't be used."""


class NewerFormat(Error):
    """The catalog is of a format only a newer Understeer reads."""


class _Skip(ValueError):
    """One entry can't be used; the message is its reason, for the log."""


Meta = namedtuple("Meta", "author game wheelbase drive torque_nm description tags")
# values: the settings this version knows, as the wheelbase keeps them;
# unsupported: ids of settings it doesn't know (e.g. from a newer version);
# adjusted: [(id, value shared, value kept)] where the value was fitted to
# the steps of its setting, as a local import does, for the preview to show
Entry = namedtuple("Entry", "id name values meta fingerprint unsupported adjusted")
# skipped: [(position in "presets", id or None, reason)]
Catalog = namedtuple("Catalog", "entries skipped")


def fingerprint(values):
    """A short hash of the values, the same for the same settings in any
    order; tells whether a local preset already has them."""
    canonical = json.dumps(sorted(values.items()), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _no_duplicates(pairs):
    keys = [k for k, _v in pairs]
    if len(keys) != len(set(keys)):
        raise Error("duplicate key in a JSON object")
    return dict(pairs)


def _no_constant(name):
    raise Error(f"{name} is not a number in JSON")


def _load(raw):
    if not isinstance(raw, (bytes, bytearray)):
        raise Error("the catalog must be bytes")
    if len(raw) > MAX_CATALOG_BYTES:
        raise Error("the catalog is too big")
    try:
        text = bytes(raw).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Error("the catalog is not UTF-8") from None
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates,
                          parse_constant=_no_constant)
    except json.JSONDecodeError as e:
        raise Error(f"the catalog is no valid JSON: {e.msg}") from None
    except RecursionError:
        raise Error("the catalog is nested too deeply") from None


def _text(meta, key, limit):
    value = meta.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Skip(f"meta.{key} is not text")
    return one_line(value, limit) or None


def _description(meta):
    value = meta.get("description")
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Skip("meta.description is not text")
    lines = [one_line(line, MAX_DESCRIPTION) for line in value.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    text = "\n".join(lines[:MAX_DESCRIPTION_LINES])
    return text[:MAX_DESCRIPTION].strip() or None


def _tags(meta):
    tags = meta.get("tags", [])
    if not isinstance(tags, list) or len(tags) > MAX_TAGS:
        raise _Skip("meta.tags is not a list of at most "
                    f"{MAX_TAGS} tags")
    clean = []
    for tag in tags:
        if not isinstance(tag, str) or not TAG_RE.fullmatch(tag.lower()):
            raise _Skip("meta.tags has a tag that is no short word of a-z, 0-9 and -")
        if tag.lower() not in clean:
            clean.append(tag.lower())
    return tuple(clean)


def _meta(meta):
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise _Skip("meta is not an object")
    drive = meta.get("drive")
    if drive is not None and drive not in DRIVES:
        raise _Skip("meta.drive is not one of " + ", ".join(DRIVES))
    torque = meta.get("torque_nm")
    if torque is not None and (isinstance(torque, bool)
                               or not isinstance(torque, int)
                               or not 1 <= torque <= MAX_TORQUE_NM):
        raise _Skip(f"meta.torque_nm is not a whole number of 1 to {MAX_TORQUE_NM}")
    return Meta(author=_text(meta, "author", MAX_AUTHOR),
                game=_text(meta, "game", MAX_GAME),
                wheelbase=_text(meta, "wheelbase", MAX_WHEELBASE),
                drive=drive, torque_nm=torque,
                description=_description(meta), tags=_tags(meta))


def _values(values):
    """(the known settings, ids of unknown ones, values fitted to the
    steps of their setting). The values are cleaned exactly as a local
    import does (sharing.clean_values). A known setting with a
    value it can't have makes the preset unusable: rather none than a
    preset that is different from what its author shared."""
    if not isinstance(values, dict) or not values:
        raise _Skip("preset.values is not an object with settings")
    if len(values) > MAX_VALUES:
        raise _Skip("preset.values has too many settings")
    known, unsupported, adjusted = {}, [], []
    for attr, value in values.items():
        if attr not in SETTINGS_BY_ID:
            if not SETTING_ID_RE.fullmatch(attr):
                raise _Skip("preset.values has a key that is no setting id")
            unsupported.append(attr)
            continue
        clean = sharing.clean_values({attr: value})
        if not clean:
            raise _Skip(f"preset.values.{attr} is out of range or no number")
        known.update(clean)
        if clean[attr] != value:
            adjusted.append((attr, value, clean[attr]))
    if not known:
        raise _Skip("the preset holds no setting this version knows")
    ordered = {s["id"]: known[s["id"]] for s in SETTINGS
               if s["id"] in known}
    order = list(ordered)
    adjusted.sort(key=lambda a: order.index(a[0]))
    return ordered, tuple(sorted(unsupported)), tuple(adjusted)


def _preset(preset):
    """(name, values, unsupported, adjusted) of the shared preset in an
    entry."""
    if not isinstance(preset, dict):
        raise _Skip("preset is not an object")
    version = preset.get("understeer_preset")
    if isinstance(version, bool) or not isinstance(version, int) \
            or not 1 <= version <= sharing.FORMAT:
        raise _Skip("preset.understeer_preset is missing or not supported")
    name = preset.get("name")
    if not isinstance(name, str) or not one_line(name, sharing.MAX_NAME):
        raise _Skip("preset.name is missing")
    return (sharing._name(preset, ""), *_values(preset.get("values")))


def _entry(data):
    if not isinstance(data, dict):
        raise _Skip("the entry is not an object")
    entry_id = data.get("id")
    if not isinstance(entry_id, str) or not ID_RE.fullmatch(entry_id):
        raise _Skip("id is not 1 to 64 characters of a-z, 0-9 and -")
    name, values, unsupported, adjusted = _preset(data.get("preset"))
    return Entry(id=entry_id, name=name, values=values,
                 meta=_meta(data.get("meta")),
                 fingerprint=fingerprint(values), unsupported=unsupported,
                 adjusted=adjusted)


def parse_catalog(raw):
    """The usable presets of a catalog (bytes as downloaded). Entries that
    break the rules are skipped, each with its reason; raises Error when
    the catalog as a whole is broken, too big or of a newer format."""
    data = _load(raw)
    if not isinstance(data, dict):
        raise Error("the catalog is not a JSON object")
    version = data.get("understeer_library")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise Error("this is no Understeer preset catalog")
    if version > LIBRARY_FORMAT:
        raise NewerFormat("the catalog needs a newer version of Understeer")
    presets = data.get("presets")
    if not isinstance(presets, list):
        raise Error("the catalog has no list of presets")
    if len(presets) > MAX_ENTRIES:
        raise Error("the catalog has too many presets")
    entries, skipped, seen = [], [], set()
    for pos, item in enumerate(presets):
        item_id = item.get("id") if isinstance(item, dict) else None
        item_id = item_id if isinstance(item_id, str) and \
            ID_RE.fullmatch(item_id) else None
        try:
            entry = _entry(item)
        except _Skip as e:
            skipped.append((pos, item_id, str(e)))
            continue
        if entry.id in seen:
            skipped.append((pos, entry.id, "the id is used already"))
            continue
        seen.add(entry.id)
        entries.append(entry)
    return Catalog(entries, skipped)
