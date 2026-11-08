import json
import zipfile

import pytest

from understeer import backup, config


@pytest.fixture
def home(tmp_path, monkeypatch):
    cfg = tmp_path / "a" / "Understeer"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(config, "GAMES_FILE", cfg / "settings" / "games.json")
    monkeypatch.setattr(config, "APP_SETTINGS_FILE", cfg / "settings" / "app.json")
    monkeypatch.setattr(config, "SLOT_NAMES_FILE", cfg / "settings" / "slot_names.json")
    monkeypatch.setattr(config, "CUSTOM_COVER_DIR", cfg / "covers")
    return tmp_path


def move_to(monkeypatch, tmp_path, name):
    """Point the config at another (empty) computer."""
    cfg = tmp_path / name / "Understeer"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(config, "GAMES_FILE", cfg / "settings" / "games.json")
    monkeypatch.setattr(config, "APP_SETTINGS_FILE", cfg / "settings" / "app.json")
    monkeypatch.setattr(config, "SLOT_NAMES_FILE", cfg / "settings" / "slot_names.json")
    monkeypatch.setattr(config, "CUSTOM_COVER_DIR", cfg / "covers")


def test_round_trip(home, monkeypatch, tmp_path):
    config.write_preset(config.preset_path("Drift"), "Drift", {"FF": 55})
    game = {"game": "steam:284160", "name": "BeamNG.drive",
            "preset": "Drift.json", "auto": True}
    pic = tmp_path / "pic.png"
    pic.write_bytes(b"png")
    config.set_game_cover(game, pic)
    config.save_games([game])
    config.set_setting("language", "de")
    config.save_slot_name("0EB7:0020", 1, "A")

    counts = backup.export(tmp_path / "out.zip")
    assert counts == {"presets": 1, "games": 1, "covers": 1}

    move_to(monkeypatch, tmp_path, "b")
    found = backup.read(tmp_path / "out.zip")
    assert backup.summary(found) == counts
    backup.import_(found)
    assert config.list_presets() == [("Drift", config.CONFIG_DIR / "Drift.json")]
    assert config.load_games()[0]["preset"] == "Drift.json"
    assert config.game_cover_path(config.load_games()[0]).read_bytes() == b"png"
    assert config.get_setting("language") == "de"
    assert config.load_slot_names("0EB7:0020") == {1: "A"}


def test_import_adds_to_what_is_there(home, monkeypatch, tmp_path):
    config.write_preset(config.preset_path("Drift"), "Drift", {"FF": 55})
    config.save_games([{"game": "steam:1", "name": "New name", "preset": None,
                        "auto": True}])
    config.set_setting("language", "de")
    backup.export(tmp_path / "out.zip")

    move_to(monkeypatch, tmp_path, "b")
    config.write_preset(config.preset_path("Drift"), "Drift", {"FF": 10})
    config.write_preset(config.preset_path("Rally"), "Rally", {"FF": 80})
    config.save_games([{"game": "steam:1", "name": "Old", "preset": None, "auto": True},
                       {"game": "steam:2", "name": "Other", "preset": None, "auto": True}])
    config.set_setting("layout", "compact")
    backup.import_(backup.read(tmp_path / "out.zip"))

    assert [n for n, _p in config.list_presets()] == ["Drift", "Rally"]
    assert config.read_preset(config.preset_path("Drift"))[1] == {"FF": 55}
    assert sorted((g["game"], g["name"]) for g in config.load_games()) == [
        ("steam:1", "New name"), ("steam:2", "Other")]
    assert config.get_setting("layout") == "compact"
    assert config.get_setting("language") == "de"


def test_refuses_other_files(home, tmp_path):
    (tmp_path / "x.zip").write_text("not a zip")
    with pytest.raises(backup.Error):
        backup.read(tmp_path / "x.zip")
    with zipfile.ZipFile(tmp_path / "y.zip", "w") as z:
        z.writestr("presets/a.json", "{}")
    with pytest.raises(backup.Error):
        backup.read(tmp_path / "y.zip")
    with zipfile.ZipFile(tmp_path / "z.zip", "w") as z:
        z.writestr(backup.MANIFEST, json.dumps({"format": 99}))
    with pytest.raises(backup.Error):
        backup.read(tmp_path / "z.zip")


def test_skips_unsafe_paths(home, tmp_path):
    with zipfile.ZipFile(tmp_path / "evil.zip", "w") as z:
        z.writestr(backup.MANIFEST, json.dumps({"format": 1}))
        z.writestr("presets/../../evil.json", "{}")
        z.writestr("covers/../x", "x")
        z.writestr("settings/other.json", "{}")
        z.writestr("presets/ok.json", json.dumps({"name": "Ok", "values": {}}))
    found = backup.read(tmp_path / "evil.zip")
    assert list(found["presets"]) == ["ok.json"]
    assert found["covers"] == {} and found["settings"] == {}
