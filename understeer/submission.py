"""Preparing one local preset as a submission to the community library.

The result is one catalog entry, exactly as it would stand in the
community catalog.json (docs/preset-library.md): its id, the community
metadata in "meta" and the preset as Understeer exports it in "preset".
Nothing here reads files, the network or a device; everything comes in as
arguments, and the local preset passed in is never changed.

Every text is the user's input and untrusted. It is checked, not cut or
quietly changed: a submission must hold exactly what its author sees, so
a text that is too long, holds control characters, a web address, an
e-mail address or a file path is refused with a reason, as are settings
Understeer doesn't know and values it would store differently. Only
spacing is tidied (runs of spaces, spaces at the ends of lines). Finally
the entry is read back with the catalog parser (library.parse_catalog),
so a prepared submission is one the library accepts as it is.

Problems name the field and a code; the UI turns them into its own
translated text.
"""
import json
import re
import unicodedata
from collections import namedtuple

from . import library, sharing
from .device import DIRECT_DRIVE, WHEEL_BASES
from .settings import SETTINGS, SETTINGS_BY_ID

MAX_ENTRY_BYTES = 16 * 1024     # far above what the field limits allow
REQUIRED = ("name", "game", "wheelbase", "author", "description")
# The names Understeer shows for the wheelbases it knows, and their drive
WHEELBASE_DRIVES = {name: "direct_drive" if pid in DIRECT_DRIVE else "belt"
                    for pid, name in WHEEL_BASES.items()}

# Rules of public submissions, also in the preset repository's check
URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://|www\.", re.IGNORECASE)
# Personal data that has no place in a public catalog
EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[a-z]{2,}", re.IGNORECASE)
PATH_RE = re.compile(r"(?:^|[\s\"'(=])~?/[^\s/]+/|\b[A-Za-z]:\\|\\\\[^\s\\]+\\")
# Looks like a serial number or machine id: one word of 12 or more letters
# and digits, at least 4 of them digits
CODE_RE = re.compile(r"\b(?=(?:[A-Za-z]*\d){4})[A-Za-z0-9]{12,}\b")

# field: "name", "game", "wheelbase", "author", "description", "tags",
# "drive", "torque_nm", "values", "id" or "entry"; code: what is wrong;
# detail: a setting id, a value or a limit, for the message
Problem = namedtuple("Problem", "field code detail", defaults=(None,))
# entry: the catalog entry (a dict, see entry_json); warnings: problems
# the user should look at once more, which don't stop the submission
Submission = namedtuple("Submission", "entry warnings")


class Invalid(ValueError):
    """The preset can't be submitted as it is; problems says why."""

    def __init__(self, problems):
        super().__init__(", ".join(f"{p.field}: {p.code}" for p in problems))
        self.problems = tuple(problems)


def _text(value, field, limit, problems, max_lines=1):
    """value with tidied spacing, or None after adding its problems."""
    if value is None or (isinstance(value, str) and not value.strip()):
        problems.append(Problem(field, "required"))
        return None
    if not isinstance(value, str):
        problems.append(Problem(field, "not_text"))
        return None
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if max_lines == 1 and len([ln for ln in lines if ln.strip()]) > 1:
        problems.append(Problem(field, "line_breaks"))
        return None
    lines = [" ".join(line.split()) for line in lines]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    text = "\n".join(lines)
    if any(not c.isprintable() for line in lines for c in line):
        problems.append(Problem(field, "control_characters"))
        return None
    if len(lines) > max_lines:
        problems.append(Problem(field, "too_many_lines", max_lines))
        return None
    if len(text) > limit:
        problems.append(Problem(field, "too_long", limit))
        return None
    return text


def _private(field, text, private_words, problems, warnings):
    """Refuse web, e-mail and file addresses; warn about words the
    caller knows are private (e.g. the login name) and serial-like codes."""
    if URL_RE.search(text):
        problems.append(Problem(field, "web_address"))
    elif EMAIL_RE.search(text):
        problems.append(Problem(field, "email_address"))
    elif PATH_RE.search(text):
        problems.append(Problem(field, "file_path"))
    folded = text.casefold()
    for word in private_words:
        if isinstance(word, str) and len(word.strip()) >= 3 \
                and word.strip().casefold() in folded:
            warnings.append(Problem(field, "private_word"))
            break
    if CODE_RE.search(text):
        warnings.append(Problem(field, "looks_like_serial"))


def _values(values, problems):
    """The settings in Understeer's order, unchanged, or None. Each value
    must be one the wheelbase keeps exactly as it is (sharing.clean_values,
    as for every import); nothing is left out or fitted silently."""
    if not isinstance(values, dict) or not values:
        problems.append(Problem("values", "no_settings"))
        return None
    if len(values) > library.MAX_VALUES:
        problems.append(Problem("values", "too_many_settings", library.MAX_VALUES))
        return None
    found = len(problems)
    for attr, value in values.items():
        if not isinstance(attr, str) or attr not in SETTINGS_BY_ID:
            problems.append(Problem("values", "unsupported_setting",
                                    str(attr)[:16]))
        elif isinstance(value, bool) or not isinstance(value, int):
            problems.append(Problem("values", "invalid_value", attr))
        else:
            kept = sharing.clean_values({attr: value}).get(attr)
            if kept is None:
                problems.append(Problem("values", "invalid_value", attr))
            elif kept != value:
                problems.append(Problem("values", "adjusted_value",
                                        (attr, value, kept)))
    if len(problems) > found:
        return None
    return {s["id"]: values[s["id"]] for s in SETTINGS if s["id"] in values}


def _slug(text):
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.decode().lower()).strip("-")


def _fit(slug, limit=64):
    if len(slug) > limit:
        slug = slug[:limit]
        slug = slug[:slug.rfind("-")] if "-" in slug else slug
    return slug.strip("-")


def suggest_id(game, name, author="", values=None, taken=()):
    """A proposal for the entry's id: "<game>-<name>" in a-z, 0-9 and -,
    with "-<author>", then "-2", "-3", ... added while taken. Maintainers
    may still choose another id when they add it; once published, it
    stays. Without any usable letters (e.g. a name in another script):
    "preset-" and the fingerprint of the values."""
    base = _fit(_slug(f"{game} {name}"))
    if not base or not library.ID_RE.fullmatch(base):
        base = "preset-" + library.fingerprint(values or {})[:12]
    candidates = [base]
    if _slug(author):
        candidates.append(_fit(f"{_fit(base, 40)}-{_slug(author)}"))
    for candidate in candidates:
        if library.ID_RE.fullmatch(candidate) and candidate not in taken:
            return candidate
    n = 2
    while f"{_fit(candidates[-1], 58)}-{n}" in taken:
        n += 1
    return f"{_fit(candidates[-1], 58)}-{n}"


def entry_json(entry):
    """The entry as text, always the same for the same entry: as it would
    stand in catalog.json, and to put into a submission."""
    return json.dumps(entry, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _read_back(entry, values):
    """Whether the catalog parser takes the entry exactly as it is."""
    raw = json.dumps({"understeer_library": library.LIBRARY_FORMAT,
                      "presets": [entry]}, ensure_ascii=False,
                     allow_nan=False).encode()
    catalog = library.parse_catalog(raw)
    if catalog.skipped or len(catalog.entries) != 1:
        return False
    got, meta = catalog.entries[0], entry["meta"]
    return (got.id == entry["id"] and got.name == entry["preset"]["name"]
            and got.values == values and not got.unsupported
            and not got.adjusted
            and got.meta == library.Meta(
                author=meta["author"], game=meta["game"],
                wheelbase=meta["wheelbase"], drive=meta.get("drive"),
                torque_nm=meta.get("torque_nm"),
                description=meta["description"],
                tags=tuple(meta.get("tags", ()))))


def prepare(name, values, *, author, game, wheelbase, description, tags=(),
            drive=None, torque_nm=None, entry_id=None, taken_ids=(),
            private_words=()):
    """The Submission of the local preset (name, values), or raises
    Invalid with every problem found.

    author must be what the user typed or confirmed; nothing here fills it
    in. wheelbase is one of the names Understeer shows (WHEELBASE_DRIVES).
    entry_id: the id to use, else suggest_id() avoiding taken_ids (the ids
    of the catalog). private_words: words the caller knows are private,
    such as the login name, to warn about; they aren't put anywhere."""
    problems, warnings = [], []
    clean = {
        "name": _text(name, "name", sharing.MAX_NAME, problems),
        "game": _text(game, "game", library.MAX_GAME, problems),
        "author": _text(author, "author", library.MAX_AUTHOR, problems),
        "description": _text(description, "description",
                             library.MAX_DESCRIPTION, problems,
                             library.MAX_DESCRIPTION_LINES),
    }
    if clean["name"] and len(clean["name"].encode()) > sharing.MAX_NAME_BYTES:
        problems.append(Problem("name", "too_long", sharing.MAX_NAME_BYTES))
        clean["name"] = None
    for field in ("name", "game", "author", "description"):
        if clean[field]:
            _private(field, clean[field], private_words, problems, warnings)

    if wheelbase is None or (isinstance(wheelbase, str) and not wheelbase.strip()):
        problems.append(Problem("wheelbase", "required"))
    elif not isinstance(wheelbase, str) or wheelbase not in WHEELBASE_DRIVES:
        problems.append(Problem("wheelbase", "unknown_wheelbase"))
    if drive is not None:
        if drive not in library.DRIVES:
            problems.append(Problem("drive", "invalid"))
        elif isinstance(wheelbase, str) \
                and WHEELBASE_DRIVES.get(wheelbase, drive) != drive:
            problems.append(Problem("drive", "not_this_wheelbase"))
    if torque_nm is not None and (isinstance(torque_nm, bool)
                                  or not isinstance(torque_nm, int)
                                  or not 1 <= torque_nm <= library.MAX_TORQUE_NM):
        problems.append(Problem("torque_nm", "invalid", library.MAX_TORQUE_NM))

    clean_tags = []
    if isinstance(tags, str) or not isinstance(tags, (list, tuple)):
        problems.append(Problem("tags", "invalid"))
    elif len(tags) > library.MAX_TAGS:
        problems.append(Problem("tags", "too_many", library.MAX_TAGS))
    else:
        for tag in tags:
            if not isinstance(tag, str) or not library.TAG_RE.fullmatch(tag.lower()):
                problems.append(Problem("tags", "invalid"))
                break
            if tag.lower() not in clean_tags:
                clean_tags.append(tag.lower())

    ordered = _values(values, problems)

    if entry_id is not None and (not isinstance(entry_id, str)
                                 or not library.ID_RE.fullmatch(entry_id)):
        problems.append(Problem("id", "invalid"))
    elif entry_id is not None and entry_id in taken_ids:
        problems.append(Problem("id", "taken"))
    if problems:
        raise Invalid(problems)

    meta = {"author": clean["author"], "game": clean["game"],
            "wheelbase": wheelbase}
    if drive is not None:
        meta["drive"] = drive
    if torque_nm is not None:
        meta["torque_nm"] = torque_nm
    meta["description"] = clean["description"]
    if clean_tags:
        meta["tags"] = clean_tags
    entry = {"id": entry_id or suggest_id(clean["game"], clean["name"],
                                          clean["author"], ordered, taken_ids),
             "meta": meta,
             "preset": {"understeer_preset": sharing.FORMAT,
                        "name": clean["name"], "values": ordered}}
    if len(entry_json(entry).encode()) > MAX_ENTRY_BYTES:
        raise Invalid([Problem("entry", "too_big", MAX_ENTRY_BYTES)])
    if not _read_back(entry, ordered):
        raise Invalid([Problem("entry", "not_accepted")])
    return Submission(entry, tuple(warnings))
