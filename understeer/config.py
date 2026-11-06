"""Presets and app settings in ~/.config/Understeer, without depending on GTK.

Every *.json directly in CONFIG_DIR is a preset:
    {"name": "GT3 Race", "values": {"FF": 70, "SEN": 900, ...}}
"""
import hashlib
import json
import os
import pathlib
import re
import shutil

CONFIG_DIR  = pathlib.Path.home() / ".config" / "Understeer"
OLD_CONFIG_DIR = pathlib.Path.home() / ".config" / "SimPower"   # before rename
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


def migrate_config():
    """Move presets and settings from the old SimPower folder once."""
    if OLD_CONFIG_DIR.is_dir() and not CONFIG_DIR.exists():
        try:
            OLD_CONFIG_DIR.rename(CONFIG_DIR)
        except OSError:
            pass


def load_axis_roles():
    try:
        return json.loads(AXES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_axis_roles(roles):
    try:
        AXES_FILE.parent.mkdir(parents=True, exist_ok=True)
        AXES_FILE.write_text(json.dumps(roles, indent=2))
    except OSError:
        pass


def _read_json(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


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
    try:
        SLOT_NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        SLOT_NAMES_FILE.write_text(json.dumps(data, indent=2, sort_keys=True))
    except OSError:
        pass


def load_slot_count(model):
    """Highest slot number seen on this wheel base model, 0 if unknown."""
    count = _read_json(SLOT_COUNTS_FILE).get(model)
    return count if isinstance(count, int) and count > 0 else 0


def save_slot_count(model, count):
    data = _read_json(SLOT_COUNTS_FILE)
    data[model] = count
    try:
        SLOT_COUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SLOT_COUNTS_FILE.write_text(json.dumps(data, indent=2, sort_keys=True))
    except OSError:
        pass


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
    try:
        APP_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        APP_SETTINGS_FILE.write_text(json.dumps(data, indent=2, sort_keys=True))
    except OSError:
        pass


def load_games():
    """Games with their preset, e.g.
    [{"game": "steam:805550", "name": "Assetto Corsa Competizione",
      "preset": "GT3 Race.json", "auto": true}]
    "preset" is the file name of a preset in CONFIG_DIR, or None.
    "cover" (optional) is the file name of a picture the user chose, in
    CUSTOM_COVER_DIR.
    """
    try:
        data = json.loads(GAMES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    games = []
    for g in data:
        if isinstance(g, dict) and isinstance(g.get("game"), str):
            games.append({"game": g["game"],
                          "name": str(g.get("name") or g["game"]),
                          "preset": g.get("preset") if isinstance(
                              g.get("preset"), str) else None,
                          "auto": bool(g.get("auto", True))})
            cover = g.get("cover")
            if isinstance(cover, str) and cover and "/" not in cover:
                games[-1]["cover"] = cover
    return games


def save_games(games):
    try:
        GAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        GAMES_FILE.write_text(json.dumps(games, indent=2))
    except OSError:
        pass


def game_cover_path(game):
    """Picture the user chose for a game, or None."""
    name = game.get("cover")
    path = CUSTOM_COVER_DIR / name if name else None
    return path if path is not None and path.is_file() else None


def set_game_cover(game, source):
    """Copy the picture at source for game; returns its path. Raises OSError."""
    suffix = pathlib.Path(source).suffix.lower() or ".img"
    name = hashlib.sha1(game["game"].encode()).hexdigest()[:16] + suffix
    CUSTOM_COVER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CUSTOM_COVER_DIR / (name + ".part")
    shutil.copyfile(source, tmp)
    remove_game_cover(game)
    tmp.replace(CUSTOM_COVER_DIR / name)
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
def list_presets():
    """[(name, path)] of all presets, sorted by file name."""
    if not CONFIG_DIR.exists():
        return []
    result = []
    for f in sorted(CONFIG_DIR.glob("*.json")):
        try:
            name = json.loads(f.read_text()).get("name", f.stem)
        except (OSError, json.JSONDecodeError):
            name = f.stem
        result.append((name, f))
    return result


def preset_path(name):
    """File for a new preset called name."""
    return CONFIG_DIR / (re.sub(r"[/\\]", "_", name) + ".json")


def read_preset(path):
    """Return (name, values) of a preset file; raises on bad files."""
    data = json.loads(pathlib.Path(path).read_text())
    return data.get("name", pathlib.Path(path).stem), data.get("values", {})


def write_preset(path, name, values):
    """Write a preset file; raises OSError."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(
        {"name": name, "values": values}, indent=2))
