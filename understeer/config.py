"""Presets and app settings in ~/.config/Understeer, without depending on GTK.

Every *.json directly in CONFIG_DIR is a preset:
    {"name": "BeamNG Drift", "folder": "BeamNG", "values": {"FF": 55, ...}}
"folder" is optional: presets can be grouped in folders, one level deep.
The folder is only a field in the file, so games keep their presets by
file name and older versions just show all presets without folders.
"""
import hashlib
import json
import logging
import os
import pathlib
import re

CONFIG_DIR  = pathlib.Path.home() / ".config" / "Understeer"
# Used by SimPower and by Understeer up to v1.0.4, when it still had that name
OLD_CONFIG_DIR = pathlib.Path.home() / ".config" / "SimPower"
# Kept in a subfolder: every *.json directly in CONFIG_DIR is a preset.
AXES_FILE   = CONFIG_DIR / "settings" / "axes.json"
SLOT_NAMES_FILE = CONFIG_DIR / "settings" / "slot_names.json"
SLOT_COUNTS_FILE = CONFIG_DIR / "settings" / "slot_counts.json"
APP_SETTINGS_FILE = CONFIG_DIR / "settings" / "app.json"
GAMES_FILE = CONFIG_DIR / "settings" / "games.json"
# Downloaded game covers can be fetched again, so they go to the cache.
COVER_DIR = (pathlib.Path(os.environ.get("XDG_CACHE_HOME") or
                          pathlib.Path.home() / ".cache") / "Understeer" / "covers")
# Pictures the user picked for a game can't be fetched again: config, not cache.
CUSTOM_COVER_DIR = CONFIG_DIR / "covers"

log = logging.getLogger("understeer.config")


def write_file(path, data):
    """Write text or bytes to path completely or not at all; raises OSError.

    The data goes to a .part file first, which then replaces the old file,
    so a crash or a full disk never leaves a half-written file behind.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        if isinstance(data, str):
            tmp.write_text(data, encoding="utf-8")
        else:
            tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def load_json(path, default=None):
    """What the JSON file at path holds (read as UTF-8, as written), or
    default if it is missing or damaged."""
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):       # also JSONDecodeError, UnicodeDecodeError
        return default


def _read_json(path):
    """The dict in the JSON file at path, or {}."""
    data = load_json(path)
    return data if isinstance(data, dict) else {}


def _save_json(path, data, sort_keys=False):
    """Store data as JSON; a failure is logged, the app goes on."""
    try:
        write_file(path, json.dumps(data, indent=2, sort_keys=sort_keys))
    except OSError as e:
        log.warning("Could not save %s: %s", path, e)


def migrate_config():
    """Take over presets and settings from the old SimPower folder once."""
    if OLD_CONFIG_DIR.is_dir() and not CONFIG_DIR.exists():
        try:
            OLD_CONFIG_DIR.rename(CONFIG_DIR)
        except OSError:
            pass


def load_axis_roles():
    return _read_json(AXES_FILE)


def save_axis_roles(roles):
    _save_json(AXES_FILE, roles)


def load_slot_names(model):
    """{slot number: name} the user gave the slots of this wheel base model."""
    names = _read_json(SLOT_NAMES_FILE).get(model)
    if not isinstance(names, dict):
        return {}
    return {int(k): v.strip() for k, v in names.items()
            if k.isdigit() and isinstance(v, str) and v.strip()}


def save_slot_name(model, slot, name):
    """Remember a slot name for this model; an empty name removes it."""
    data = _read_json(SLOT_NAMES_FILE)
    names = data.get(model)
    names = names if isinstance(names, dict) else {}
    if name.strip():
        names[str(slot)] = name.strip()
    else:
        names.pop(str(slot), None)
    if names:
        data[model] = names
    else:
        data.pop(model, None)
    _save_json(SLOT_NAMES_FILE, data, sort_keys=True)


def load_slot_count(model):
    """Highest slot number seen on this wheel base model, 0 if unknown."""
    count = _read_json(SLOT_COUNTS_FILE).get(model)
    return count if isinstance(count, int) and count > 0 else 0


def save_slot_count(model, count):
    data = _read_json(SLOT_COUNTS_FILE)
    data[model] = count
    _save_json(SLOT_COUNTS_FILE, data, sort_keys=True)


def get_setting(key, default=None):
    """App setting such as "language" or "layout" from settings/app.json."""
    value = _read_json(APP_SETTINGS_FILE).get(key)
    return default if value is None else value


def set_setting(key, value):
    """Store an app setting; None removes it."""
    data = _read_json(APP_SETTINGS_FILE)
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    _save_json(APP_SETTINGS_FILE, data, sort_keys=True)


def _file_name(name):
    """name if it is a plain file name, else None. Keeps a games.json from
    an import pointing outside the folders of presets and pictures."""
    if isinstance(name, str) and name not in ("", ".", "..") and \
            "/" not in name and "\0" not in name:
        return name
    return None


def load_games():
    """Games with their preset, e.g.
    [{"game": "steam:284160", "name": "BeamNG.drive",
      "preset": "BeamNG Drift.json", "auto": true}]
    "preset" is the file name of a preset in CONFIG_DIR, or None.
    "cover" (optional) is the file name of a picture the user chose, in
    CUSTOM_COVER_DIR; "steamgriddb" (optional) the game's SteamGridDB id.
    """
    data = load_json(GAMES_FILE)
    if not isinstance(data, list):
        return []
    games = []
    for g in data:
        if isinstance(g, dict) and isinstance(g.get("game"), str):
            games.append({"game": g["game"],
                          "name": str(g.get("name") or g["game"]),
                          "preset": _file_name(g.get("preset")),
                          "auto": bool(g.get("auto", True))})
            cover = _file_name(g.get("cover"))
            if cover:
                games[-1]["cover"] = cover
            sgdb = g.get("steamgriddb")
            if isinstance(sgdb, int) and not isinstance(sgdb, bool) and sgdb > 0:
                games[-1]["steamgriddb"] = sgdb
    return games


def save_games(games):
    _save_json(GAMES_FILE, games)


def game_cover_path(game):
    """Picture the user chose for a game, or None."""
    name = game.get("cover")
    path = CUSTOM_COVER_DIR / name if name else None
    return path if path is not None and path.is_file() else None


def set_game_cover(game, source):
    """Copy the picture at source for game; returns its path. Raises OSError."""
    suffix = pathlib.Path(source).suffix.lower() or ".img"
    name = hashlib.sha1(game["game"].encode()).hexdigest()[:16] + suffix
    data = pathlib.Path(source).read_bytes()
    remove_game_cover(game)
    write_file(CUSTOM_COVER_DIR / name, data)
    game["cover"] = name
    return CUSTOM_COVER_DIR / name


def remove_game_cover(game):
    """Forget the picture the user chose for a game and delete its copy."""
    path = game_cover_path(game)
    game.pop("cover", None)
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass


def game_preset_path(game):
    """Path of the preset a game uses, or None if unset or deleted."""
    name = game.get("preset")
    path = CONFIG_DIR / name if name else None
    return path if path is not None and path.is_file() else None


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
MAX_FOLDER = 80             # longer folder names are cut
KEEP = object()            # write_preset(): the folder stays


def clean_folder(folder):
    """A folder name as stored: one line of printable characters, or None
    for no folder."""
    if not isinstance(folder, str):
        return None
    folder = "".join(c for c in " ".join(folder.split()) if c.isprintable())
    return folder[:MAX_FOLDER].strip() or None


def _scan():
    """[(name, path, folder)] of all presets, sorted by file name."""
    if not CONFIG_DIR.exists():
        return []
    result = []
    for f in sorted(CONFIG_DIR.glob("*.json")):
        data = _read_json(f)
        name = data.get("name") if isinstance(data.get("name"), str) else f.stem
        result.append((name, f, clean_folder(data.get("folder"))))
    return result


def list_presets():
    """[(name, path)] of all presets, sorted by file name."""
    return [(name, path) for name, path, _folder in _scan()]


def _kept_folders():
    """Folders the user made, kept also while empty (setting
    "preset_folders")."""
    kept = get_setting("preset_folders", [])
    if not isinstance(kept, list):
        return []
    names, seen = [], set()
    for folder in map(clean_folder, kept):
        if folder is not None and folder.casefold() not in seen:
            seen.add(folder.casefold())
            names.append(folder)
    return names


def _keep_folders(names):
    set_setting("preset_folders", sorted(names, key=str.casefold) or None)


def add_folder(folder, respell=True):
    """Keep folder, also while no preset is in it. Returns its name as
    stored: with respell, an existing folder's spelling. None: no name."""
    folder = existing_folder(folder) if respell else clean_folder(folder)
    if folder is not None:
        kept = _kept_folders()
        if folder.casefold() not in {k.casefold() for k in kept}:
            _keep_folders(kept + [folder])
    return folder


def list_presets_by_folder(empty=False):
    """[(folder, [(name, path)])]: first the presets without a folder
    (folder None, left out if there are none), then each folder by name;
    with empty, also the kept folders without presets."""
    groups, spelled = {}, {}        # "BeamNG" and "beamng" are one folder
    for name, path, folder in _scan():
        key = folder.casefold() if folder is not None else None
        spelled.setdefault(key, folder)
        groups.setdefault(key, []).append((name, path))
    if empty:
        for folder in _kept_folders():
            spelled.setdefault(folder.casefold(), folder)
            groups.setdefault(folder.casefold(), [])
    keys = sorted(k for k in groups if k is not None)
    return ([(None, groups[None])] if None in groups else []) + \
        [(spelled[k], groups[k]) for k in keys]


def list_folders():
    """The names of all folders, also empty ones, sorted."""
    return [f for f, _presets in list_presets_by_folder(empty=True)
            if f is not None]


def existing_folder(folder, besides=None):
    """folder cleaned, written as an existing folder of the same name is,
    e.g. "beamng" as "BeamNG"; besides: a folder not to count."""
    folder = clean_folder(folder)
    if folder is None:
        return None
    skip = clean_folder(besides)
    for known in list_folders():
        if known.casefold() == folder.casefold() and (
                skip is None or known.casefold() != skip.casefold()):
            return known
    return folder


def preset_folder(path):
    """The folder of the preset at path, or None."""
    return clean_folder(_read_json(path).get("folder"))


def preset_path(name):
    """File for a new preset called name."""
    return CONFIG_DIR / (re.sub(r"[/\\]", "_", name) + ".json")


def read_preset(path):
    """Return (name, values) of a preset file. Raises OSError, or
    ValueError for a file that holds no preset."""
    path = pathlib.Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("values", {}), dict):
        raise ValueError(f"{path.name} holds no preset")
    name = data.get("name")
    return (name if isinstance(name, str) else path.stem), data.get("values", {})


def write_preset(path, name, values, folder=KEEP):
    """Write a preset file; raises OSError. folder: the folder to put it
    in, None for none; left out, a preset written again stays in its
    folder."""
    if folder is KEEP:
        folder = preset_folder(path)
    elif clean_folder(folder):
        add_folder(folder, respell=False)   # kept once the preset leaves
    data = {"name": name}
    folder = clean_folder(folder)
    if folder:
        data["folder"] = folder
    data["values"] = values
    write_file(path, json.dumps(data, indent=2))


def set_preset_folder(path, folder):
    """Put a preset into folder (None: into no folder); raises OSError
    or ValueError (bad file)."""
    name, values = read_preset(path)
    write_preset(path, name, values, existing_folder(folder))


def rename_folder(old, new):
    """Give a folder a new name (also another spelling, e.g. "beamng" to
    "BeamNG"), or merge it into another one (new None: the presets leave
    the folder, which is gone then). Returns how many presets moved;
    raises OSError."""
    old, new = clean_folder(old), existing_folder(new, besides=old)
    if old is None:
        return 0
    kept = [k for k in _kept_folders() if k.casefold() != old.casefold()]
    if new is not None and new.casefold() not in {k.casefold() for k in kept}:
        kept.append(new)
    _keep_folders(kept)
    moved = 0
    for name, path, folder in _scan():
        if folder is not None and folder.casefold() == old.casefold() \
                and folder != new:
            _name, values = read_preset(path)
            write_preset(path, name, values, new)
            moved += 1
    return moved


def delete_folder(folder):
    """Remove a folder; its presets stay, without a folder. Returns how
    many presets were in it; raises OSError."""
    return rename_folder(folder, None)


def rename_preset(path, new_name):
    """Give a preset a new name; its file is renamed to match.

    Games that use the preset keep it. Returns the new path. Raises
    FileExistsError if another preset already has that file name, OSError
    or ValueError (bad file) otherwise.
    """
    path = pathlib.Path(path)
    _name, values = read_preset(path)
    new_path = preset_path(new_name)
    if new_path != path and new_path.exists():
        raise FileExistsError(new_path.name)
    write_preset(new_path, new_name, values, preset_folder(path))
    if new_path != path:
        path.unlink(missing_ok=True)
        games = load_games()
        changed = False
        for game in games:
            if game.get("preset") == path.name:
                game["preset"] = new_path.name
                changed = True
        if changed:
            save_games(games)
    return new_path
