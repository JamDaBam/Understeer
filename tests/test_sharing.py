import json

import pytest

from understeer import i18n, sharing
from understeer.settings import SIM_DEFAULTS


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")


def test_json_round_trip():
    text = sharing.preset_json("GT3", {"FF": 85, "SEN": 540})
    data = json.loads(text)
    assert data == {"understeer_preset": 1, "name": "GT3",
                    "values": {"SEN": 540, "FF": 85}}    # in SETTINGS order
    assert sharing.parse(text) == ("GT3", {"SEN": 540, "FF": 85})


def test_text_is_readable_and_round_trips():
    text = sharing.preset_text("GT3 Nordschleife", SIM_DEFAULTS)
    lines = text.splitlines()
    assert lines[0] == "Understeer preset “GT3 Nordschleife”"
    assert "Steering Angle: 900°" in lines
    assert "Force Feedback Strength: 70 %" in lines
    assert sharing.parse(text) == ("GT3 Nordschleife", SIM_DEFAULTS)


def test_text_in_german_round_trips():
    i18n.set_language("de")
    text = sharing.preset_text("Drift", {"SEN": 0, "DRI": -3})
    assert "Lenkwinkel: Auto" in text
    assert sharing.parse(text) == ("Drift", {"SEN": 0, "DRI": -3})


def test_found_among_other_words():
    data = json.dumps({"name": "Rally", "values": {"FF": 60}})
    post = f"Try my setup!\n{data}\nHave fun"
    assert sharing.parse(post) == ("Rally", {"FF": 60})


def test_plain_preset_file_is_understood():
    """Preset files from the config folder have no format marker."""
    text = json.dumps({"name": "Old", "values": {"SHO": 40}}, indent=2)
    assert sharing.parse(text) == ("Old", {"SHO": 40})


@pytest.mark.parametrize("values, kept", [
    ({"FF": 101}, {}),                          # above the range
    ({"SEN": 0}, {"SEN": 0}),                   # Auto
    ({"SEN": 45}, {}),
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
    assert sharing.parse(text) == ("X", {"FF": 70})


@pytest.mark.parametrize("text", [
    "", "hello", "[1, 2]", '{"name": "X"}', '{"values": [1]}',
    '{"name": "X", "values": {"XYZ": 1}}', "{" * 10,
    '{"values": {"FF": 1}}' + " " * sharing.MAX_SIZE,
])
def test_no_preset(text):
    with pytest.raises(sharing.Error):
        sharing.parse(text)


@pytest.mark.parametrize("name, expected", [
    ("  Spaced   name ", "Spaced name"),
    ("x" * 200, "x" * sharing.MAX_NAME),
    ("", "file"), (None, "file"), (5, "file"), ("a\x00b\nc", "ab c"),
])
def test_name(name, expected):
    text = json.dumps({"name": name, "values": {"FF": 1}})
    assert sharing.parse(text, fallback_name="file")[0] == expected


def test_name_without_fallback():
    assert sharing.parse('{"values": {"FF": 1}}')[0] == "Imported Preset"


def test_free_name():
    taken = {"GT3", "GT3 (2)"}
    assert sharing.free_name("Rally", taken.__contains__) == "Rally"
    assert sharing.free_name("GT3", taken.__contains__) == "GT3 (3)"


def test_long_name_fits_a_file_name():
    name = sharing.parse(json.dumps({"name": "ü" * 200, "values": {"FF": 1}}))[0]
    assert len(name.encode()) <= sharing.MAX_NAME_BYTES


def test_nothing_is_run():
    """Text is only read as JSON; markup and code stay plain text."""
    name = "<b>x</b> $(rm -rf ~) __import__('os')"
    text = sharing.preset_text(name, {"FF": 70})
    assert sharing.parse(text) == (name, {"FF": 70})
