import inspect
import io
import logging
import struct
import time
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

    def read1(self, limit):                    # as covers are read
        piece, self.body = self.body[:limit], self.body[limit:]
        return piece

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_cover(tmp_path):
    def urlopen(req, timeout):
        assert "/805550/library_600x900.jpg" in req.full_url
        return FakeResponse(200, "image/jpeg", jpeg(600, 900))
    with mock.patch.object(games.fetch, "open_https", urlopen):
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
    with mock.patch.object(games.fetch, "open_https", urlopen):
        assert games.download_cover("1161040", tmp_path) is not None
    assert len(asked) == 3          # both servers for the portrait, then header


def test_download_without_cover(tmp_path):
    with mock.patch.object(games.fetch, "open_https",
                           lambda req, timeout: FakeResponse(200, "text/html", b"<html>")):
        assert games.download_cover("1", tmp_path) is None
    assert not list(tmp_path.iterdir())


class ErrorBody(io.BytesIO):
    """The answer an HTTPError holds; fail: closing it raises that."""

    def __init__(self, fail=None):
        super().__init__(b"not found")
        self.fail = fail

    def close(self):
        super().close()
        if self.fail:
            raise self.fail


class ClosedResponse(FakeResponse):
    """Notes when the code is done with it."""

    def __init__(self, *args):
        super().__init__(*args)
        self.closed = False

    def __exit__(self, *exc):
        self.closed = True
        return False


@pytest.mark.parametrize("fail", [None, OSError("gone"),
                                  ValueError("I/O operation on closed file")])
def test_a_missing_portrait_is_closed_before_the_header(tmp_path, fail):
    raised, answered = [], []      # kept: only the code may close them

    def urlopen(req, timeout):
        if "library_600x900" in req.full_url:
            raised.append(games.urllib.error.HTTPError(
                req.full_url, 404, "Not Found", {}, ErrorBody(fail)))
            raise raised[-1]
        answered.append(ClosedResponse(200, "image/jpeg", jpeg(460, 215)))
        return answered[-1]
    with mock.patch.object(games.fetch, "open_https", urlopen):
        path = games.download_cover("1161040", tmp_path)
    assert path == tmp_path / "steam_1161040.jpg"
    assert games.image_size(path) == (460, 215)
    assert len(raised) == 2 and all(e.fp.closed for e in raised)
    assert len(answered) == 1 and answered[0].closed


@pytest.mark.parametrize("fail", [None, OSError("gone"),
                                  ValueError("I/O operation on closed file")])
def test_no_cover_anywhere_closes_every_answer(tmp_path, fail):
    raised = []

    def urlopen(req, timeout):
        raised.append(games.urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {}, ErrorBody(fail)))
        raise raised[-1]
    with mock.patch.object(games.fetch, "open_https", urlopen):
        assert games.download_cover("1", tmp_path) is None
    assert len(raised) == 4 and all(e.fp.closed for e in raised)
    assert not list(tmp_path.iterdir())



# -- broken answers, from a real server on 127.0.0.1 --------------------------
from test_fetch import Server  # noqa: E402

CUT_SHORT = (b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
             b"Content-Length: 1000\r\n\r\n" + b"j" * 100)
CHUNK_CUT_SHORT = (b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
                   b"Transfer-Encoding: chunked\r\n\r\n3e8\r\n" + b"j" * 100)
BAD_STATUS_LINE = b"NONSENSE\r\n\r\n"


@pytest.fixture
def local(monkeypatch):
    """A local server in place of Steam's two; its answers: server.answer."""
    s = Server()
    monkeypatch.setattr(games, "CDN_URLS",
                        [f"http://{s.address}/a/{{appid}}/{{file}}",
                         f"http://{s.address}/b/{{appid}}/{{file}}"])
    yield s
    s.close()


@pytest.mark.parametrize("broken", [CUT_SHORT, CHUNK_CUT_SHORT, BAD_STATUS_LINE],
                         ids=["cut-short", "chunk-cut-short", "bad-status-line"])
def test_a_broken_answer_is_no_cover(local, tmp_path, broken):
    """Each of the four tries gets a broken answer: no cover, nothing kept,
    and no http.client error escapes into the background task."""
    local.answer = lambda h: h.wfile.write(broken)
    assert games.download_cover("805550", tmp_path) is None
    assert len(local.requests) == 4
    assert not list(tmp_path.iterdir())
    assert local.answered.wait(5)


def test_a_broken_answer_is_followed_by_the_next_server(local, tmp_path):
    def answer(h):
        if h.path.startswith("/a/"):
            h.wfile.write(CUT_SHORT)            # the first server breaks off
        else:
            h.send_response(200)
            h.send_header("Content-Type", "image/jpeg")
            h.end_headers()                     # no length: read to the end
            h.wfile.write(jpeg(600, 900))
    local.answer = answer
    path = games.download_cover("805550", tmp_path)
    assert path == tmp_path / "steam_805550.jpg"
    assert games.image_size(path) == (600, 900)
    assert [p for p, _h in local.requests] == ["/a/805550/library_600x900.jpg",
                                              "/b/805550/library_600x900.jpg"]


def test_answers_that_are_no_picture_are_closed(tmp_path):
    answered = []

    def urlopen(req, timeout):
        answered.append(ClosedResponse(200, "text/html", b"<html>"))
        return answered[-1]
    with mock.patch.object(games.fetch, "open_https", urlopen):
        assert games.download_cover("1", tmp_path) is None
    assert len(answered) == 4 and all(r.closed for r in answered)


def test_another_error_while_closing_isnt_hidden(tmp_path):
    def urlopen(req, timeout):
        raise games.urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {}, ErrorBody(RuntimeError("bug")))
    with mock.patch.object(games.fetch, "open_https", urlopen), \
            pytest.raises(RuntimeError, match="bug"):
        games.download_cover("1", tmp_path)


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


# -- size and time of covers from Steam's servers ------------------------------
import test_fetch  # noqa: E402

answers = test_fetch.answers       # every HTTP answer opened, see there
COVER_MAX = 20 * 1024 * 1024
TRIES = ["/a/805550/library_600x900.jpg", "/b/805550/library_600x900.jpg",
         "/a/805550/header.jpg", "/b/805550/header.jpg"]


def cover(size):
    return b"\xff\xd8" + b"c" * (size - 2)


def send(data, length=True, content_type="image/jpeg", status=200):
    def answer(h):
        h.send_response(status)
        h.send_header("Content-Type", content_type)
        if length:
            h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)
    return answer


def send_chunked(*pieces):
    def answer(h):
        h.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
                      b"Transfer-Encoding: chunked\r\n\r\n")
        for piece in pieces:
            h.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
        h.wfile.write(b"0\r\n\r\n")
    return answer


def trickle(data, every):
    def answer(h):
        h.send_response(200)
        h.send_header("Content-Type", "image/jpeg")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        for i in range(len(data)):
            h.wfile.write(data[i:i + 1])
            h.wfile.flush()
            time.sleep(every)
    return answer


def first_server_then(first, rest):
    """first answers the first server's requests, rest the others."""
    return lambda h: (first if h.path.startswith("/a/") else rest)(h)


def no_cover(local, tmp_path):
    """All four tries fail: None, nothing written, the tries in order."""
    assert games.download_cover("805550", tmp_path) is None
    assert [p for p, _h in local.requests] == TRIES
    assert not list(tmp_path.iterdir())


def all_closed(answers):
    return bool(answers) and all(a.closed for a in answers)


def test_the_limits_of_covers():
    assert games.MAX_COVER == COVER_MAX == 20971520
    assert games.COVER_DEADLINE == 30
    assert inspect.signature(games.download_cover).parameters["timeout"].default == 10


@pytest.mark.parametrize("answer", [
    send(jpeg(600, 900)),
    send(jpeg(600, 900), length=False),
    send_chunked(jpeg(600, 900)[:10], jpeg(600, 900)[10:]),
], ids=["length", "no-length", "chunked"])
def test_a_cover_is_read_saved_and_closed(local, tmp_path, answers, answer):
    local.answer = answer
    path = games.download_cover("805550", tmp_path)
    assert path == tmp_path / "steam_805550.jpg"
    assert path.read_bytes() == jpeg(600, 900)
    assert [p.name for p in tmp_path.iterdir()] == ["steam_805550.jpg"]   # no .part
    (path_asked, headers), = local.requests
    assert path_asked == TRIES[0] and "Authorization" not in headers
    assert all_closed(answers)


@pytest.mark.parametrize("length", [True, False], ids=["length", "no-length"])
def test_a_cover_of_exactly_the_limit_is_saved(local, tmp_path, length):
    local.answer = send(cover(COVER_MAX), length=length)
    path = games.download_cover("805550", tmp_path)
    assert path.read_bytes() == cover(COVER_MAX)


@pytest.mark.parametrize("answer", [
    send(cover(COVER_MAX + 1)),
    send(cover(COVER_MAX + 1), length=False),
    send_chunked(*[b"c" * 65536] * 320 + [b"c"]),
], ids=["announced", "unannounced", "chunked"])
def test_a_cover_too_large_is_no_cover(local, tmp_path, answers, answer):
    local.answer = answer
    no_cover(local, tmp_path)
    assert len(answers) == 4 and all_closed(answers)


class Counted(FakeResponse):
    """An answer that counts the bytes read from it, however read."""

    def __init__(self, body, length=None):
        super().__init__(200, "image/jpeg", body)
        if length is not None:
            self.headers["Content-Length"] = length
        self.read_bytes = 0

    def read(self, *limit):
        piece = super().read()
        self.body, self.read_bytes = b"", self.read_bytes + len(piece)
        return piece

    def read1(self, limit):
        piece = super().read1(limit)
        self.read_bytes += len(piece)
        return piece


@pytest.mark.parametrize("length, read", [
    (str(COVER_MAX + 1), 0),        # announced: refused before reading
    (None, COVER_MAX + 1),          # one byte past the limit tells it
], ids=["announced", "unannounced"])
def test_a_cover_too_large_is_not_read_to_its_end(tmp_path, length, read):
    made = []

    def urlopen(req, timeout):
        made.append(Counted(b"c" * (COVER_MAX + 5 * 1024 * 1024), length))
        return made[-1]
    with mock.patch.object(games.fetch, "open_https", urlopen):
        assert games.download_cover("805550", tmp_path) is None
    assert [a.read_bytes for a in made] == [read] * 4


def test_a_cover_too_large_on_the_first_server_comes_from_the_second(local, tmp_path):
    local.answer = first_server_then(send(cover(COVER_MAX + 1)), send(jpeg(600, 900)))
    path = games.download_cover("805550", tmp_path)
    assert path.read_bytes() == jpeg(600, 900)
    assert [p for p, _h in local.requests] == TRIES[:2]


def test_a_slow_cover_within_the_deadline_is_saved(local, tmp_path, monkeypatch):
    monkeypatch.setattr(games, "COVER_DEADLINE", 30)
    local.answer = trickle(cover(20), 0.01)
    assert games.download_cover("805550", tmp_path).read_bytes() == cover(20)


def test_a_trickling_cover_on_the_first_server_comes_from_the_second(
        local, tmp_path, monkeypatch, answers):
    """A byte each 0.2 s would take more than 7 s; the deadline of that try
    stops it after about 0.5 s (plus up to one read), then the next server
    is asked: the deadline counts for each try on its own."""
    monkeypatch.setattr(games, "COVER_DEADLINE", 0.5)
    local.answer = first_server_then(trickle(cover(40), 0.2), send(jpeg(600, 900)))
    start = time.monotonic()
    path = games.download_cover("805550", tmp_path)
    assert time.monotonic() - start < 4
    assert path.read_bytes() == jpeg(600, 900)
    assert [p for p, _h in local.requests] == TRIES[:2]
    assert all_closed(answers)


def test_trickling_covers_everywhere_are_no_cover(local, tmp_path, monkeypatch, answers):
    monkeypatch.setattr(games, "COVER_DEADLINE", 0.5)
    local.answer = trickle(cover(40), 0.2)
    start = time.monotonic()
    no_cover(local, tmp_path)
    assert time.monotonic() - start < 4 * 0.5 + 4       # not 4 x 8 s
    assert len(answers) == 4 and all_closed(answers)


@pytest.mark.parametrize("answer", [
    lambda h: h.wfile.write(CUT_SHORT), lambda h: h.wfile.write(CHUNK_CUT_SHORT),
    lambda h: h.wfile.write(BAD_STATUS_LINE), send(b"gone", status=404),
    send(b"error", status=500), send(b"<html>", content_type="text/html"),
], ids=["cut-short", "chunk-cut-short", "bad-status-line", "404", "500", "no-picture"])
def test_a_broken_cover_is_closed(local, tmp_path, answers, answer):
    local.answer = answer
    no_cover(local, tmp_path)
    assert all(a.closed for a in answers)


def test_failed_tries_log_no_credentials(local, tmp_path, caplog):
    local.answer = send(cover(COVER_MAX + 1))
    caplog.set_level(logging.DEBUG, logger="understeer.games")
    no_cover(local, tmp_path)
    assert caplog.records                              # the tries were logged
    assert all(r.levelno == logging.DEBUG for r in caplog.records)
    assert "Authorization" not in caplog.text and "Bearer" not in caplog.text
    assert all("Authorization" not in headers for _p, headers in local.requests)


# -- covers follow redirects to https:// only ----------------------------------
https_servers = test_fetch.https_servers   # https:// names on local servers
lookups = test_fetch.lookups               # host names looked up
SHARED, CLOUDFLARE = "shared.steamstatic.com", "cdn.cloudflare.steamstatic.com"
CDN_TRIES = [(SHARED, "/store_item_assets/steam/apps/805550/library_600x900.jpg"),
             (CLOUDFLARE, "/steam/apps/805550/library_600x900.jpg"),
             (SHARED, "/store_item_assets/steam/apps/805550/header.jpg"),
             (CLOUDFLARE, "/steam/apps/805550/header.jpg")]


def redirect(location):
    def answer(h):
        h.send_response(302)
        h.send_header("Location", location)
        h.send_header("Content-Length", "0")
        h.end_headers()
    return answer


def steam_servers(https_servers):
    """Local servers in place of Steam's two (the real CDN_URLS)."""
    return https_servers(SHARED), https_servers(CLOUDFLARE)


def asked(*servers):
    return sorted(((name, p) for name, s in servers for p, _h in s.requests),
                  key=lambda t: CDN_TRIES.index(t))


@pytest.fixture
def plain():
    """A local http:// server, without touching CDN_URLS (unlike local)."""
    s = Server()
    yield s
    s.close()


def test_a_cover_redirected_to_http_comes_from_the_next_server(
        https_servers, plain, tmp_path, answers, caplog):
    shared, cloudflare = steam_servers(https_servers)
    shared.answer = redirect(f"http://{plain.address}/x.jpg")
    cloudflare.answer = send(jpeg(600, 900))
    plain.answer = send(cover(100))
    caplog.set_level(logging.DEBUG, logger="understeer.games")
    path = games.download_cover("805550", tmp_path)
    assert path.read_bytes() == jpeg(600, 900)
    assert asked((SHARED, shared), (CLOUDFLARE, cloudflare)) == CDN_TRIES[:2]
    assert plain.requests == []                         # never asked over http
    assert "a redirect away from HTTPS" in caplog.text
    assert plain.address not in caplog.text
    assert all(r.levelno == logging.DEBUG for r in caplog.records)
    assert all_closed(answers)


@pytest.mark.parametrize("location", [
    "http://never-looked-up.invalid/x.jpg", "ftp://never-looked-up.invalid/x.jpg"])
def test_covers_redirected_away_from_https_everywhere_are_no_cover(
        https_servers, tmp_path, answers, lookups, location):
    shared, cloudflare = steam_servers(https_servers)
    shared.answer = cloudflare.answer = redirect(location)
    assert games.download_cover("805550", tmp_path) is None
    assert asked((SHARED, shared), (CLOUDFLARE, cloudflare)) == CDN_TRIES
    assert not list(tmp_path.iterdir())
    assert "never-looked-up.invalid" not in lookups
    assert len(answers) == 4 and all_closed(answers)
    assert all("Authorization" not in h
               for s in (shared, cloudflare) for _p, h in s.requests)


@pytest.mark.parametrize("location", ["https://images.example/x.jpg", "/elsewhere/x.jpg"],
                         ids=["other-host", "relative"])
def test_a_cover_follows_https_redirects(https_servers, tmp_path, answers, location):
    shared, cloudflare = steam_servers(https_servers)
    images = https_servers("images.example")
    shared.answer = lambda h: (send(jpeg(600, 900)) if h.path.startswith("/elsewhere")
                               else redirect(location))(h)
    images.answer = send(jpeg(600, 900))
    path = games.download_cover("805550", tmp_path)
    assert path.read_bytes() == jpeg(600, 900)
    assert cloudflare.requests == []
    assert all_closed(answers)


def test_a_cover_after_an_https_redirect_keeps_its_limit(https_servers, tmp_path, answers):
    shared, cloudflare = steam_servers(https_servers)
    images = https_servers("images.example")
    shared.answer = redirect("https://images.example/x.jpg")
    images.answer = send(cover(COVER_MAX + 1), length=False)
    cloudflare.answer = send(jpeg(600, 900))
    path = games.download_cover("805550", tmp_path)
    assert path.read_bytes() == jpeg(600, 900)          # from the next try
    assert asked((SHARED, shared), (CLOUDFLARE, cloudflare)) == CDN_TRIES[:2]
    assert all_closed(answers)


def test_a_cover_after_an_https_redirect_keeps_its_deadline(
        https_servers, tmp_path, monkeypatch, answers):
    monkeypatch.setattr(games, "COVER_DEADLINE", 0.5)
    shared, cloudflare = steam_servers(https_servers)
    images = https_servers("images.example")
    shared.answer = redirect("https://images.example/x.jpg")
    images.answer = trickle(cover(40), 0.2)
    cloudflare.answer = send(jpeg(600, 900))
    start = time.monotonic()
    path = games.download_cover("805550", tmp_path)
    assert time.monotonic() - start < 4
    assert path.read_bytes() == jpeg(600, 900)
    assert all_closed(answers)
    assert images.answered.wait(10)
