import struct
from unittest import mock

import pytest

from understeer import config, games


def jpeg(width, height):
    """Smallest file our header reader accepts as a JPEG of that size."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + bytes(9)
    sof = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, height, width) + bytes(12)
    return b"\xff\xd8" + app0 + sof


def png(width, height):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + \
        struct.pack(">II", width, height) + bytes(5)


def acf(appid, name):
    return f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n}}\n'


@pytest.fixture
def steam(tmp_path, monkeypatch):
    """A Steam installation in ~/.local/share/Steam plus a second library."""
    root = tmp_path / "home" / ".local" / "share" / "Steam"
    (root / "steamapps").mkdir(parents=True)
    extra = tmp_path / "games-disk" / "SteamLibrary"
    (extra / "steamapps").mkdir(parents=True)
    (root / "steamapps" / "libraryfolders.vdf").write_text(f'''
"libraryfolders"
{{
    "0"
    {{
        "path"      "{root}"
        "apps"      {{ "805550" "123" }}
    }}
    "1"
    {{
        "path"      "{extra}"
    }}
}}''')
    monkeypatch.setattr(games, "STEAM_ROOTS", [
        root,
        tmp_path / "home" / ".steam" / "steam",                    # missing
        tmp_path / "home" / ".var" / "app" / "com.valvesoftware.Steam" / "Steam",
    ])
    return root, extra


# -- Steam files ----------------------------------------------------------
def test_parse_vdf():
    data = games.parse_vdf('"A" { "b" "1" "c" { "d" "x \\"y\\"" } } "e" "2"')
    assert data == {"A": {"b": "1", "c": {"d": 'x "y"'}}, "e": "2"}


def test_steam_roots_skip_missing_and_duplicates(steam, tmp_path, monkeypatch):
    root, _extra = steam
    link = tmp_path / "home" / ".steam" / "steam"
    link.parent.mkdir(parents=True)
    link.symlink_to(root)                       # Debian/Ubuntu style link
    monkeypatch.setattr(games, "STEAM_ROOTS", [root, link])
    assert games.steam_roots() == [root.resolve()]


def test_library_folders(steam):
    root, extra = steam
    assert games.library_folders(root) == [root, extra]


def test_library_folders_old_format(tmp_path):
    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        '"LibraryFolders"\n{\n\t"TimeNextStatsReport"\t"123"\n'
        '\t"1"\t"/mnt/games/SteamLibrary"\n}\n')
    assert [str(p) for p in games.library_folders(root)] == [
        str(root), "/mnt/games/SteamLibrary"]


def test_installed_games_from_all_libraries(steam):
    root, extra = steam
    (root / "steamapps" / "appmanifest_805550.acf").write_text(
        acf(805550, "Assetto Corsa Competizione"))
    (root / "steamapps" / "appmanifest_228980.acf").write_text(
        acf(228980, "Steamworks Common Redistributables"))
    (extra / "steamapps" / "appmanifest_2399420.acf").write_text(
        acf(2399420, "Le Mans Ultimate"))
    (extra / "steamapps" / "appmanifest_2805730.acf").write_text(
        acf(2805730, "Proton 9.0"))
    (extra / "steamapps" / "appmanifest_1628350.acf").write_text(
        acf(1628350, "Steam Linux Runtime 3.0 (sniper)"))
    assert games.installed_games() == [("805550", "Assetto Corsa Competizione"),
                                       ("2399420", "Le Mans Ultimate")]


def test_no_steam(tmp_path, monkeypatch):
    monkeypatch.setattr(games, "STEAM_ROOTS", [tmp_path / "nothing"])
    assert games.installed_games() == []
    assert games.find_cover("805550") is None


# -- covers ---------------------------------------------------------------
def test_image_size(tmp_path):
    (tmp_path / "a.jpg").write_bytes(jpeg(600, 900))
    (tmp_path / "b.png").write_bytes(png(460, 215))
    (tmp_path / "c.txt").write_text("hello")
    assert games.image_size(tmp_path / "a.jpg") == (600, 900)
    assert games.image_size(tmp_path / "b.png") == (460, 215)
    assert games.image_size(tmp_path / "c.txt") is None
    assert games.image_size(tmp_path / "missing.jpg") is None


def test_cover_new_layout_picks_portrait(steam):
    root, _extra = steam
    folder = root / "appcache" / "librarycache" / "805550"
    folder.mkdir(parents=True)
    (folder / "header.jpg").write_bytes(jpeg(460, 215))
    (folder / "bd2b709a08caf7286f73c51d3b3937667f7aa053.jpg").write_bytes(jpeg(600, 900))
    (folder / "0a1b2c.jpg").write_bytes(jpeg(1920, 620))       # hero image
    assert games.find_cover("805550").name == "bd2b709a08caf7286f73c51d3b3937667f7aa053.jpg"


def test_cover_new_layout_only_header(steam):
    root, _extra = steam
    folder = root / "appcache" / "librarycache" / "1161040"
    folder.mkdir(parents=True)
    (folder / "header.jpg").write_bytes(jpeg(460, 215))
    assert games.find_cover("1161040") == folder / "header.jpg"


def test_cover_old_flat_layout(steam):
    root, _extra = steam
    cache = root / "appcache" / "librarycache"
    cache.mkdir(parents=True)
    (cache / "805550_header.jpg").write_bytes(jpeg(460, 215))
    (cache / "805550_library_600x900.jpg").write_bytes(jpeg(600, 900))
    assert games.find_cover("805550") == cache / "805550_library_600x900.jpg"


def test_downloaded_cover_comes_first(steam, tmp_path):
    root, _extra = steam
    folder = root / "appcache" / "librarycache" / "805550"
    folder.mkdir(parents=True)
    (folder / "header.jpg").write_bytes(jpeg(460, 215))
    own = tmp_path / "covers"
    own.mkdir()
    (own / "steam_805550.jpg").write_bytes(jpeg(600, 900))
    assert games.find_cover("805550", own) == own / "steam_805550.jpg"


class FakeResponse:
    def __init__(self, status, content_type, body):
        self.status, self.body = status, body
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_cover(tmp_path):
    def urlopen(req, timeout):
        assert "/805550/library_600x900.jpg" in req.full_url
        return FakeResponse(200, "image/jpeg", jpeg(600, 900))
    with mock.patch.object(games.urllib.request, "urlopen", urlopen):
        path = games.download_cover("805550", tmp_path / "covers")
    assert path == tmp_path / "covers" / "steam_805550.jpg"
    assert games.image_size(path) == (600, 900)


def test_download_falls_back_to_header(tmp_path):
    asked = []

    def urlopen(req, timeout):
        asked.append(req.full_url)
        if "library_600x900" in req.full_url:
            raise games.urllib.error.HTTPError(req.full_url, 404, "", {}, None)
        return FakeResponse(200, "image/jpeg", jpeg(460, 215))
    with mock.patch.object(games.urllib.request, "urlopen", urlopen):
        assert games.download_cover("1161040", tmp_path) is not None
    assert len(asked) == 3          # both servers for the portrait, then header


def test_download_without_cover(tmp_path):
    with mock.patch.object(games.urllib.request, "urlopen",
                           lambda req, timeout: FakeResponse(200, "text/html", b"<html>")):
        assert games.download_cover("1", tmp_path) is None
    assert not list(tmp_path.iterdir())


# -- running games --------------------------------------------------------
def process(proc, pid, environ=b"", cmdline=b"", comm=""):
    d = proc / str(pid)
    d.mkdir(parents=True)
    (d / "environ").write_bytes(environ)
    (d / "cmdline").write_bytes(cmdline)
    (d / "comm").write_text(comm + "\n")


def test_running_games(tmp_path):
    proc = tmp_path / "proc"
    process(proc, 100, b"HOME=/home/a\0SteamAppId=805550\0",
            b"Z:\\steam\\AC2-Win64-Shipping.exe\0-dx12\0", "AC2-Win64-Shipp")
    process(proc, 200, b"HOME=/home/a\0", b"/opt/BeamNG/BeamNG.drive.x64\0",
            "BeamNG.drive.x6")
    process(proc, 300, b"SteamAppId=0\0", b"steam\0", "steam")
    (proc / "self").mkdir()
    running = games.running_games(proc)
    assert "steam:805550" in running
    assert "steam:0" not in running
    assert "process:ac2-win64-shipping" in running
    assert "process:beamng.drive.x64" in running


def test_other_users_processes_are_skipped(tmp_path):
    proc = tmp_path / "proc"
    (proc / "1").mkdir(parents=True)            # no readable environ
    assert games.running_games(proc) == set()


@pytest.mark.parametrize("name, key", [
    ("AC2-Win64-Shipping.exe", "ac2-win64-shipping"),
    ("Z:\\Games\\rFactor2.EXE", "rfactor2"),
    ("/usr/bin/BeamNG.drive.x64", "beamng.drive.x64"),
    ("  ", ""),
])
def test_program_key(name, key):
    assert games.program_key(name) == key


# -- stored games ---------------------------------------------------------
@pytest.fixture
def home(tmp_path, monkeypatch):
    cfg = tmp_path / ".config" / "Understeer"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(config, "GAMES_FILE", cfg / "settings" / "games.json")
    return cfg


def test_games_round_trip(home):
    assert config.load_games() == []
    games_list = [{"game": "steam:805550", "name": "ACC", "preset": "GT3.json",
                   "auto": True}]
    config.save_games(games_list)
    assert config.load_games() == games_list
    assert config.list_presets() == []          # not taken for a preset


def test_broken_games_file(home):
    (home / "settings").mkdir(parents=True)
    (home / "settings" / "games.json").write_text(
        '[{"game": "steam:1"}, {"name": "no id"}, "junk", {"game": 5}]')
    assert config.load_games() == [{"game": "steam:1", "name": "steam:1",
                                    "preset": None, "auto": True}]


def test_steamgriddb_id_kept(home):
    config.save_games([{"game": "process:game", "name": "Diablo II", "preset": None,
                        "auto": True, "steamgriddb": 5248},
                       {"game": "process:x", "name": "X", "steamgriddb": "5"},
                       {"game": "process:y", "name": "Y", "steamgriddb": True}])
    loaded = config.load_games()
    assert loaded[0]["steamgriddb"] == 5248
    assert "steamgriddb" not in loaded[1] and "steamgriddb" not in loaded[2]


def test_game_preset_path(home):
    config.write_preset(home / "GT3.json", "GT3", {"FF": 70})
    assert config.game_preset_path({"preset": "GT3.json"}) == home / "GT3.json"
    assert config.game_preset_path({"preset": "Deleted.json"}) is None
    assert config.game_preset_path({"preset": None}) is None


# -- recognising other programs ----------------------------------------------
def full_process(proc, pid, cmdline, comm, exe=None, starttime=1, environ=b""):
    d = proc / str(pid)
    d.mkdir(parents=True)
    (d / "environ").write_bytes(environ)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in cmdline) + b"\0")
    (d / "comm").write_text(comm + "\n")
    (d / "stat").write_text(f"{pid} ({comm}) S " + " ".join(
        ["0"] * 18 + [str(starttime)] + ["0"] * 5))
    if exe:
        (d / "exe").symlink_to(exe)


def test_wine_game_found_by_exe_argument(tmp_path):
    proc = tmp_path / "proc"
    full_process(proc, 10, ["/usr/bin/wine64-preloader",
                            "C:\\Games\\Diablo II\\Game.exe", "-w"], "Game.exe")
    assert "process:game" in games.running_games(proc)


def test_script_found_behind_interpreter(tmp_path):
    proc = tmp_path / "proc"
    full_process(proc, 11, ["/bin/bash", "/opt/MyRacingGame/start-racing-game.sh"],
                 "start-racing-ga")
    assert "process:start-racing-game.sh" in games.running_games(proc)


def test_found_by_executed_file(tmp_path):
    proc = tmp_path / "proc"
    binary = tmp_path / "RealGame.x86_64"
    binary.write_text("")
    full_process(proc, 12, ["launcher"], "launcher", exe=binary)
    assert "process:realgame.x86_64" in games.running_games(proc)


def test_long_name_matches_cut_process_name():
    running = {"process:averyveryverylo"}
    assert games.is_running("process:averyveryverylongname", running)
    assert not games.is_running("process:shortname", running)
    assert games.is_running("steam:1", {"steam:1"})
    assert not games.is_running("steam:averyveryverylongname", running)


def test_running_programs_newest_first(tmp_path):
    proc = tmp_path / "proc"
    full_process(proc, 20, ["/usr/bin/gnome-shell"], "gnome-shell", starttime=5)
    full_process(proc, 21, ["Z:\\Games\\D2R.exe"], "D2R.exe", starttime=900)
    full_process(proc, 22, ["/usr/bin/gnome-shell", "--x"], "gnome-shell",
                 starttime=6)
    (proc / "23").mkdir()                       # kernel thread: no cmdline
    (proc / "23" / "environ").write_bytes(b"")
    (proc / "23" / "cmdline").write_bytes(b"")
    names = [name for name, _cmd in games.running_programs(proc)]
    assert names == ["D2R.exe", "gnome-shell"]


# -- own pictures -------------------------------------------------------------
def test_custom_cover(home, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CUSTOM_COVER_DIR", home / "covers")
    picture = tmp_path / "Diablo.PNG"
    picture.write_bytes(png(600, 900))
    game = {"game": "process:game", "name": "Diablo II", "preset": None,
            "auto": True}
    path = config.set_game_cover(game, picture)
    assert path.suffix == ".png" and path.parent == home / "covers"
    assert config.game_cover_path(game) == path
    config.save_games([game])
    assert config.load_games()[0]["cover"] == path.name
    # a new picture replaces the old copy
    other = tmp_path / "other.jpg"
    other.write_bytes(jpeg(600, 900))
    new_path = config.set_game_cover(game, other)
    assert not path.exists() and new_path.exists()
    config.remove_game_cover(game)
    assert "cover" not in game and not new_path.exists()


def test_old_cover_stays_if_the_new_one_cannot_be_saved(home, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CUSTOM_COVER_DIR", home / "covers")
    game = {"game": "process:game", "name": "Diablo II", "preset": None, "auto": True}
    first = tmp_path / "first.png"
    first.write_bytes(png(600, 900))
    path = config.set_game_cover(game, first)
    other = tmp_path / "other.jpg"
    other.write_bytes(jpeg(600, 900))

    def disk_full(_path, _data):
        raise OSError("disk full")
    monkeypatch.setattr(config, "write_file", disk_full)
    with pytest.raises(OSError):
        config.set_game_cover(game, other)
    assert config.game_cover_path(game) == path     # still the old picture


# -- starts and stops -----------------------------------------------------------
class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_start_reported_once():
    clock = Clock()
    watch = games.StartWatch(10, clock)
    assert watch.update({"steam:1"}) == ({"steam:1"}, set())
    for _ in range(5):                          # keeps running: nothing new
        clock.now += 2
        assert watch.update({"steam:1"}) == (set(), set())


def test_short_gap_is_no_new_start():
    clock = Clock()
    watch = games.StartWatch(10, clock)
    watch.update({"process:game"})
    clock.now += 2
    assert watch.update(set()) == (set(), set())        # launcher restarts it
    assert watch.running == {"process:game"}
    clock.now += 4
    assert watch.update({"process:game"}) == (set(), set())


def test_stop_after_grace_then_new_start():
    clock = Clock()
    watch = games.StartWatch(10, clock)
    watch.update({"steam:1"})
    clock.now += 2
    watch.update(set())
    clock.now += 9
    assert watch.update(set()) == (set(), set())        # 9 s gone
    clock.now += 1
    assert watch.update(set()) == (set(), {"steam:1"})  # 10 s gone: stopped
    assert watch.running == set()
    clock.now += 2
    assert watch.update({"steam:1"}) == ({"steam:1"}, set())


def test_games_tracked_separately():
    clock = Clock()
    watch = games.StartWatch(10, clock)
    watch.update({"steam:1"})
    clock.now += 2
    assert watch.update({"steam:1", "steam:2"}) == ({"steam:2"}, set())
    clock.now += 2
    watch.update({"steam:2"})
    clock.now += 10
    assert watch.update({"steam:2"}) == (set(), {"steam:1"})
