import json

import pytest

from understeer import config


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the config folders at a temporary home directory."""
    cfg = tmp_path / ".config" / "Understeer"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(config, "OLD_CONFIG_DIR", tmp_path / ".config" / "SimPower")
    monkeypatch.setattr(config, "AXES_FILE", cfg / "settings" / "axes.json")
    monkeypatch.setattr(config, "SLOT_NAMES_FILE", cfg / "settings" / "slot_names.json")
    return tmp_path


def test_no_presets(home):
    assert config.list_presets() == []


def test_preset_round_trip(home):
    path = config.preset_path("GT3 Race")
    config.write_preset(path, "GT3 Race", {"FF": 70, "SEN": 900})
    assert config.list_presets() == [("GT3 Race", path)]
    assert config.read_preset(path) == ("GT3 Race", {"FF": 70, "SEN": 900})


def test_slashes_in_preset_name(home):
    assert config.preset_path("GT3/GT4\\Cup") == config.CONFIG_DIR / "GT3_GT4_Cup.json"


def test_broken_and_old_presets(home):
    cfg = config.CONFIG_DIR
    cfg.mkdir(parents=True)
    (cfg / "broken.json").write_text("{nope")
    (cfg / "old.json").write_text(json.dumps({"values": {"FF": 50}}))
    assert config.list_presets() == [("broken", cfg / "broken.json"),
                                     ("old", cfg / "old.json")]
    assert config.read_preset(cfg / "old.json") == ("old", {"FF": 50})
    with pytest.raises(json.JSONDecodeError):
        config.read_preset(cfg / "broken.json")


def test_axis_roles_are_no_preset(home):
    assert config.load_axis_roles() == {}
    config.save_axis_roles({"Pedals:1": "brake"})
    assert config.load_axis_roles() == {"Pedals:1": "brake"}
    assert config.list_presets() == []


def test_slot_names_per_model(home):
    assert config.load_slot_names("0EB7:0020") == {}
    config.save_slot_name("0EB7:0020", 1, "A")
    config.save_slot_name("0EB7:0020", 2, "  Set 1 ")
    config.save_slot_name("0EB7:0006", 1, "GT3")
    assert config.load_slot_names("0EB7:0020") == {1: "A", 2: "Set 1"}
    assert config.load_slot_names("0EB7:0006") == {1: "GT3"}
    assert config.list_presets() == []          # not mistaken for a preset


def test_empty_slot_name_removes_it(home):
    config.save_slot_name("0EB7:0020", 1, "A")
    config.save_slot_name("0EB7:0020", 1, "  ")
    assert config.load_slot_names("0EB7:0020") == {}
    assert json.loads(config.SLOT_NAMES_FILE.read_text()) == {}


def test_broken_slot_names_file(home):
    config.SLOT_NAMES_FILE.parent.mkdir(parents=True)
    config.SLOT_NAMES_FILE.write_text("[1, 2")
    assert config.load_slot_names("0EB7:0020") == {}
    config.save_slot_name("0EB7:0020", 3, "Rain")
    assert config.load_slot_names("0EB7:0020") == {3: "Rain"}


def test_migration_moves_old_folder(home):
    old = config.OLD_CONFIG_DIR
    old.mkdir(parents=True)
    (old / "a.json").write_text("{}")
    config.migrate_config()
    assert not old.exists()
    assert (config.CONFIG_DIR / "a.json").exists()


def test_migration_keeps_existing_folder(home):
    config.OLD_CONFIG_DIR.mkdir(parents=True)
    config.CONFIG_DIR.mkdir(parents=True)
    config.migrate_config()
    assert config.OLD_CONFIG_DIR.exists()
