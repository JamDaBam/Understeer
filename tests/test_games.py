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


def test_game_preset_path(home):
    config.write_preset(home / "GT3.json", "GT3", {"FF": 70})
    assert config.game_preset_path({"preset": "GT3.json"}) == home / "GT3.json"
    assert config.game_preset_path({"preset": "Deleted.json"}) is None
    assert config.game_preset_path({"preset": None}) is None
