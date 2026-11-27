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


# -- U-006: update and beta state stays on this computer -----------------------
LOCAL = {"update_check": True, "beta_notify": True, "update_checked": 1.0,
         "beta_checked": 2.0, "beta_handled": "9.0.0", "beta_notified": "9.1.0-beta.1",
         "last_version": "9.0.0", "beta_latest": {"version": "9.1.0-beta.1"},
         "update_latest": {"version": "99.0.0", "url": "https://evil.example/",
                           "asset": {"url": "https://evil.example/x.AppImage",
                                     "sha256": "0" * 64, "size": 5}}}


def evil_backup(path, settings):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(backup.MANIFEST, json.dumps({"format": 1, "version": "2.3.3"}))
        z.writestr("settings/app.json", json.dumps(settings))
        z.writestr("presets/ok.json", json.dumps({"name": "Ok", "values": {"FF": 5}}))


def test_an_imported_backup_brings_no_update_data(home, tmp_path):
    evil_backup(tmp_path / "evil.zip", {**LOCAL, "language": "de"})
    backup.import_(backup.read(tmp_path / "evil.zip"))
    for key in LOCAL:
        assert config.get_setting(key) is None, key   # missing stays missing
    assert config.get_setting("language") == "de"   # the rest is taken
    assert [n for n, _p in config.list_presets()] == ["Ok"]


def test_an_imported_backup_keeps_the_local_decisions(home, tmp_path):
    config.set_setting("update_check", False)
    config.set_setting("beta_notify", False)
    config.set_setting("last_version", "2.3.3")
    evil_backup(tmp_path / "evil.zip", LOCAL)
    backup.import_(backup.read(tmp_path / "evil.zip"))
    assert config.get_setting("update_check") is False
    assert config.get_setting("beta_notify") is False
    assert config.get_setting("last_version") == "2.3.3"
    assert config.get_setting("update_latest") is None


def test_an_export_holds_no_update_data(home, tmp_path):
    for key, value in {**LOCAL, "language": "de", "layout": "compact"}.items():
        config.set_setting(key, value)
    backup.export(tmp_path / "out.zip")
    with zipfile.ZipFile(tmp_path / "out.zip") as z:
        exported = json.loads(z.read("settings/app.json"))
    assert exported == {"language": "de", "layout": "compact"}
    assert config.get_setting("update_check") is True   # the export changed nothing here


def test_what_stays_on_this_computer():
    assert backup.LOCAL_ONLY_SETTINGS == {
        "update_check", "update_latest", "update_checked", "beta_notify",
        "beta_latest", "beta_checked", "beta_handled", "beta_notified",
        "last_version"}


def test_every_update_and_beta_setting_stays_on_this_computer():
    """A setting about updates or test versions added later must be added
    to LOCAL_ONLY_SETTINGS too (or this test changed on purpose)."""
    import pathlib
    import re
    code = pathlib.Path(backup.__file__).parent
    used = set()
    for source in code.rglob("*.py"):
        used.update(re.findall(
            r"""(?:get_setting|set_setting|get_text)\(\s*["']((?:update|beta)_\w+|last_version)["']""",
            source.read_text(encoding="utf-8")))
    assert used, "no setting found: the pattern is out of date"
    assert used <= backup.LOCAL_ONLY_SETTINGS, used - backup.LOCAL_ONLY_SETTINGS


def test_a_local_yes_stays_yes(home, tmp_path):
    config.set_setting("update_check", True)
    config.set_setting("beta_notify", True)
    evil_backup(tmp_path / "evil.zip", {"update_check": False, "beta_notify": False,
                                        "theme": "dark"})
    backup.import_(backup.read(tmp_path / "evil.zip"))
    assert config.get_setting("update_check") is True
    assert config.get_setting("beta_notify") is True
    assert config.get_setting("theme") == "dark"


DAMAGED = "Export: settings/app.json is damaged, left out"


@pytest.mark.parametrize("damaged", [b"{not json", b"[1, 2]", b"\xff\xfe"],
                         ids=["no-json", "no-settings", "no-utf8"])
def test_a_damaged_app_json_is_not_exported(home, monkeypatch, tmp_path, caplog,
                                            damaged):
    """Left out and said so; the rest of the export is whole and imports."""
    config.write_preset(config.preset_path("Drift"), "Drift", {"FF": 55})
    config.save_games([{"game": "steam:1", "name": "Game", "preset": "Drift.json",
                        "auto": True}])
    config.save_slot_name("0EB7:0020", 1, "A")
    config.APP_SETTINGS_FILE.write_bytes(damaged)
    with caplog.at_level("WARNING", logger="understeer.backup"):
        backup.export(tmp_path / "out.zip")
    assert DAMAGED in caplog.messages
    assert config.APP_SETTINGS_FILE.read_bytes() == damaged     # left as it was
    with zipfile.ZipFile(tmp_path / "out.zip") as z:
        names = z.namelist()
    assert "settings/app.json" not in names
    assert {"presets/Drift.json", "settings/games.json",
            "settings/slot_names.json"} <= set(names)

    move_to(monkeypatch, tmp_path, "b")
    found = backup.read(tmp_path / "out.zip")   # still an export to import
    assert backup.summary(found) == {"presets": 1, "games": 1, "covers": 0}
    backup.import_(found)
    assert config.read_preset(config.preset_path("Drift"))[1] == {"FF": 55}
    assert config.load_games()[0]["preset"] == "Drift.json"
    assert config.load_slot_names("0EB7:0020") == {1: "A"}
    assert not config.APP_SETTINGS_FILE.exists()


def test_no_app_json_is_no_damage(home, tmp_path, caplog):
    config.write_preset(config.preset_path("Drift"), "Drift", {"FF": 55})
    assert not config.APP_SETTINGS_FILE.exists()
    with caplog.at_level("WARNING", logger="understeer.backup"):
        backup.export(tmp_path / "out.zip")
    assert DAMAGED not in caplog.messages
    with zipfile.ZipFile(tmp_path / "out.zip") as z:
        assert "settings/app.json" not in z.namelist()
