import json

import pytest

from understeer import i18n, sharing
from understeer.settings import SIM_DEFAULTS


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")


def one(text, fallback_name=""):
    """(name, values) of the only preset found."""
    result = sharing.find_presets(text, fallback_name)
    assert len(result.presets) == 1
    return result.presets[0].name, result.presets[0].values


def test_json_round_trip():
    text = sharing.preset_json("GT3", {"FF": 85, "SEN": 540})
    data = json.loads(text)
    assert data == {"understeer_preset": 1, "name": "GT3",
                    "values": {"SEN": 540, "FF": 85}}    # in SETTINGS order
    assert one(text) == ("GT3", {"SEN": 540, "FF": 85})


def test_folder_is_shared():
    text = sharing.preset_json("Drift", {"FF": 55}, "BeamNG")
    assert json.loads(text)["folder"] == "BeamNG"
    found = sharing.find_presets(text).presets[0]
    assert (found.name, found.folder) == ("Drift", "BeamNG")
    found = sharing.find_presets(sharing.preset_text("Drift", {"FF": 55}, "Beam")).presets[0]
    assert found.folder == "Beam"
    assert sharing.find_presets(sharing.preset_json("Drift", {"FF": 55})).presets[0].folder is None
    odd = json.dumps({"name": "Drift", "folder": {"x": 1}, "values": {"FF": 55}})
    assert sharing.find_presets(odd).presets[0].folder is None


def test_text_is_readable_and_round_trips():
    text = sharing.preset_text("GT3 Nordschleife", SIM_DEFAULTS)
    lines = text.splitlines()
    assert lines[0] == "Understeer preset “GT3 Nordschleife”"
    assert "Steering Angle: 900°" in lines
    assert "Force Feedback Strength: 70 %" in lines
    assert one(text) == ("GT3 Nordschleife", SIM_DEFAULTS)


def test_text_in_german_round_trips():
    i18n.set_language("de")
    text = sharing.preset_text("Drift", {"SEN": 0, "DRI": -3})
    assert "Lenkwinkel: Auto" in text
    assert one(text) == ("Drift", {"SEN": 0, "DRI": -3})


def test_found_among_other_words():
    data = json.dumps({"name": "Rally", "values": {"FF": 60}})
    post = f"Try my setup!\n{data}\nHave fun"
    assert one(post) == ("Rally", {"FF": 60})


def test_plain_preset_file_is_understood():
    """Preset files from the config folder have no format marker."""
    text = json.dumps({"name": "Old", "values": {"SHO": 40}}, indent=2)
    assert one(text) == ("Old", {"SHO": 40})


@pytest.mark.parametrize("values, kept", [
    ({"FF": 101}, {}),                          # above the range
    ({"SEN": 0}, {"SEN": 0}),                   # Auto
    ({"SEN": 45}, {}),
    ({"SEN": 2520}, {"SEN": 2520}),             # direct drive wheelbases
    ({"SEN": 2530}, {}),                        # that is how Auto reads
    ({"SEN": 2356}, {"SEN": 2350}),             # as the wheelbase keeps it
    ({"SHO": 35, "FF": 35}, {"SHO": 30, "FF": 35}),
    ({"FFS": 1}, {"FFS": 1}),                   # a choice
    ({"FFS": 7}, {}),
    ({"FF": "70"}, {}),
    ({"FF": True}, {}),
    ({"FF": 70.0}, {}),
    ({"XYZ": 1}, {}),                           # unknown setting
])
def test_values_are_checked(values, kept):
    assert sharing.clean_values(values) == kept


def test_invalid_values_are_left_out():
    text = json.dumps({"name": "X", "values": {"FF": 70, "SHO": 500}})
    assert one(text) == ("X", {"FF": 70})


@pytest.mark.parametrize("text", [
    "", "hello", "[1, 2]", '{"name": "X"}', '{"values": [1]}',
    '{"name": "X", "values": {"XYZ": 1}}', "{" * 10,
    '{"values": {"FF": 1}}' + " " * sharing.MAX_SIZE,
])
def test_no_preset(text):
    with pytest.raises(sharing.Error):
        sharing.find_presets(text)


@pytest.mark.parametrize("name, expected", [
    ("  Spaced   name ", "Spaced name"),
    ("x" * 200, "x" * sharing.MAX_NAME),
    ("", "file"), (None, "file"), (5, "file"), ("a\x00b\nc", "ab c"),
])
def test_name(name, expected):
    text = json.dumps({"name": name, "values": {"FF": 1}})
    assert one(text, "file")[0] == expected


def test_name_without_fallback():
    assert one('{"values": {"FF": 1}}')[0] == "Imported Preset"


def test_long_name_fits_a_file_name():
    name = one(json.dumps({"name": "ü" * 200, "values": {"FF": 1}}))[0]
    assert len(name.encode()) <= sharing.MAX_NAME_BYTES


def test_nothing_is_run():
    """Text is only read as JSON; markup and code stay plain text."""
    name = "<b>x</b> $(rm -rf ~) __import__('os')"
    text = sharing.preset_text(name, {"FF": 70})
    assert one(text) == (name, {"FF": 70})


def preset(name, **values):
    return json.dumps({"understeer_preset": 1, "name": name, "values": values})


def names(text):
    return [(p.name, p.version) for p in sharing.find_presets(text).presets]


def test_array():
    text = f"[\n{preset('A', FF=50)},\n{preset('B', FF=60)}\n]"
    assert names(text) == [("A", 1), ("B", 1)]


def test_forum_thread():
    """Shared texts among posts, quoted, repeated and improved."""
    gt3 = sharing.preset_text("GT3", {"FF": 70, "SHO": 40})
    rally = sharing.preset_text("Rally", {"FF": 60})
    better = sharing.preset_text("GT3", {"FF": 75, "SHO": 40})
    quoted = "\n".join("> " + line for line in gt3.splitlines())
    thread = (f"Here is mine:\n{gt3}\nNice! {{not json}} :-{{\n{quoted}\n"
              f"Mine: {rally}\nUpdate: {better}\nSignature {{ }}")
    assert names(thread) == [("GT3", 1), ("Rally", 1), ("GT3", 2)]


def test_inside_other_json():
    text = json.dumps({"presets": [json.loads(preset("A", FF=1)),
                                   json.loads(preset("B", FF=2))]})
    assert names(text) == [("A", 1), ("B", 1)]


def test_skipped_and_cut():
    many = "".join(preset(f"P{i}", FF=i) for i in range(sharing.MAX_PRESETS + 3))
    result = sharing.find_presets(many + preset("Bad", XYZ=1))
    assert len(result.presets) == sharing.MAX_PRESETS
    assert result.cut == 3 and result.skipped == 1


def test_broken_brackets_end_quickly():
    import time
    start = time.monotonic()
    for text in ("{" * sharing.MAX_SIZE, '{"a":' * 200000,
                 ('{"a": [' * 50 + "x") * 2000, "{}" * sharing.MAX_SIZE,
                 '{"a":1 ' * sharing.MAX_SIZE):
        with pytest.raises(sharing.Error):
            sharing.find_presets(text[:sharing.MAX_SIZE])
    assert time.monotonic() - start < 3
