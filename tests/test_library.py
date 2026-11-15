import copy
import json
import pathlib

import pytest

from understeer import i18n, library, sharing

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "library" / "catalog.json"


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")


@pytest.fixture
def catalog():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def parse(data):
    return library.parse_catalog(json.dumps(data).encode())


def entry(**changes):
    """A valid entry with changes; a value None removes the key."""
    data = {"id": "test", "meta": {"game": "Test"},
            "preset": {"understeer_preset": 1, "name": "Test",
                       "values": {"FF": 70}}}
    for key, value in changes.items():
        target, key = (data["preset"], key[7:]) if key.startswith("preset_") \
            else (data["meta"], key[5:]) if key.startswith("meta_") else (data, key)
        if value is None:
            target.pop(key, None)
        else:
            target[key] = value
    return data


def skipped(item):
    """The reason the only entry was skipped, or None if it was taken."""
    result = parse({"understeer_library": 1, "presets": [item]})
    assert len(result.entries) + len(result.skipped) == 1
    return result.skipped[0][2] if result.skipped else None


# --- the fixture -------------------------------------------------------------

def test_fixture_is_read_whole():
    result = library.parse_catalog(FIXTURE.read_bytes())
    assert result.skipped == []
    gt3, rally = result.entries
    assert (gt3.id, gt3.name) == ("acc-gt3-endurance", "GT3 Endurance")
    assert gt3.meta == library.Meta(
        author="Example Driver", game="Assetto Corsa Competizione",
        wheelbase="CSL DD", drive="direct_drive", torque_nm=8,
        description="Calm GT3 feel for long stints.\n"
                    "Lower the strength on weaker wheelbases.",
        tags=("gt3", "endurance"))
    assert gt3.values["SEN"] == 540 and gt3.unsupported == ()
    assert (rally.name, rally.meta.drive, rally.values["DRI"]) == \
        ("Rally Gravel", "belt", -2)


def test_preset_is_the_exported_format(catalog):
    """The preset part of an entry is what Understeer exports, so the
    existing import understands it unchanged."""
    for item, parsed in zip(catalog["presets"],
                            library.parse_catalog(FIXTURE.read_bytes()).entries):
        exported = json.loads(sharing.preset_json(parsed.name, parsed.values))
        assert exported == item["preset"]
        found = sharing.find_presets(json.dumps(item["preset"])).presets[0]
        assert (found.name, found.values) == (parsed.name, parsed.values)


def test_metadata_stays_out_of_the_values():
    gt3 = library.parse_catalog(FIXTURE.read_bytes()).entries[0]
    assert set(gt3.values) <= {s["id"] for s in library.SETTINGS}


# --- identifiers ---------------------------------------------------------------

def test_fingerprint_is_deterministic():
    a = library.fingerprint({"FF": 70, "SEN": 900})
    assert a == library.fingerprint({"SEN": 900, "FF": 70})
    assert len(a) == 16 and a != library.fingerprint({"FF": 71, "SEN": 900})


@pytest.mark.parametrize("entry_id", ["a", "gt3", "acc-gt3-2024", "x" * 64])
def test_good_ids(entry_id):
    assert skipped(entry(id=entry_id)) is None


@pytest.mark.parametrize("entry_id", [
    "", "-gt3", "gt3-", "GT3", "gt3_endurance", "gt3 endurance", "x" * 65,
    "../evil", "a/b", "a\\b", ".", "..", "a.json", "a\0b", 5, None, ["a"]])
def test_bad_ids(entry_id):
    assert "id" in skipped(entry(id=entry_id))


def test_later_duplicate_id_is_skipped():
    first, second = entry(), entry(preset_name="Other")
    result = parse({"understeer_library": 1, "presets": [first, second]})
    assert [e.name for e in result.entries] == ["Test"]
    assert result.skipped == [(1, "test", "the id is used already")]


@pytest.mark.parametrize("name, kept", [
    ("  GT3\nNordschleife\t ", "GT3 Nordschleife"),
    ("../../.bashrc", "../../.bashrc"),     # a name only; the file is named locally
    ("<b>bold</b>", "<b>bold</b>"),         # text, never markup
    ("x" * 200, "x" * sharing.MAX_NAME),
])
def test_names_are_one_short_line(name, kept):
    result = parse({"understeer_library": 1,
                    "presets": [entry(preset_name=name)]})
    assert result.entries[0].name == kept


@pytest.mark.parametrize("name", ["", "   ", "\n\t", 5, None])
def test_preset_needs_a_name(name):
    assert "name" in skipped(entry(preset_name=name))


# --- values ----------------------------------------------------------------

def test_values_are_ordered_and_fitted():
    result = parse({"understeer_library": 1, "presets": [
        entry(preset_values={"SHO": 35, "SEN": 2356, "FF": 70})]})
    assert list(result.entries[0].values.items()) == \
        [("SEN", 2350), ("FF", 70), ("SHO", 30)]


def test_unknown_settings_are_left_out_and_listed():
    result = parse({"understeer_library": 1, "presets": [
        entry(preset_values={"FF": 70, "NEW": 3, "Abc": 1})]})
    e = result.entries[0]
    assert e.values == {"FF": 70} and e.unsupported == ("Abc", "NEW")


@pytest.mark.parametrize("values", [
    {"FF": 101}, {"FF": -1}, {"FF": "70"}, {"FF": True}, {"FF": 70.0},
    {"FF": None}, {"FFS": 7}, {"ACP": 0}, {"SEN": 45},
])
def test_known_setting_out_of_range_skips_the_preset(values):
    assert "out of range" in skipped(entry(preset_values=dict(values, SHO=50)))


@pytest.mark.parametrize("values", [
    {}, [], "FF=70", None, {"NEW": 3},
    {"../x": 1}, {"FF FF": 1}, {"VERYLONGID": 1},
    {f"X{chr(65 + i // 26)}{chr(65 + i % 26)}": 1 for i in range(65)},
])
def test_bad_values_skip_the_preset(values):
    assert "values" in skipped(entry(preset_values=values)) or \
        "no setting" in skipped(entry(preset_values=values))


@pytest.mark.parametrize("version", [None, 0, 2, "1", True, 1.0])
def test_preset_format_must_be_supported(version):
    assert "understeer_preset" in skipped(entry(preset_understeer_preset=version))


def test_shared_folder_is_not_taken():
    """A catalog doesn't get to put presets into the user's folders."""
    item = entry()
    item["preset"]["folder"] = "../../etc"
    e = parse({"understeer_library": 1, "presets": [item]}).entries[0]
    assert not hasattr(e, "folder")


# --- metadata ----------------------------------------------------------------

def test_metadata_is_optional():
    e = parse({"understeer_library": 1, "presets": [entry(meta=None)]}).entries[0]
    assert e.meta == library.Meta(None, None, None, None, None, None, ())


def test_texts_are_cleaned_and_cut():
    e = parse({"understeer_library": 1, "presets": [entry(
        meta_author="  Max\nMuster\x1b[31m ", meta_game="g" * 300,
        meta_wheelbase="", meta_tags=["GT3", "gt3", "Rain"])]}).entries[0]
    assert e.meta.author == "Max Muster[31m"
    assert e.meta.game == "g" * library.MAX_GAME
    assert e.meta.wheelbase is None
    assert e.meta.tags == ("gt3", "rain")


def test_description_keeps_lines_within_limits():
    text = "\n".join(f"line {i}\x07" for i in range(30)) + "\n\n\n"
    e = parse({"understeer_library": 1,
               "presets": [entry(meta_description=text)]}).entries[0]
    lines = e.meta.description.split("\n")
    assert len(lines) == library.MAX_DESCRIPTION_LINES and lines[0] == "line 0"
    long = parse({"understeer_library": 1,
                  "presets": [entry(meta_description="x" * 5000)]}).entries[0]
    assert len(long.meta.description) == library.MAX_DESCRIPTION


def test_urls_and_markup_stay_plain_text():
    text = '<a href="https://evil.example">click</a> & https://x.example/y'
    e = parse({"understeer_library": 1,
               "presets": [entry(meta_description=text)]}).entries[0]
    assert e.meta.description == text


@pytest.mark.parametrize("changes", [
    {"meta": []}, {"meta": "x"},
    {"meta_author": 5}, {"meta_game": ["x"]}, {"meta_description": {"a": 1}},
    {"meta_drive": "gear"}, {"meta_drive": 1},
    {"meta_torque_nm": 0}, {"meta_torque_nm": 41}, {"meta_torque_nm": 8.5},
    {"meta_torque_nm": "8"}, {"meta_torque_nm": True},
    {"meta_tags": "gt3"}, {"meta_tags": ["gt3", 5]}, {"meta_tags": ["gt 3"]},
    {"meta_tags": ["-gt3"]}, {"meta_tags": ["x" * 25]},
    {"meta_tags": [f"t{i}" for i in range(11)]},
])
def test_bad_metadata_skips_the_preset(changes):
    assert "meta" in skipped(entry(**changes))


def test_unknown_keys_are_ignored():
    item = entry()
    item["future"] = {"x": 1}
    item["meta"]["homepage"] = "https://example.org"
    e = parse({"understeer_library": 1, "presets": [item]}).entries[0]
    assert "homepage" not in e.meta._fields


# --- the catalog as a whole --------------------------------------------------

def test_bad_entries_dont_spoil_the_good_ones(catalog):
    catalog["presets"].insert(1, "not an entry")
    catalog["presets"].append(entry(id="Bad Id"))
    result = parse(catalog)
    assert [e.id for e in result.entries] == \
        ["acc-gt3-endurance", "dirt-rally-2-gravel"]
    assert [(p, i) for p, i, _r in result.skipped] == [(1, None), (3, None)]


@pytest.mark.parametrize("raw", [
    b"", b"not json", b"[]", b"null", b'"x"',
    b'{"presets": []}',
    b'{"understeer_library": "1", "presets": []}',
    b'{"understeer_library": true, "presets": []}',
    b'{"understeer_library": 0, "presets": []}',
    b'{"understeer_library": 1}',
    b'{"understeer_library": 1, "presets": {}}',
    b'{"understeer_library": 1, "presets": [], "presets": []}',
    b'{"understeer_library": 1, "presets": [{"id": NaN}]}',
    b'{"understeer_library": 1, "presets": [Infinity]}',
    b"\xff\xfe{}",
    b"[" * 100000 + b"]" * 100000,
])
def test_broken_catalogs_are_rejected(raw):
    with pytest.raises(library.Error):
        library.parse_catalog(raw)


LONG = b"1" + b"0" * 5000          # more digits than Python makes an int of


@pytest.mark.parametrize("raw", [
    b'{"understeer_library": 1, "presets": [{"id": "a", "preset": '
    b'{"understeer_preset": 1, "name": "x", "values": {"FF": ' + LONG + b'}}}]}',
    b'{"understeer_library": ' + LONG + b', "presets": []}',
    b'{"understeer_library": 1, "presets": [], "x": -' + LONG + b'}',
    b'{"understeer_library": 1, "presets": [' + LONG + b']}',
], ids=["in-values", "format-version", "unknown-key", "in-presets"])
def test_too_long_numbers_reject_the_catalog(raw):
    """A number Python won't turn into an int is a broken catalog, not an
    error escaping the parser."""
    with pytest.raises(library.Error, match="number that is too long"):
        library.parse_catalog(raw)


@pytest.mark.parametrize("raw, reason", [
    (b'{"understeer_library": 1, "presets": [{"id": NaN}]}', "NaN is not a number"),
    (b'{"understeer_library": 1, "presets": [], "presets": []}', "duplicate key"),
])
def test_reasons_of_the_checks_are_kept(raw, reason):
    """The checks while reading raise library.Error, a ValueError too: they
    keep their own reason, not the one for too long numbers."""
    with pytest.raises(library.Error, match=reason):
        library.parse_catalog(raw)


def test_long_but_convertible_numbers_are_just_invalid_values():
    """Up to the limit a long number is read, and then out of range."""
    raw = (b'{"understeer_library": 1, "presets": [{"id": "a", "preset": '
           b'{"understeer_preset": 1, "name": "x", "values": {"FF": 1'
           + b"0" * 4000 + b'}}}]}')
    catalog = library.parse_catalog(raw)
    assert catalog.entries == [] and catalog.skipped == [
        (0, "a", "preset.values.FF is out of range or no number")]


def test_newer_catalog_format_is_rejected():
    with pytest.raises(library.Error, match="newer"):
        parse({"understeer_library": 2, "presets": []})


def test_text_instead_of_bytes_is_rejected():
    with pytest.raises(library.Error):
        library.parse_catalog(FIXTURE.read_text())


def test_size_limits(catalog):
    with pytest.raises(library.Error, match="too big"):
        library.parse_catalog(b" " * (library.MAX_CATALOG_BYTES + 1))
    many = {"understeer_library": 1,
            "presets": [entry(id=f"p{i}") for i in range(library.MAX_ENTRIES + 1)]}
    with pytest.raises(library.Error, match="too many"):
        parse(many)
    many["presets"].pop()
    assert len(parse(many).entries) == library.MAX_ENTRIES


def test_byte_order_mark_is_fine():
    raw = b"\xef\xbb\xbf" + FIXTURE.read_bytes()
    assert len(library.parse_catalog(raw).entries) == 2


def test_parsing_leaves_the_input_alone(catalog):
    before = copy.deepcopy(catalog)
    parse(catalog)
    assert catalog == before


# --- the same values as a local import --------------------------------------

def _sweep(setting):
    if setting.get("kind") == "choice":
        return [c for c, _l in setting["choices"]] + [-1, 0, 5, 99]
    lo, hi = setting["lo"], setting["hi"]
    return list(range(lo - 15, min(hi, lo + 40) + 1)) + \
        list(range(max(lo, hi - 40), hi + 16)) + [0, -1, 2530]


@pytest.mark.parametrize("setting", library.SETTINGS, ids=lambda s: s["id"])
def test_values_match_the_local_import(setting):
    """Every value the library takes ends up as a local import keeps it;
    every value it refuses, a local import leaves out as well."""
    attr = setting["id"]
    other = "FF" if attr != "FF" else "SHO"
    for value in _sweep(setting):
        local = sharing.find_presets(json.dumps(
            {"name": "x", "values": {attr: value, other: 50}})).presets[0].values
        result = parse({"understeer_library": 1, "presets": [
            entry(preset_values={attr: value, other: 50})]})
        if result.entries:
            assert result.entries[0].values == local, (attr, value)
        else:
            assert attr not in local, (attr, value)


def test_fitted_values_are_reported():
    e = parse({"understeer_library": 1, "presets": [entry(
        preset_values={"SHO": 35, "FF": 70, "SEN": 2356})]}).entries[0]
    assert e.adjusted == (("SEN", 2356, 2350), ("SHO", 35, 30))
    fixture = library.parse_catalog(FIXTURE.read_bytes()).entries
    assert all(f.adjusted == () for f in fixture)


# --- more limits ---------------------------------------------------------------

def test_duplicate_key_inside_an_entry_rejects_the_catalog():
    raw = (b'{"understeer_library": 1, "presets": [{"id": "a", "preset": '
           b'{"understeer_preset": 1, "name": "A", '
           b'"values": {"FF": 10, "FF": 100}}}]}')
    with pytest.raises(library.Error, match="duplicate"):
        library.parse_catalog(raw)


def test_too_long_number_touches_nothing():
    """In a fresh interpreter: a catalog with a number too long for an int
    is refused without any audit event (no open, socket, subprocess, ioctl,
    ...) and without loading a module that talks to devices."""
    import subprocess
    import sys
    code = """
import sys
from understeer import library
events = []
sys.addaudithook(lambda event, args: events.append(event))
raw = b'{"understeer_library": 1, "presets": [' + b"7" * 5000 + b']}'
try:
    library.parse_catalog(raw)
    raise SystemExit("not refused")
except library.Error:
    pass
assert events == [], sorted(set(events))
hardware = {"understeer.device", "understeer.capabilities", "understeer.leds",
            "understeer.display", "understeer.inputs", "understeer.device_info"}
assert not hardware & set(sys.modules), hardware & set(sys.modules)
"""
    root = pathlib.Path(__file__).parent.parent
    run = subprocess.run([sys.executable, "-c", code], cwd=root,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr + run.stdout


def test_name_fits_a_file_name_in_bytes():
    name = parse({"understeer_library": 1, "presets": [
        entry(preset_name="🏎" * 80)]}).entries[0].name
    assert len(name.encode()) <= sharing.MAX_NAME_BYTES


# --- no hardware, no files, no network ----------------------------------------

def test_parsing_touches_neither_hardware_nor_files_nor_network():
    """In a fresh interpreter: reading the library loads no module that
    talks to devices, and parsing raises no audit event at all (no open,
    ioctl, socket, subprocess, ...)."""
    import subprocess
    import sys
    code = f"""
import sys
from understeer import library
hardware = {{"understeer.device", "understeer.capabilities", "understeer.leds",
            "understeer.display", "understeer.inputs", "understeer.device_info",
            "understeer.axis_health", "understeer.background"}}
assert not hardware & set(sys.modules), hardware & set(sys.modules)
good = open({str(FIXTURE)!r}, "rb").read()
bad = good.replace(b'"FF": 75', b'"FF": 750')
events = []
sys.addaudithook(lambda event, args: events.append(event))
assert len(library.parse_catalog(good).entries) == 2
assert len(library.parse_catalog(bad).skipped) == 1
try:
    library.parse_catalog(b"[" * 100000)
except library.Error:
    pass
assert events == [], sorted(set(events))
"""
    root = pathlib.Path(__file__).parent.parent
    run = subprocess.run([sys.executable, "-c", code], cwd=root,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
