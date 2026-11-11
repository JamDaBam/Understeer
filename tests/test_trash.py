import json

import pytest

from understeer import config, trash


@pytest.fixture
def home(tmp_path, monkeypatch):
    cfg = tmp_path / ".config" / "Understeer"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(config, "APP_SETTINGS_FILE", cfg / "settings" / "app.json")
    monkeypatch.setattr(config, "GAMES_FILE", cfg / "settings" / "games.json")
    monkeypatch.setattr(config, "CUSTOM_COVER_DIR", cfg / "covers")
    return cfg


def test_preset_out_and_back(home):
    path = config.preset_path("Drift")
    config.write_preset(path, "Drift", {"FF": 55}, "BeamNG")
    before = path.read_bytes()
    item = trash.put_preset(path)
    assert not path.exists()
    assert config.list_presets() == []               # the bin is no preset
    [entry] = trash.items()
    assert (entry["id"], entry["kind"], entry["name"], entry["folder"]) == \
        (item, "preset", "Drift", "BeamNG")
    config.delete_folder("BeamNG")
    assert trash.restore_preset(item) == path
    assert path.read_bytes() == before and trash.items() == []
    assert config.list_folders() == ["BeamNG"]       # its folder is back


def test_preset_back_under_a_free_name(home):
    path = config.preset_path("Drift")
    config.write_preset(path, "Drift", {"FF": 55})
    item = trash.put_preset(path)
    config.write_preset(path, "Drift", {"FF": 90})   # a new one meanwhile
    back = trash.restore_preset(item)
    assert back == config.preset_path("Drift (2)")
    assert config.read_preset(back) == ("Drift (2)", {"FF": 55})
    assert config.read_preset(path) == ("Drift", {"FF": 90})


def test_game_out_and_back_with_its_picture(home, tmp_path):
    picture = tmp_path / "pic.png"
    picture.write_bytes(b"png")
    game = {"game": "steam:1", "name": "BeamNG.drive", "preset": "Drift.json",
            "auto": False}
    config.set_game_cover(game, picture)
    cover = config.game_cover_path(game)
    item = trash.put_game(game)
    assert not cover.exists()
    assert [(i["kind"], i["name"]) for i in trash.items()] == [("game", "BeamNG.drive")]
    back = trash.restore_game(item, [])
    assert back == game and cover.read_bytes() == b"png"
    assert trash.items() == []


def test_game_there_already(home):
    game = {"game": "steam:1", "name": "BeamNG.drive", "preset": None, "auto": True}
    item = trash.put_game(game)
    with pytest.raises(trash.Error):
        trash.restore_game(item, [dict(game)])
    assert len(trash.items()) == 1                   # still in the bin


def test_expire_remove_and_empty(home):
    paths = [config.preset_path(n) for n in ("A", "B", "C")]
    for p in paths:
        config.write_preset(p, p.stem, {"FF": 50})
    old, new, other = (trash.put_preset(p) for p in paths)
    data = json.loads((home / "trash" / f"{old}.json").read_text())
    data["deleted"] -= 31 * 86400
    (home / "trash" / f"{old}.json").write_text(json.dumps(data))
    assert trash.expire() == 1
    assert {i["id"] for i in trash.items()} == {new, other}
    trash.remove(new)
    assert [i["id"] for i in trash.items()] == [other]
    trash.empty()
    assert trash.items() == []
    with pytest.raises(trash.Error):
        trash.restore_preset(other)


def test_broken_items_are_skipped(home):
    (home / "trash").mkdir(parents=True)
    (home / "trash" / "x.json").write_text("{nope")
    (home / "trash" / "y.json").write_text(json.dumps({"kind": "other"}))
    assert trash.items() == []
