import pytest

from understeer import i18n
from understeer.settings import (PAGES, SETTINGS, SETTINGS_BY_ID, SIM_DEFAULTS,
                                 format_value)


def test_ids_are_unique():
    assert len(SETTINGS_BY_ID) == len(SETTINGS)


@pytest.mark.parametrize("s", SETTINGS, ids=lambda s: s["id"])
def test_setting_is_complete(s):
    assert s["page"] in {key for key, _title, _icon in PAGES}
    assert s["group"] and s["label"] and s["desc"]
    assert s["icon"].endswith("-symbolic")
    if s.get("kind") == "choice":
        values = [v for v, _l in s["choices"]]
        assert len(values) == len(set(values))
    else:
        assert s["lo"] < s["hi"]
        assert all(s["lo"] <= v <= s["hi"] for v in s.get("quick", []))


@pytest.mark.parametrize("attr_id, value", SIM_DEFAULTS.items())
def test_sim_default_is_valid(attr_id, value):
    s = SETTINGS_BY_ID[attr_id]
    if s.get("kind") == "choice":
        assert value in [v for v, _l in s["choices"]]
    else:
        assert s["lo"] <= value <= s["hi"]


@pytest.fixture
def english(monkeypatch):
    """Source texts, whatever language the system uses."""
    monkeypatch.setattr(i18n, "_CATALOG", {})


@pytest.mark.parametrize("attr_id, value, text", [
    ("SEN", 900, "900°"),
    ("SEN", 0, "Auto"),
    ("FF", 70, "70 %"),
    ("INT", 3, "3"),
    ("DRI", 0, "Off"),
    ("DRI", -3, "Counter-steer 3"),
    ("DRI", 2, "Centering +2"),
    ("BLI", 0, "Off"),
    ("BLI", 60, "60 %"),
    ("BLI", 101, "Always on"),
    ("FFS", 1, "Peak"),
    ("FFS", 9, "9"),            # value the app does not know
])
def test_format_value(english, attr_id, value, text):
    assert format_value(SETTINGS_BY_ID[attr_id], value) == text


def test_format_value_translated(monkeypatch):
    monkeypatch.setattr(i18n, "_CATALOG", {"Peak": "Spitze"})
    assert format_value(SETTINGS_BY_ID["FFS"], 1) == "Spitze"
