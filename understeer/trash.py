"""Understeer's own bin for deleted presets and games, without GTK.

Deleting moves a preset or game here instead of removing it, so it can be
put back: right away with Undo, or later from the Trash button on
the Presets or Games page. After
KEEP_DAYS days it is gone for good (expire(), at start).

Each item is a JSON file in CONFIG_DIR/trash: the preset file's text, or
the game with its preset and settings; a picture the user chose for a
game is kept next to it. The folder is below CONFIG_DIR, where only
*.json directly in it count as presets.
"""
import json
import logging
import os
import pathlib
import time

from . import config
from .sharing import free_name

KEEP_DAYS = 30

log = logging.getLogger("understeer.trash")


class Error(Exception):
    """An item could not be put back; the message can be shown."""


def _folder():
    return config.CONFIG_DIR / "trash"


def _new_id():
    return f"{time.time_ns()}-{os.urandom(3).hex()}"


def _store(item_id, data):
    config.write_file(_folder() / f"{item_id}.json",
                      json.dumps(data, indent=2, ensure_ascii=False))


def put_preset(path):
    """Move the preset file at path into the bin; returns the item's id.
    Raises OSError."""
    path = pathlib.Path(path)
    content = path.read_text(encoding="utf-8")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {}
    data = data if isinstance(data, dict) else {}
    name = data.get("name") if isinstance(data.get("name"), str) else path.stem
    item_id = _new_id()
    _store(item_id, {"kind": "preset", "name": name, "deleted": time.time(),
                     "folder": config.clean_folder(data.get("folder")),
                     "file": path.name, "content": content})
    path.unlink()
    log.info("Preset %s put in the bin", path.name)
    return item_id


def put_game(game):
    """Put a game (a dict of config.load_games()) into the bin, with the
    picture the user chose for it; returns the item's id. The caller
    removes it from its games. Raises OSError."""
    item_id = _new_id()
    entry = {"kind": "game", "name": game.get("name") or game.get("game"),
             "deleted": time.time(), "game": dict(game)}
    cover = config.game_cover_path(game)
    if cover is not None:
        kept = _folder() / f"{item_id}.cover{cover.suffix}"
        kept.parent.mkdir(parents=True, exist_ok=True)
        os.replace(cover, kept)
        entry["cover"] = kept.name
    _store(item_id, entry)
    log.info("Game %s put in the bin", game.get("game"))
    return item_id


def items(kind=None):
    """The items in the bin, newest first, only those of kind if given:
    dicts with "id", "kind" ("preset" or "game"), "name", "deleted" (a
    time.time()) and, for a preset, "folder"."""
    found = []
    for path in _folder().glob("*.json") if _folder().is_dir() else []:
        data = config.load_json(path)
        if not isinstance(data, dict) or data.get("kind") not in ("preset", "game") \
                or kind not in (None, data["kind"]):
            continue
        deleted = data.get("deleted")
        found.append({"id": path.stem, "kind": data["kind"],
                      "name": str(data.get("name") or path.stem),
                      "deleted": deleted if isinstance(deleted, (int, float)) else 0,
                      "folder": config.clean_folder(data.get("folder"))})
    return sorted(found, key=lambda i: i["deleted"], reverse=True)


def _read(item_id):
    path = _folder() / f"{item_id}.json"
    if "/" in item_id or not path.is_file():
        raise Error("not in the bin")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise Error(str(e)) from e
    if not isinstance(data, dict):
        raise Error("not in the bin")
    return data


def restore_preset(item_id):
    """Put a preset back; returns its path. Under another name if one
    with its file name came meanwhile, e.g. "Drift (2)". Raises Error or
    OSError."""
    data = _read(item_id)
    if data.get("kind") != "preset" or not isinstance(data.get("content"), str):
        raise Error("not a preset")
    content, name = data["content"], data.get("name") or "Preset"
    file_name = config._file_name(data.get("file"))
    path = config.CONFIG_DIR / file_name if file_name else config.preset_path(name)
    if path.exists():
        name = free_name(name, lambda n: config.preset_path(n).exists())
        path = config.preset_path(name)
        try:
            stored = json.loads(content)
            stored["name"] = name
            content = json.dumps(stored, indent=2)
        except (json.JSONDecodeError, TypeError):
            pass
    if data.get("folder"):
        config.add_folder(data["folder"], respell=False)
    config.write_file(path, content)
    remove(item_id)
    log.info("Preset %s put back from the bin", path.name)
    return path


def restore_game(item_id, games):
    """The game of an item, with its chosen picture back in place, for
    the caller to add to games (its list of games). Raises Error if a game
    with that id is there already, or OSError."""
    data = _read(item_id)
    game = data.get("game")
    if data.get("kind") != "game" or not isinstance(game, dict) \
            or not isinstance(game.get("game"), str):
        raise Error("not a game")
    if any(g.get("game") == game["game"] for g in games):
        raise Error("the game is there already")
    game = dict(game)
    cover = data.get("cover")
    if cover and config._file_name(game.get("cover")):
        source = _folder() / cover
        if source.is_file():
            config.CUSTOM_COVER_DIR.mkdir(parents=True, exist_ok=True)
            os.replace(source, config.CUSTOM_COVER_DIR / game["cover"])
        else:
            game.pop("cover", None)
    else:
        game.pop("cover", None)
    remove(item_id)
    log.info("Game %s put back from the bin", game["game"])
    return game


def remove(item_id):
    """Delete an item for good."""
    for path in _folder().glob(f"{item_id}.*") if _folder().is_dir() else []:
        if path.name == f"{item_id}.json" or path.name.startswith(f"{item_id}.cover"):
            try:
                path.unlink()
            except OSError as e:
                log.warning("Could not delete %s: %s", path, e)


def empty(kind=None):
    """Delete everything in the bin for good, or only what is of kind."""
    for item in items(kind):
        remove(item["id"])


def expire(now=None, days=KEEP_DAYS):
    """Delete items older than days for good; returns how many."""
    now = time.time() if now is None else now
    old = [i for i in items() if now - i["deleted"] > days * 86400]
    for item in old:
        remove(item["id"])
    if old:
        log.info("%d items older than %d days deleted from the bin", len(old), days)
    return len(old)
