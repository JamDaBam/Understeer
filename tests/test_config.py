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
    monkeypatch.setattr(config, "SLOT_COUNTS_FILE", cfg / "settings" / "slot_counts.json")
    monkeypatch.setattr(config, "APP_SETTINGS_FILE", cfg / "settings" / "app.json")
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


def test_slot_count_per_model(home):
    assert config.load_slot_count("0EB7:0020") == 0
    config.save_slot_count("0EB7:0020", 6)
    assert config.load_slot_count("0EB7:0020") == 6
    assert config.load_slot_count("0EB7:0006") == 0
    assert config.list_presets() == []


def test_broken_slot_count(home):
    config.SLOT_COUNTS_FILE.parent.mkdir(parents=True)
    config.SLOT_COUNTS_FILE.write_text('{"0EB7:0020": "six"}')
    assert config.load_slot_count("0EB7:0020") == 0


def test_app_settings(home):
    assert config.get_setting("language") is None
    assert config.get_setting("layout", "full") == "full"
    config.set_setting("language", "de")
    config.set_setting("layout", "compact")
    assert config.get_setting("language") == "de"
    assert config.get_setting("layout", "full") == "compact"
    config.set_setting("language", None)
    assert config.get_setting("language") is None
    assert config.get_setting("layout") == "compact"
    assert config.list_presets() == []


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


def test_rename_preset(home, monkeypatch):
    monkeypatch.setattr(config, "GAMES_FILE", config.CONFIG_DIR / "settings" / "games.json")
    old = config.preset_path("Drfit")
    config.write_preset(old, "Drfit", {"FF": 55})
    config.write_preset(config.preset_path("Rally"), "Rally", {"FF": 80})
    config.save_games([{"game": "steam:1", "name": "BeamNG.drive",
                        "preset": "Drfit.json", "auto": True}])
    new = config.rename_preset(old, "Drift")
    assert new == config.preset_path("Drift") and not old.exists()
    assert config.read_preset(new) == ("Drift", {"FF": 55})
    assert config.load_games()[0]["preset"] == "Drift.json"
    with pytest.raises(FileExistsError):
        config.rename_preset(new, "Rally")
    assert config.rename_preset(new, "Drift") == new    # same name: unchanged


def test_games_only_name_files_in_their_folders(home, monkeypatch):
    """A games.json from an import can't point outside the folders."""
    monkeypatch.setattr(config, "GAMES_FILE", config.CONFIG_DIR / "settings" / "games.json")
    config.save_games([
        {"game": "steam:1", "preset": "../../outside.json", "cover": "../x.png"},
        {"game": "steam:2", "preset": "..", "cover": "."},
        {"game": "steam:3", "preset": "Drift.json", "cover": "abc.png"}])
    games = config.load_games()
    assert [g["preset"] for g in games] == [None, None, "Drift.json"]
    assert [g.get("cover") for g in games] == [None, None, "abc.png"]


def test_write_file_replaces_completely(home):
    path = config.CONFIG_DIR / "settings" / "app.json"
    config.write_file(path, '{"a": 1}')
    config.write_file(path, b'{"a": 2}')
    assert path.read_text() == '{"a": 2}'
    assert [p.name for p in path.parent.iterdir()] == ["app.json"]


def _disk_full(self, target):
    raise OSError(28, "No space left on device")


def test_failed_write_keeps_old_file(home, monkeypatch):
    config.set_setting("language", "de")
    with monkeypatch.context() as m:
        m.setattr(config.pathlib.Path, "replace", _disk_full)
        config.set_setting("language", "en")     # must not raise
    assert config.get_setting("language") == "de"
    assert not list(config.APP_SETTINGS_FILE.parent.glob("*.part"))


def test_failed_save_is_logged(home, monkeypatch, caplog):
    def denied(path, data):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(config, "write_file", denied)
    with caplog.at_level("WARNING", logger="understeer.config"):
        config.save_axis_roles({"x": "brake"})
    assert "Could not save" in caplog.text
    assert "Permission denied" in caplog.text


def test_write_preset_raises(home, monkeypatch):
    monkeypatch.setattr(config.pathlib.Path, "replace", _disk_full)
    with pytest.raises(OSError):
        config.write_preset(config.preset_path("GT3"), "GT3", {"FF": 70})


# -- folders --------------------------------------------------------------------
def test_presets_by_folder(home):
    config.write_preset(config.preset_path("Daily"), "Daily", {"FF": 50})
    config.write_preset(config.preset_path("Drift"), "Drift", {"FF": 55}, "BeamNG")
    config.preset_path("Climb").write_text(json.dumps(        # by hand or older
        {"name": "Climb", "folder": "beamng ", "values": {"FF": 60}}))
    config.write_preset(config.preset_path("Stage"), "Stage", {"FF": 70}, "Assetto")
    path = config.preset_path
    # "beamng" is the same folder as "BeamNG"; the spelling of the first
    # file by name counts
    assert config.list_presets_by_folder() == [
        (None, [("Daily", path("Daily"))]),
        ("Assetto", [("Stage", path("Stage"))]),
        ("beamng", [("Climb", path("Climb")), ("Drift", path("Drift"))])]
    assert config.list_folders() == ["Assetto", "beamng"]
    assert config.rename_folder("BEAMNG", "BeamNG") == 1    # "Climb" only
    assert config.list_folders() == ["Assetto", "BeamNG"]
    assert config.existing_folder(" beamNG ") == "BeamNG"
    assert config.existing_folder("New") == "New"
    assert config.existing_folder("") is None
    config.set_preset_folder(path("Daily"), "ASSETTO")
    assert config.preset_folder(path("Daily")) == "Assetto"
    assert config.rename_folder("assetto", "beamng") == 2       # merged
    assert config.list_folders() == ["BeamNG"]
    # The folder doesn't change the list without folders
    assert [n for n, _p in config.list_presets()] == ["Climb", "Daily", "Drift", "Stage"]


def test_no_loose_presets(home):
    config.write_preset(config.preset_path("Drift"), "Drift", {"FF": 55}, "BeamNG")
    assert config.list_presets_by_folder() == [
        ("BeamNG", [("Drift", config.preset_path("Drift"))])]


def test_saved_again_stays_in_its_folder(home):
    path = config.preset_path("Drift")
    config.write_preset(path, "Drift", {"FF": 55}, "BeamNG")
    config.write_preset(path, "Drift", {"FF": 60})
    assert config.preset_folder(path) == "BeamNG"
    assert config.read_preset(path) == ("Drift", {"FF": 60})
    config.write_preset(path, "Drift", {"FF": 60}, None)
    assert config.preset_folder(path) is None
    assert "folder" not in json.loads(path.read_text())


def test_move_and_rename_folders(home, monkeypatch):
    monkeypatch.setattr(config, "GAMES_FILE", config.CONFIG_DIR / "settings" / "games.json")
    drift, climb = config.preset_path("Drift"), config.preset_path("Climb")
    config.write_preset(drift, "Drift", {"FF": 55})
    config.write_preset(climb, "Climb", {"FF": 60}, "Beam")
    config.set_preset_folder(drift, "  Beam ")
    assert config.preset_folder(drift) == "Beam"
    assert config.rename_folder("Beam", "BeamNG") == 2
    assert config.rename_folder("BeamNG", "BeamNG") == 0
    assert config.list_folders() == ["BeamNG"]
    config.write_preset(config.preset_path("Stage"), "Stage", {"FF": 70}, "Rally")
    assert config.rename_folder("Rally", "BeamNG") == 1         # merged
    assert config.list_folders() == ["BeamNG"]
    assert config.rename_folder("BeamNG", "") == 3              # out of it
    assert config.list_folders() == []
    assert config.read_preset(drift) == ("Drift", {"FF": 55})
    config.set_preset_folder(drift, "BeamNG")
    renamed = config.rename_preset(drift, "Drifting")
    assert config.preset_folder(renamed) == "BeamNG"


@pytest.mark.parametrize("folder, expected", [
    ("BeamNG", "BeamNG"), ("  Beam   NG \n", "Beam NG"), ("", None), ("   ", None),
    (None, None), (5, None), ("a\x00b\x1bc", "abc"), ("x" * 100, "x" * 80)])
def test_clean_folder(folder, expected):
    assert config.clean_folder(folder) == expected


def test_odd_folder_in_file(home):
    cfg = config.CONFIG_DIR
    cfg.mkdir(parents=True)
    (cfg / "a.json").write_text(json.dumps({"name": "A", "folder": ["x"], "values": {}}))
    (cfg / "b.json").write_text(json.dumps(["not", "a", "preset"]))
    assert [f for f, _g in config.list_presets_by_folder()] == [None]


def test_empty_folders_are_kept(home):
    drift = config.preset_path("Drift")
    config.write_preset(drift, "Drift", {"FF": 55}, "BeamNG")
    assert config.add_folder(" Rally ") == "Rally"
    assert config.add_folder("beamng") == "BeamNG"
    assert config.add_folder("") is None
    assert config.list_folders() == ["BeamNG", "Rally"]
    # Menus show folders with presets only, the presets page all
    assert [f for f, _g in config.list_presets_by_folder()] == ["BeamNG"]
    assert config.list_presets_by_folder(empty=True) == [
        ("BeamNG", [("Drift", drift)]), ("Rally", [])]
    config.set_preset_folder(drift, None)
    assert config.list_folders() == ["BeamNG", "Rally"]     # emptied, kept
    config.set_preset_folder(drift, "Rally")
    assert config.rename_folder("Rally", "Stage") == 1
    assert config.list_folders() == ["BeamNG", "Stage"]
    assert config.rename_folder("Stage", "beamng") == 1     # merged
    assert config.list_folders() == ["BeamNG"]
    assert config.preset_folder(drift) == "BeamNG"
    assert config.delete_folder("BeamNG") == 1
    assert config.list_folders() == [] and config.preset_folder(drift) is None
    assert config.read_preset(drift) == ("Drift", {"FF": 55})
    assert config.get_setting("preset_folders") is None
    config.set_setting("preset_folders", "nonsense")
    assert config.list_folders() == []
