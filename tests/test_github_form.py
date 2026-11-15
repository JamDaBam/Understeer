import copy
import json
import pathlib
import re
import subprocess
import sys
import urllib.parse

import pytest

from understeer import github_form, i18n, sharing, submission
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
        "preset-json": '{\n  "understeer_preset": 1,\n  "name": "GT3 Endurance",\n'
                       '  "values": {\n    "SEN": 540,\n    "FF": 75\n  }\n}'}
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


def test_preset_json_is_only_the_preset():
    result = prepared(values={"FOR": 100, "FF": 75, "SEN": 540})
    text = github_form.plan(result.entry).values["preset-json"]
    data = json.loads(text)
    assert data == {"understeer_preset": 1, "name": "GT3 Endurance",
                    "values": {"SEN": 540, "FF": 75, "FOR": 100}}
    assert list(data) == ["understeer_preset", "name", "values"]
    assert not {"id", "meta", "preset"} & set(data)
    # What Understeer itself reads back as a shared preset
    found = sharing.find_presets(text).presets
    assert [(f.name, f.values) for f in found] == [
        ("GT3 Endurance", {"SEN": 540, "FF": 75, "FOR": 100})]


def test_every_setting_is_in_the_json():
    values = sharing.clean_values({s["id"]: (s["choices"][-1][0] if s.get("kind")
                                             == "choice" else s["hi"])
                                   for s in SETTINGS})
    plan = github_form.for_submission(prepared(values=values))
    assert json.loads(query(plan.url)["preset-json"])["values"] == values


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
    assert json.loads(q["preset-json"])["name"] == "Nürburgring 🏁 & Co"
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


def test_long_description_leaves_out_the_json_first():
    result = prepared(description=("Fahrgefühl für Übungsrunden " * 40)[:1000].strip())
    plan = github_form.for_submission(result)
    assert plan.left_out[0] == "preset-json"
    assert len(plan.url) <= github_form.MAX_URL_CHARS
    q = query(plan.url)
    for field in plan.left_out:
        assert field not in q
    for field in set(github_form.FIELDS) - set(plan.left_out):
        assert q[field] == plan.values[field]       # whole, never cut


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
    assert url.count("%E2%82%AC") == 100
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
