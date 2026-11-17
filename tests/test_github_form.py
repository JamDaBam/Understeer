import copy
import json
import pathlib
import re
import subprocess
import sys
import urllib.parse

import pytest

from understeer import github_form, i18n, library, sharing, submission
from understeer.settings import SETTINGS

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRESETS_FORM = ROOT.parent / "Understeer-Presets" / ".github" / "ISSUE_TEMPLATE" \
    / "community-preset.yml"


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")


def prepared(name="GT3 Endurance", values=None, **meta):
    fields = dict(author="Alex", game="Assetto Corsa Competizione",
                  wheelbase="CSL DD", description="Calm GT3 feel.",
                  tags=["gt3", "endurance"])
    fields.update(meta)
    return submission.prepare(name, values or {"FF": 75, "SEN": 540}, **fields)


def query(url):
    """{parameter: value} of an address, decoded."""
    base, _, q = url.partition("?")
    assert base == github_form.FORM_URL
    pairs = urllib.parse.parse_qsl(q, keep_blank_values=True, strict_parsing=True)
    keys = [k for k, _v in pairs]
    assert len(keys) == len(set(keys))
    return dict(pairs)


# --- 1./2./3. the address and its fields ------------------------------------------

ENTRY_TEXT = """\
{
  "id": "assetto-corsa-competizione-gt3-endurance",
  "meta": {
    "author": "Alex",
    "game": "Assetto Corsa Competizione",
    "wheelbase": "CSL DD",
    "drive": "direct_drive",
    "description": "Calm GT3 feel.",
    "tags": [
      "gt3",
      "endurance"
    ]
  },
  "preset": {
    "understeer_preset": 1,
    "name": "GT3 Endurance",
    "values": {
      "SEN": 540,
      "FF": 75
    }
  }
}
"""


def test_address_fills_in_every_field():
    plan = github_form.for_submission(prepared())
    assert plan.left_out == ()
    assert query(plan.url) == {
        "template": "community-preset.yml",
        "title": "[Preset] GT3 Endurance",
        "preset-name": "GT3 Endurance",
        "game": "Assetto Corsa Competizione",
        "wheelbase": "CSL DD",
        "author": "Alex",
        "description": "Calm GT3 feel.",
        "tags": "gt3, endurance",
        "catalog-id": "assetto-corsa-competizione-gt3-endurance",
        "preset-json": ENTRY_TEXT}
    assert plan.url.index("template=") < plan.url.index("title=")


def test_field_ids_are_the_forms():
    assert github_form.FIELDS == ("title", "preset-name", "game", "wheelbase",
                                  "author", "description", "tags",
                                  "catalog-id", "preset-json")
    assert set(github_form.LEAVE_OUT) == set(github_form.FIELDS)


@pytest.mark.skipif(not PRESETS_FORM.exists(), reason="no Understeer-Presets checkout")
def test_field_ids_match_the_published_form():
    """Against the form in a checkout of the preset repository next to
    this one, read as text (it is not run)."""
    ids = re.findall(r"^    id: ([\w-]+)$", PRESETS_FORM.read_text(encoding="utf-8"),
                     re.MULTILINE)
    assert ids[:-1] == list(github_form.FIELDS[1:])
    assert ids[-1] == "acknowledgement"
    assert "template=community-preset.yml" in github_form.url({})


def _form_element(field_id):
    """The lines of one element of the published form, as text."""
    text = PRESETS_FORM.read_text(encoding="utf-8")
    start = text.index(f"\n    id: {field_id}\n")
    end = text.find("\n  - type:", start)
    return text[start:end if end != -1 else len(text)]


@pytest.mark.skipif(not PRESETS_FORM.exists(), reason="no Understeer-Presets checkout")
def test_the_published_form_takes_the_catalog_entry():
    """The field keeps its id, is still required and shown as JSON, and
    is named as share_dialog names it for copying."""
    element = _form_element("preset-json")
    assert "\n      label: Catalog Entry (JSON)\n" in element
    assert "\n      render: json\n" in element
    assert "\n      required: true\n" in element
    assert "type: textarea" in PRESETS_FORM.read_text(encoding="utf-8").split(
        "\n    id: preset-json\n")[0].rsplit("\n  - ", 1)[1]


def test_field_holds_the_complete_catalog_entry():
    result = prepared(values={"FOR": 100, "FF": 75, "SEN": 540})
    text = github_form.plan(result.entry).values["preset-json"]
    data = json.loads(text)
    assert data == result.entry
    assert list(data) == ["id", "meta", "preset"]
    assert data["preset"]["values"] == {"SEN": 540, "FF": 75, "FOR": 100}
    assert data["meta"]["drive"] == "direct_drive"
    assert "torque_nm" not in data["meta"]
    # Two spaces, as submission.entry_json writes it; no second copy of
    # the preset next to it
    assert text == submission.entry_json(result.entry)
    assert text.count('"understeer_preset"') == 1
    # What the catalog parser reads back, exactly as it was
    catalog = library.parse_catalog(json.dumps(
        {"understeer_library": 1, "presets": [data]}).encode())
    assert catalog.skipped == []
    (got,) = catalog.entries
    assert (got.id, got.name, got.values) == (
        result.entry["id"], "GT3 Endurance", {"SEN": 540, "FF": 75, "FOR": 100})
    assert got.unsupported == () and got.adjusted == ()


@pytest.mark.parametrize("wheelbase, drive", sorted(
    submission.WHEELBASE_DRIVES.items()))
def test_entry_has_the_drive_of_each_wheelbase(wheelbase, drive):
    plan = github_form.for_submission(prepared(wheelbase=wheelbase))
    meta = json.loads(plan.values["preset-json"])["meta"]
    assert (meta["wheelbase"], meta["drive"]) == (wheelbase, drive)
    assert "torque_nm" not in meta


def test_backticks_are_escaped_and_read_back_unchanged():
    """A ``` in a text can't end GitHub's code block around the field: in
    the JSON every ` is \\u0060, which reads back as the same text."""
    description = "Close the block ``` here\n```json\n{\"x\": 1}\n``` `a`"
    result = prepared(name="Fast `Lap`", description=description,
                      game="Game ` One", author="A`B")
    text = github_form.plan(result.entry).values["preset-json"]
    assert "`" not in text
    assert text.count("\\u0060") == description.count("`") + 2 + 1 + 1
    data = json.loads(text)
    assert data == result.entry
    assert data["meta"]["description"] == description
    assert data["preset"]["name"] == "Fast `Lap`"
    # Only the backticks differ from submission.entry_json
    assert text.replace("\\u0060", "`") == submission.entry_json(result.entry)
    # The readable fields keep their text as it is
    fields = github_form.plan(result.entry).values
    assert fields["description"] == description
    assert fields["preset-name"] == "Fast `Lap`"


def test_json_special_characters_survive_in_the_entry():
    text = 'Quote " backslash \\ slash / tab-free <b>&amp;</b> ${x} % #'
    result = prepared(description=text, name="Say \"Hi\" \\ 🏁")
    data = json.loads(github_form.plan(result.entry).values["preset-json"])
    assert data["meta"]["description"] == text
    assert data["preset"]["name"] == "Say \"Hi\" \\ 🏁"
    assert data == result.entry


def test_older_entries_without_drive_are_still_accepted():
    """Entries of the earlier format (no drive) stay valid for the form."""
    entry = copy.deepcopy(prepared().entry)
    del entry["meta"]["drive"]
    assert json.loads(github_form.plan(entry).values["preset-json"]) == entry


def test_every_setting_is_in_the_json():
    values = sharing.clean_values({s["id"]: (s["choices"][-1][0] if s.get("kind")
                                             == "choice" else s["hi"])
                                   for s in SETTINGS})
    plan = github_form.for_submission(prepared(values=values))
    assert json.loads(query(plan.url)["preset-json"])["preset"]["values"] == values


def test_no_acknowledgement_is_filled_in():
    """The boxes stay the user's: no parameter can tick them."""
    plan = github_form.for_submission(prepared())
    assert "acknowledgement" not in query(plan.url)
    assert not any("acknowledge" in k for k in query(plan.url))


# --- 4./5./6. text as it is --------------------------------------------------------

def test_unicode_multiline_and_special_characters_survive():
    text = ("Ä ö ü ß € 🏁 — „quotes“ \"double\" 'single'\n\n"
            "a&b=c?d#e%f+g/h\\i <b>tag</b> ${x} `y`")
    result = prepared(name="Nürburgring 🏁 & Co", game="F1® 24 #1 + 100%",
                      author="Jürgen", description=text, tags=["oel"])
    plan = github_form.for_submission(result)
    q = query(plan.url)
    assert q["description"] == text
    assert q["preset-name"] == "Nürburgring 🏁 & Co"
    assert q["title"] == "[Preset] Nürburgring 🏁 & Co"
    assert q["game"] == "F1® 24 #1 + 100%"
    entry = json.loads(q["preset-json"])
    assert entry == result.entry
    assert entry["preset"]["name"] == "Nürburgring 🏁 & Co"
    assert entry["meta"]["description"] == text
    # Only safe characters in the address itself
    assert re.fullmatch(r"[A-Za-z0-9%._~&=?:/-]+", plan.url)
    assert "\n" not in plan.url and " " not in plan.url


def test_address_is_the_same_for_the_same_submission():
    assert github_form.for_submission(prepared()).url == \
        github_form.for_submission(prepared()).url


# --- 7. length -----------------------------------------------------------------------

def test_typical_submission_fits():
    plan = github_form.for_submission(prepared(
        values={"SEN": 900, "FF": 70, "FOR": 100, "SPR": 0, "DPR": 0, "FFS": 1,
                "FEI": 30, "INT": 0, "SHO": 80},
        description="Calm GT3 feel for long stints, a bit softer than default."))
    assert plan.left_out == () and len(plan.url) <= github_form.MAX_URL_CHARS


BEAMNG_VALUES = {"SEN": 900, "FF": 50, "FOR": 100, "SPR": 100, "DPR": 100,
                 "FFS": 1, "FEI": 100, "INT": 3, "NDP": 15, "NFR": 0, "NIN": 0,
                 "SHO": 100, "brF": 90, "BLI": 101, "FUL": 0, "ACP": 1}


def test_the_maintainers_beamng_example_fits():
    """A real submission: 16 settings, a short description, one tag."""
    result = submission.prepare(
        "BeamNG - Allrounder", BEAMNG_VALUES, author="JamDaBam", game="BeamNG",
        wheelbase="CSL DD", description="Allrounder Preset for BeamNG",
        tags=["allround"])
    plan = github_form.for_submission(result)
    assert plan.left_out == () and len(plan.url) <= github_form.MAX_URL_CHARS
    data = json.loads(query(plan.url)["preset-json"])
    assert data == {
        "id": "beamng-beamng-allrounder",
        "meta": {"author": "JamDaBam", "game": "BeamNG", "wheelbase": "CSL DD",
                 "drive": "direct_drive",
                 "description": "Allrounder Preset for BeamNG",
                 "tags": ["allround"]},
        "preset": {"understeer_preset": 1, "name": "BeamNG - Allrounder",
                   "values": BEAMNG_VALUES}}
    assert list(data["preset"]["values"].items()) == list(BEAMNG_VALUES.items())


def test_long_description_leaves_out_the_entry_first():
    result = prepared(description=("Fahrgefühl für Übungsrunden " * 40)[:1000].strip())
    plan = github_form.for_submission(result)
    assert plan.left_out[0] == "preset-json"
    assert len(plan.url) <= github_form.MAX_URL_CHARS
    q = query(plan.url)
    for field in plan.left_out:
        assert field not in q
    for field in set(github_form.FIELDS) - set(plan.left_out):
        assert q[field] == plan.values[field]       # whole, never cut


def test_text_to_copy_is_the_complete_entry():
    """What the Copy button of a left-out field copies (plan.values): the
    whole entry, formatted, never cut, also with the longest texts."""
    values = sharing.clean_values({s["id"]: (s["choices"][-1][0] if s.get("kind")
                                             == "choice" else s["hi"])
                                   for s in SETTINGS})
    result = prepared(name="Ü" * 80, game="🏁" * 80, author="Ä" * 40,
                      description="\n".join(["Ö" * 49] * 20),
                      tags=[f"tag-{i}" for i in range(10)], values=values)
    plan = github_form.for_submission(result)
    assert "preset-json" in plan.left_out
    text = plan.values["preset-json"]
    assert text == submission.entry_json(result.entry)
    assert json.loads(text) == result.entry
    assert json.loads(text)["preset"]["values"] == values
    assert len(json.loads(text)["meta"]["description"]) == 20 * 49 + 19


@pytest.mark.parametrize("limit", [100, 300, 600, 900, 1200, 2000, 5000])
def test_limit_is_kept_and_nothing_is_cut(limit):
    result = prepared(description="Ä" * 500, name="N" * 80, game="G" * 80)
    plan = github_form.for_submission(result, limit)
    assert len(plan.url) <= max(limit, len(github_form.url({})))
    q = query(plan.url)
    assert set(q) - {"template"} == set(github_form.FIELDS) - set(plan.left_out)
    for field, value in q.items():
        if field != "template":
            assert value == plan.values[field]
    order = list(github_form.LEAVE_OUT)
    assert list(plan.left_out) == order[:len(plan.left_out)]


def test_exactly_at_the_limit():
    result = prepared()
    full = github_form.for_submission(result, 10_000).url
    assert github_form.for_submission(result, len(full)).left_out == ()
    assert github_form.for_submission(result, len(full) - 1).left_out == ("preset-json",)


def test_length_counts_the_encoded_address():
    result = prepared(description="€" * 100)        # 9 characters each, encoded
    url = github_form.for_submission(result, 10_000).url
    # Once in the description field, once in the catalog entry
    assert url.count("%E2%82%AC") == 200
    assert github_form.for_submission(result, len(url) - 1).left_out


def test_only_the_bare_form_when_nothing_fits():
    plan = github_form.for_submission(prepared(), 10)
    assert plan.url == github_form.url({})
    assert set(plan.left_out) == set(github_form.FIELDS)


# --- 12. no prepared submission ------------------------------------------------------

@pytest.mark.parametrize("broken", [
    None, "text", 1, [], {}, submission.Submission(None, ()),
    submission.Submission({"id": "x"}, ())])
def test_only_prepared_submissions(broken):
    with pytest.raises(ValueError):
        github_form.for_submission(broken)


def _entry_with(change):
    entry = copy.deepcopy(prepared().entry)
    change(entry)
    return entry


@pytest.mark.parametrize("change", [
    lambda e: e["preset"]["values"].update(SEN=905),            # adjusted
    lambda e: e["preset"]["values"].update(ZZZ=1),              # unknown
    lambda e: e["preset"]["values"].update(FF=float("nan")),
    lambda e: e["preset"]["values"].update(FF=101),
    lambda e: e["meta"].pop("author"),
    lambda e: e["meta"].update(description=""),
    lambda e: e.pop("meta"),
    lambda e: e.update(id="Not An Id"),
    lambda e: e["preset"].pop("values"),
])
def test_changed_entries_are_refused(change):
    with pytest.raises(ValueError):
        github_form.plan(_entry_with(change))


# --- 14./15./16. nothing else happens -------------------------------------------------

def test_submission_is_not_changed():
    result = prepared()
    before = copy.deepcopy(result.entry)
    github_form.for_submission(result, 100)
    assert result.entry == before


def test_building_the_address_touches_nothing():
    """In a fresh interpreter: no audit event at all (no open, socket,
    subprocess, ioctl, webbrowser, ...) and no device or GUI module."""
    code = """
import sys
from understeer import github_form, submission
events = []
sys.addaudithook(lambda event, args: events.append(event))
r = submission.prepare("GT3", {"FF": 70}, author="A", game="G",
                       wheelbase="CSL DD", description="D")
github_form.for_submission(r)
github_form.for_submission(r, 100)
assert events == [], sorted(set(events))
hardware = {"understeer.capabilities", "understeer.leds", "understeer.display",
            "understeer.inputs", "understeer.device_info", "webbrowser",
            "urllib.request", "http.client", "socket", "gi"}
assert not hardware & set(sys.modules), hardware & set(sys.modules)
"""
    run = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
