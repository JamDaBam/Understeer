"""The address that opens the community preset submission form on GitHub
with a prepared submission filled in.

The form (.github/ISSUE_TEMPLATE/community-preset.yml in the preset
repository) takes its fields from URL parameters named after their ids.
Only the public submission goes into the address: the fields the user
entered and checked, the proposed id and the preset itself, never anything
else. Nothing here opens a browser or the network; it only builds text.

GitHub doesn't say how long such an address may be. Filling in the form
was tested with an address of about 830 characters, which is no general
limit of GitHub or of browsers. So an address is kept to MAX_URL_CHARS;
when everything doesn't fit, whole fields are left out, the preset JSON
first, and the user copies them into the form. Nothing is ever cut short.
"""
import json
import urllib.parse
from collections import namedtuple

from . import library, submission

FORM_URL = "https://github.com/JamDaBam/Understeer-Presets/issues/new"
TEMPLATE = "community-preset.yml"
# Kept well below what is known to cause trouble ("414 URI Too Long"),
# and close to the tested length: see the module description
MAX_URL_CHARS = 2000
TITLE_PREFIX = "[Preset] "
# The form's field ids, in its order; "title" is the issue's title
FIELDS = ("title", "preset-name", "game", "wheelbase", "author",
          "description", "tags", "catalog-id", "preset-json")
# Left out in this order while the address is too long
LEAVE_OUT = ("preset-json", "description", "tags", "game", "preset-name",
             "author", "catalog-id", "wheelbase", "title")

# url: the address to open; values: every field's text, {id: text};
# left_out: ids of the fields not in the address, for the user to copy
Plan = namedtuple("Plan", "url values left_out")


def preset_json(entry):
    """The form's "Preset JSON": only the preset, as Understeer copies it
    with Share → Copy as JSON, never the whole catalog entry."""
    return json.dumps(entry["preset"], indent=2, ensure_ascii=False,
                      allow_nan=False)


def _checked(entry):
    """entry, if it is a prepared submission the catalog parser takes as
    it is; else raises ValueError. Guards against a broken or changed
    entry ever reaching the form."""
    try:
        raw = json.dumps({"understeer_library": library.LIBRARY_FORMAT,
                          "presets": [entry]}, allow_nan=False).encode()
        catalog = library.parse_catalog(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"not a prepared submission: {e}") from None
    if catalog.skipped or len(catalog.entries) != 1:
        raise ValueError("not a prepared submission")
    got = catalog.entries[0]
    meta = entry.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("not a prepared submission")
    if got.unsupported or got.adjusted or got.values != entry["preset"]["values"] \
            or any(not meta.get(k) for k in ("author", "game", "wheelbase",
                                             "description")):
        raise ValueError("not a prepared submission")
    return entry


def values(entry):
    """{field id: text} of a prepared submission (submission.prepare())."""
    meta = _checked(entry)["meta"]
    name = entry["preset"]["name"]
    return {"title": TITLE_PREFIX + name, "preset-name": name,
            "game": meta["game"], "wheelbase": meta["wheelbase"],
            "author": meta["author"], "description": meta["description"],
            "tags": ", ".join(meta.get("tags", ())), "catalog-id": entry["id"],
            "preset-json": preset_json(entry)}


def url(fields):
    """The form's address with fields ({id: text}) filled in; empty ones
    are left out."""
    query = [("template", TEMPLATE)] + [(k, fields[k]) for k in FIELDS
                                        if fields.get(k)]
    return FORM_URL + "?" + urllib.parse.urlencode(
        query, quote_via=urllib.parse.quote, safe="")


def plan(entry, limit=MAX_URL_CHARS):
    """The Plan for a prepared submission: as many fields in the address
    as fit into limit characters, whole; the others for the user to copy.
    Raises ValueError for anything but a prepared submission."""
    fields = values(entry)
    shown = {k: v for k, v in fields.items() if v}
    left_out = []
    for field in LEAVE_OUT:
        if len(url(shown)) <= limit:
            break
        if field in shown:
            del shown[field]
            left_out.append(field)
    return Plan(url(shown), fields, tuple(left_out))


def for_submission(result, limit=MAX_URL_CHARS):
    """plan() of a submission.Submission; ValueError for anything else."""
    if not isinstance(result, submission.Submission) \
            or not isinstance(result.entry, dict):
        raise ValueError("not a prepared submission")
    return plan(result.entry, limit)
