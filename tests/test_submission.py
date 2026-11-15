import copy
import json
import math
import pathlib

import pytest

from understeer import i18n, library, sharing, submission
from understeer.submission import Invalid, Problem, prepare

ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")


VALUES = {"FF": 75, "SEN": 540, "FOR": 100}
META = dict(author="Alex", game="Assetto Corsa Competizione",
            wheelbase="CSL DD", description="Calm GT3 feel for long stints.")


DEFAULT = object()


def submit(name="GT3 Endurance", values=DEFAULT, **changes):
    meta = dict(META, **changes)
    return prepare(name, VALUES if values is DEFAULT else values, **meta)


def problems(name="GT3 Endurance", values=DEFAULT, **changes):
    with pytest.raises(Invalid) as e:
        submit(name, values, **changes)
    return e.value.problems


def read_back(entry):
    raw = json.dumps({"understeer_library": 1, "presets": [entry]}).encode()
    return library.parse_catalog(raw)


# --- 1. a valid local preset --------------------------------------------------

def test_valid_preset_becomes_a_catalog_entry():
    result = submit(tags=["GT3", "endurance", "gt3"], drive="direct_drive",
                    torque_nm=8)
    assert result.warnings == ()
    assert result.entry == {
        "id": "assetto-corsa-competizione-gt3-endurance",
        "meta": {"author": "Alex", "game": "Assetto Corsa Competizione",
                 "wheelbase": "CSL DD", "drive": "direct_drive",
                 "torque_nm": 8,
                 "description": "Calm GT3 feel for long stints.",
                 "tags": ["gt3", "endurance"]},
        "preset": {"understeer_preset": 1, "name": "GT3 Endurance",
                   "values": {"SEN": 540, "FF": 75, "FOR": 100}}}


def test_only_spacing_is_tidied():
    entry = submit("  GT3   Endurance ", game=" Assetto\tCorsa ",
                   description="\n First  line \r\n\r\nSecond line\n\n").entry
    assert entry["preset"]["name"] == "GT3 Endurance"
    assert entry["meta"]["game"] == "Assetto Corsa"
    assert entry["meta"]["description"] == "First line\n\nSecond line"


def test_every_known_setting_can_be_submitted():
    values = {s["id"]: (s["choices"][0][0] if s.get("kind") == "choice"
                        else s["lo"]) for s in submission.SETTINGS}
    values = sharing.clean_values(values)
    assert submit(values=values).entry["preset"]["values"] == values


# --- 2. missing metadata ------------------------------------------------------

@pytest.mark.parametrize("field", ["game", "wheelbase", "author", "description"])
@pytest.mark.parametrize("value", [None, "", "   \n "])
def test_required_metadata(field, value):
    assert Problem(field, "required") in problems(**{field: value})


def test_name_is_required():
    assert problems(name=" ") == (Problem("name", "required"),)


def test_all_problems_are_reported_at_once():
    found = problems(name="", author=None, game="", wheelbase="", description="",
                     values={"FF": 101})
    assert {p.field for p in found} == {"name", "author", "game", "wheelbase",
                                        "description", "values"}


# --- 3. author and game -------------------------------------------------------

@pytest.mark.parametrize("field, limit", [("author", library.MAX_AUTHOR),
                                          ("game", library.MAX_GAME)])
def test_long_author_or_game_is_refused_not_cut(field, limit):
    assert submit(**{field: "x" * limit}).entry["meta"][field] == "x" * limit
    assert problems(**{field: "x" * (limit + 1)}) == (Problem(field, "too_long", limit),)


@pytest.mark.parametrize("field", ["author", "game"])
@pytest.mark.parametrize("value, code", [
    ("Alex\nSmith", "line_breaks"), ("Al\x00ex", "control_characters"),
    ("Al\u202eex", "control_characters"), ("Al\u200bex", "control_characters"),
    ("Al\x1b[31mex", "control_characters"), (42, "not_text"), (["A"], "not_text")])
def test_bad_author_or_game(field, value, code):
    assert problems(**{field: value}) == (Problem(field, code),)


# --- 4. wheelbase -------------------------------------------------------------

@pytest.mark.parametrize("wheelbase", ["csl dd", "CSL DD Pro", "Moza R9", 20, ["CSL DD"]])
def test_wheelbase_must_be_one_understeer_knows(wheelbase):
    assert problems(wheelbase=wheelbase) == (Problem("wheelbase", "unknown_wheelbase"),)


def test_every_known_wheelbase_is_accepted():
    for name, drive in submission.WHEELBASE_DRIVES.items():
        assert submit(wheelbase=name, drive=drive).entry["meta"]["wheelbase"] == name


@pytest.mark.parametrize("changes, problem", [
    (dict(drive="belt"), Problem("drive", "not_this_wheelbase")),
    (dict(drive="gear"), Problem("drive", "invalid")),
    (dict(torque_nm=0), Problem("torque_nm", "invalid", 40)),
    (dict(torque_nm=41), Problem("torque_nm", "invalid", 40)),
    (dict(torque_nm=8.5), Problem("torque_nm", "invalid", 40)),
    (dict(torque_nm=True), Problem("torque_nm", "invalid", 40)),
])
def test_drive_and_torque(changes, problem):
    assert problems(**changes) == (problem,)


# --- 5./6./7. values ----------------------------------------------------------

def test_unsupported_setting_is_refused_not_left_out():
    assert problems(values={"FF": 70, "ZZZ": 1}) == (
        Problem("values", "unsupported_setting", "ZZZ"),)


def test_value_that_would_be_adjusted_is_refused():
    assert problems(values={"SEN": 905}) == (
        Problem("values", "adjusted_value", ("SEN", 905, 900)),)


@pytest.mark.parametrize("value", [101, -1, 70.0, 70.5, math.nan, math.inf,
                                   -math.inf, "70", True, None, [70], {"v": 70}])
def test_invalid_value(value):
    assert problems(values={"FF": value}) == (Problem("values", "invalid_value", "FF"),)


def test_unknown_choice_is_invalid():
    choice = next(s for s in submission.SETTINGS if s.get("kind") == "choice")
    bad = max(c for c, _label in choice["choices"]) + 1
    assert problems(values={choice["id"]: bad}) == (
        Problem("values", "invalid_value", choice["id"]),)


@pytest.mark.parametrize("values", [{}, [], None, "FF=70", [("FF", 70)]])
def test_values_must_be_settings(values):
    assert problems(values=values) == (Problem("values", "no_settings"),)


def test_too_many_settings():
    values = {f"X{chr(65 + i // 26)}{chr(65 + i % 26)}": 1 for i in range(65)}
    assert problems(values=values) == (Problem("values", "too_many_settings", 64),)


def test_every_wrong_setting_is_named():
    found = problems(values={"FF": 101, "SEN": 905, "ZZZ": 1, "FOR": 100})
    assert [p.detail for p in found] == ["FF", ("SEN", 905, 900), "ZZZ"]


# --- 8. text length and form ---------------------------------------------------

def test_name_limits():
    assert problems(name="x" * 81) == (Problem("name", "too_long", 80),)
    assert problems(name="🏎" * 51) == (Problem("name", "too_long", 200),)
    assert submit(name="🏎" * 50).entry["preset"]["name"] == "🏎" * 50


def test_description_limits():
    assert submit(description="x" * 1000).entry["meta"]["description"] == "x" * 1000
    assert problems(description="x" * 1001) == (Problem("description", "too_long", 1000),)
    assert submit(description="\n".join(["a"] * 20)).entry
    assert problems(description="\n".join(["a"] * 21)) == (
        Problem("description", "too_many_lines", 20),)


@pytest.mark.parametrize("field", ["name", "author", "game", "description"])
@pytest.mark.parametrize("text", ["see https://example.org", "www.example.org",
                                  "javascript://x", "FILE:///etc"])
def test_web_addresses_are_refused(field, text):
    assert Problem(field, "web_address") in problems(**{field: text})


@pytest.mark.parametrize("tags, problem", [
    (["a" * 25], Problem("tags", "invalid")), (["two words"], Problem("tags", "invalid")),
    ([""], Problem("tags", "invalid")), ([1], Problem("tags", "invalid")),
    ("gt3", Problem("tags", "invalid")), (None, Problem("tags", "invalid")),
    ([f"t{i}" for i in range(11)], Problem("tags", "too_many", 10))])
def test_bad_tags(tags, problem):
    assert problems(tags=tags) == (problem,)


def test_markup_and_shell_text_stays_plain_text():
    text = "<b>bold</b> $(id) `id` ${{ x }} ' \" ; & |"
    entry = submit(description=text, game=text[:80]).entry
    assert entry["meta"]["description"] == text
    assert read_back(entry).entries[0].meta.description == text


# --- 9. privacy ---------------------------------------------------------------

@pytest.mark.parametrize("text, code", [
    ("mail me: alex.smith@example.org", "email_address"),
    ("ALEX@EXAMPLE.DE", "email_address"),
    ("from /home/alex/.config/understeer/presets", "file_path"),
    ("~/presets/gt3.json", "file_path"),
    (r"C:\Users\alex\presets", "file_path"),
    ("/run/media/alex/usb", "file_path"),
])
@pytest.mark.parametrize("field", ["name", "author", "game", "description"])
def test_personal_data_is_refused(field, text, code):
    if field == "name" and code == "email_address":
        text = text[-24:]
    assert Problem(field, code) in problems(**{field: text[:40]})


@pytest.mark.parametrize("text", ["50/50 split", "1080° wheel, FF 70 / 80",
                                  "GT3 @ Spa", "Version 2.3.3"])
def test_ordinary_text_is_no_personal_data(text):
    assert submit(description=text).warnings == ()


def test_private_words_are_warned_about_but_not_used():
    result = submit(author="arne", description="My setup",
                    private_words=["arne", "nobara-pc", "", "ab"])
    assert result.warnings == (Problem("author", "private_word"),)
    assert "nobara-pc" not in submission.entry_json(result.entry)


def test_serial_like_codes_are_warned_about():
    result = submit(description="Base 0123ABCD4567EF89 works fine")
    assert result.warnings == (Problem("description", "looks_like_serial"),)


def test_nothing_is_added_that_wasnt_passed():
    """No folder, user, path, device or machine data ends up in the entry:
    it holds the given fields and the settings, nothing else."""
    entry = submit().entry
    assert set(entry) == {"id", "meta", "preset"}
    assert set(entry["meta"]) == {"author", "game", "wheelbase", "description"}
    assert set(entry["preset"]) == {"understeer_preset", "name", "values"}


# --- ids ----------------------------------------------------------------------

@pytest.mark.parametrize("game, name, expected", [
    ("Assetto Corsa Competizione", "GT3 Endurance",
     "assetto-corsa-competizione-gt3-endurance"),
    ("Gran Turismo 7", "Nürburgring Nordschleife", "gran-turismo-7-nurburgring-nordschleife"),
    ("  ", "--GT3--", "gt3"),
    ("EA Sports WRC", "Rallye  Monte-Carlo!!", "ea-sports-wrc-rallye-monte-carlo"),
])
def test_suggested_id(game, name, expected):
    assert submission.suggest_id(game, name) == expected


def test_suggested_id_without_latin_letters_uses_the_fingerprint():
    found = submission.suggest_id("グランツーリスモ", "設定", values={"FF": 70})
    assert found == "preset-" + library.fingerprint({"FF": 70})[:12]


def test_suggested_id_is_short_enough():
    found = submission.suggest_id("G" * 50, "a-name " * 20)
    assert library.ID_RE.fullmatch(found) and len(found) <= 64


def test_taken_id_gets_the_author_then_a_number():
    taken = {"acc-gt3"}
    assert submission.suggest_id("ACC", "GT3", "Alex", taken=taken) == "acc-gt3-alex"
    taken.add("acc-gt3-alex")
    assert submission.suggest_id("ACC", "GT3", "Alex", taken=taken) == "acc-gt3-alex-2"
    taken.add("acc-gt3-alex-2")
    assert submission.suggest_id("ACC", "GT3", "Alex", taken=taken) == "acc-gt3-alex-3"


def test_suggested_id_avoids_the_catalog():
    entry = submit(game="ACC", name="GT3", taken_ids={"acc-gt3"}).entry
    assert entry["id"] == "acc-gt3-alex"


@pytest.mark.parametrize("entry_id, problem", [
    ("Bad Id", Problem("id", "invalid")), ("-x", Problem("id", "invalid")),
    ("x" * 65, Problem("id", "invalid")), ("taken", Problem("id", "taken"))])
def test_given_id(entry_id, problem):
    assert problems(entry_id=entry_id, taken_ids={"taken"}) == (problem,)
    assert submit(entry_id="my-id").entry["id"] == "my-id"


# --- 10. deterministic output -------------------------------------------------

def test_serialization_is_deterministic():
    a = submit(values={"FOR": 100, "FF": 75, "SEN": 540}, tags=["b", "a"])
    b = submit(values={"SEN": 540, "FOR": 100, "FF": 75}, tags=["b", "a"])
    assert submission.entry_json(a.entry) == submission.entry_json(b.entry)
    text = submission.entry_json(a.entry)
    assert text.endswith("}\n") and json.loads(text) == a.entry
    assert text.index('"SEN"') < text.index('"FF"') < text.index('"FOR"')


def test_serialization_keeps_unicode_readable():
    text = submission.entry_json(submit(author="Jürgen", game="Le Mans Ultimate").entry)
    assert '"author": "Jürgen"' in text


# --- 11. read back by the catalog parser ----------------------------------------

def test_round_trip_through_the_catalog_parser():
    result = submit(name="Nürburgring 🏁", tags=["gt3"], drive="direct_drive",
                    torque_nm=8, description="Line one\n\nLine three")
    catalog = read_back(json.loads(submission.entry_json(result.entry)))
    assert catalog.skipped == []
    (got,) = catalog.entries
    assert (got.id, got.name, got.values) == (
        result.entry["id"], "Nürburgring 🏁", {"SEN": 540, "FF": 75, "FOR": 100})
    assert got.unsupported == () and got.adjusted == ()
    assert got.meta == library.Meta("Alex", "Assetto Corsa Competizione", "CSL DD",
                                    "direct_drive", 8, "Line one\n\nLine three",
                                    ("gt3",))
    assert got.fingerprint == library.fingerprint(VALUES)


def test_entry_fits_into_the_fixture_catalog():
    fixture = ROOT / "tests" / "fixtures" / "library" / "catalog.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    taken = {e["id"] for e in data["presets"]}
    data["presets"].append(submit(taken_ids=taken).entry)
    catalog = library.parse_catalog(json.dumps(data).encode())
    assert catalog.skipped == [] and len(catalog.entries) == len(taken) + 1


def test_the_parser_is_the_last_word(monkeypatch):
    """Should the checks here ever let through what the parser changes,
    the submission is refused rather than different from what was shown."""
    monkeypatch.setattr(submission.library, "MAX_GAME", 10)
    assert problems(game="Assetto Corsa") == (Problem("game", "too_long", 10),)
    monkeypatch.setattr(submission.library, "MAX_GAME", 80)
    real = library.parse_catalog
    monkeypatch.setattr(submission.library, "parse_catalog",
                        lambda raw: real(raw.replace(b'"Alex"', b'"Ale"')))
    assert problems() == (Problem("entry", "not_accepted"),)


# --- 12. the local preset stays as it is ----------------------------------------

def test_local_preset_is_not_changed():
    values = {"FOR": 100, "FF": 75, "SEN": 540}
    tags = ["GT3"]
    before = copy.deepcopy((values, tags))
    result = submit(values=values, tags=tags)
    assert (values, tags) == before
    assert list(values) == ["FOR", "FF", "SEN"]
    result.entry["preset"]["values"]["FF"] = 1
    assert values["FF"] == 75


def test_local_preset_is_not_changed_when_refused():
    values = {"FF": 75, "SEN": 905, "ZZZ": 1}
    before = copy.deepcopy(values)
    problems(values=values)
    assert values == before


# --- 13. no hardware, no files, no network ----------------------------------------

def test_preparing_touches_neither_hardware_nor_files_nor_network():
    """In a fresh interpreter: preparing a submission raises no audit event
    at all (no open, ioctl, socket, subprocess, ...), and reads no
    environment, user or host information."""
    import subprocess
    import sys
    code = """
import sys
from understeer import submission
events = []
sys.addaudithook(lambda event, args: events.append(event))
result = submission.prepare("GT3", {"FF": 70, "SEN": 900}, author="A",
    game="G", wheelbase="CSL DD", description="D", tags=["x"],
    private_words=["someone"])
submission.entry_json(result.entry)
try:
    submission.prepare("GT3", {"FF": 700}, author="A", game="G",
        wheelbase="CSL DD", description="D")
except submission.Invalid:
    pass
submission.suggest_id("G", "N", "A", {"FF": 1}, {"g-n"})
assert events == [], sorted(set(events))
"""
    run = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr


def test_module_reads_no_system_information():
    source = (ROOT / "understeer" / "submission.py").read_text(encoding="utf-8")
    for name in ("import os", "getpass", "socket", "platform", "pathlib",
                 "environ", "open(", "subprocess", "urllib", "http", "Gtk",
                 "Gio", "config"):
        assert name not in source, name
