"""Steam games: installed games, their covers and which ones are running.

Works without GTK. Steam can be installed in several ways (distribution
package, Flatpak, Snap) and keep games in several libraries, so all of
them are searched.
"""
import pathlib
import re
import struct
import urllib.request

HOME = pathlib.Path.home()
STEAM_ROOTS = [
    HOME / ".local" / "share" / "Steam",            # most distributions
    HOME / ".steam" / "steam",                      # Debian/Ubuntu package
    HOME / ".steam" / "root",
    HOME / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
    HOME / "snap" / "steam" / "common" / ".local" / "share" / "Steam",
]
PROC = pathlib.Path("/proc")
CDN_URLS = [
    "https://shared.steamstatic.com/store_item_assets/steam/apps/{appid}/{file}",
    "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{file}",
]

# Steam installs these like games, but they aren't any.
TOOL_APPIDS = {"228980"}                            # Steamworks Common Redistributables
TOOL_NAMES = re.compile(r"^(Proton\b|Steam Linux Runtime|Steamworks Common|"
                        r"SteamVR|Steam Audio)", re.IGNORECASE)


def steam_id(appid):
    return f"steam:{appid}"


# ---------------------------------------------------------------------------
# Steam's text files (VDF / ACF)
# ---------------------------------------------------------------------------
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text):
    """Parse Valve's KeyValues text format into nested dicts."""
    root = {}
    stack = [root]
    key = None
    for m in _TOKEN.finditer(text):
        quoted, brace = m.groups()
        if brace == "{":
            child = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = quoted.replace('\\"', '"').replace("\\\\", "\\")
        else:
            stack[-1][key] = quoted.replace('\\"', '"').replace("\\\\", "\\")
            key = None
    return root


def _read_vdf(path):
    try:
        return parse_vdf(pathlib.Path(path).read_text(encoding="utf-8",
                                                     errors="replace"))
    except OSError:
        return {}


# ---------------------------------------------------------------------------
# Installed games
# ---------------------------------------------------------------------------
def steam_roots():
    """Existing Steam installations, each listed once."""
    found = []
    for root in STEAM_ROOTS:
        try:
            real = root.resolve()
        except OSError:
            continue
        if (real / "steamapps").is_dir() and real not in found:
            found.append(real)
    return found


def library_folders(root):
    """Steam library folders of one installation, the installation first."""
    folders = [root]
    data = _read_vdf(root / "steamapps" / "libraryfolders.vdf")
    entries = data.get("libraryfolders") or data.get("LibraryFolders") or {}
    for key, value in entries.items():
        if isinstance(value, dict):             # current format
            path = value.get("path")
        elif key.isdigit():                     # old format: "1" "/path"
            path = value
        else:
            continue
        if path:
            folder = pathlib.Path(path)
            if folder not in folders:
                folders.append(folder)
    return folders


def is_tool(appid, name):
    return appid in TOOL_APPIDS or bool(TOOL_NAMES.match(name or ""))


def installed_games():
    """[(appid, name)] of all installed Steam games, sorted by name."""
    games = {}
    for root in steam_roots():
        for folder in library_folders(root):
            for acf in sorted((folder / "steamapps").glob("appmanifest_*.acf")):
                state = _read_vdf(acf).get("AppState", {})
                appid, name = state.get("appid"), state.get("name")
                if appid and name and not is_tool(appid, name):
                    games.setdefault(appid, name)
    return sorted(games.items(), key=lambda g: g[1].lower())


# ---------------------------------------------------------------------------
# Covers
# ---------------------------------------------------------------------------
def image_size(path):
    """(width, height) of a JPEG or PNG file, read from its header."""
    try:
        with open(path, "rb") as f:
            data = f.read(65536)
    except OSError:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    return None


def _best_image(paths):
    """Prefer a portrait image (the library cover), else any image."""
    sized = [(p, image_size(p)) for p in paths]
    portrait = [p for p, size in sized if size and size[1] > size[0]]
    if portrait:
        return portrait[0]
    landscape = [p for p, size in sized if size]
    return landscape[0] if landscape else None


def find_cover(appid, cache_dir=None):
    """Local cover image of a Steam game, or None.

    Looks in our own download cache and in Steam's library cache, which
    newer Steam versions keep in one folder per game with hashed file
    names and older ones as "<appid>_library_600x900.jpg".
    """
    if cache_dir is not None:
        own = pathlib.Path(cache_dir) / f"steam_{appid}.jpg"
        if own.is_file():
            return own
    for root in steam_roots():
        cache = root / "appcache" / "librarycache"
        named = [cache / f"{appid}_library_600x900.jpg"]
        folder = cache / str(appid)
        if folder.is_dir():
            named.append(folder / "library_600x900.jpg")
            for p in sorted(folder.rglob("*")):
                if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p not in named:
                    named.append(p)
        named.append(cache / f"{appid}_header.jpg")
        best = _best_image([p for p in named if p.is_file()])
        if best:
            return best
    return None


def download_cover(appid, cache_dir, timeout=10):
    """Fetch the cover from Steam's servers into cache_dir; returns the path.

    Only called when the user turned on cover downloads.
    """
    dest = pathlib.Path(cache_dir) / f"steam_{appid}.jpg"
    for file in ("library_600x900.jpg", "header.jpg"):
        for url in CDN_URLS:
            try:
                req = urllib.request.Request(url.format(appid=appid, file=file),
                                             headers={"User-Agent": "Understeer"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status != 200 or not resp.headers.get(
                            "Content-Type", "").startswith("image/"):
                        continue
                    data = resp.read()
            except (OSError, ValueError):
                continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(dest)
            except OSError:
                return None
            return dest
    return None


# ---------------------------------------------------------------------------
# Running games
# ---------------------------------------------------------------------------
def program_key(name):
    """Normalised program name: "AC2-Win64-Shipping.exe" -> "ac2-win64-shipping"."""
    name = re.split(r"[\\/]", name.strip())[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def running_games(proc=None):
    """Ids of running games: "steam:<appid>" and "process:<program name>".

    Steam sets SteamAppId (or SteamGameId) for every game it starts, native
    or through Proton. Other games are recognised by their program name,
    taken from the command line (Proton games show their .exe there) and
    from the process name, which Linux cuts to 15 characters.
    Only processes of the current user can be read, which is all we need.
    """
    found = set()
    for pid_dir in (proc or PROC).iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            environ = (pid_dir / "environ").read_bytes()
        except OSError:
            continue
        for entry in environ.split(b"\0"):
            if entry.startswith((b"SteamAppId=", b"SteamGameId=")):
                appid = entry.split(b"=", 1)[1].decode(errors="replace")
                if appid.isdigit() and appid != "0":
                    found.add(steam_id(appid))
                    break
        names = []
        try:
            argv0 = (pid_dir / "cmdline").read_bytes().split(b"\0")[0]
            names.append(argv0.decode(errors="replace"))
            names.append((pid_dir / "comm").read_text())
        except OSError:
            pass
        for name in names:
            if program_key(name):
                found.add("process:" + program_key(name))
    return found
