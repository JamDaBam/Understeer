import pytest

from understeer import i18n
from understeer.settings import (ADVANCED_GROUPS, SETTINGS, SETTINGS_BY_ID, SIM_DEFAULTS,
                                 TABS, changes, format_value)


def test_ids_are_unique():
    assert len(SETTINGS_BY_ID) == len(SETTINGS)


@pytest.mark.parametrize("s", SETTINGS, ids=lambda s: s["id"])
def test_setting_is_complete(s):
    assert s["page"] in {key for key, _title in TABS}
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


def test_advanced_groups_exist():
    assert ADVANCED_GROUPS <= {s["group"] for s in SETTINGS}


def test_changes_in_settings_order():
    before = {"SEN": 900, "FF": 70, "DPR": 10}
    after = {"DPR": 0, "FF": 85, "SEN": 900, "SHO": 40}
    # unchanged SEN left out; SHO missing before; ordered as in SETTINGS
    assert changes(before, after) == [("FF", 70, 85), ("DPR", 10, 0),
                                      ("SHO", None, 40)]
    assert changes(after, after) == []
