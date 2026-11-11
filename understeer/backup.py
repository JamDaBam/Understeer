"""Export and import everything Understeer keeps, as one ZIP file.

For moving to another computer: presets, games with their pictures, and
the settings in settings/ (slot names, axes, language, SteamGridDB key).
Downloaded Steam covers are left out, they are fetched again.

Inside the file:
    understeer-backup.json      {"format": 1, "version": "1.4.2"}
    presets/<name>.json
    settings/<name>.json
    covers/<picture>

Importing adds to what is there: presets and games with the same name
are replaced, everything else is kept. Works without GTK.
"""
import json
import logging
import pathlib
import re
import zipfile

from . import config

FORMAT = 1
MANIFEST = "understeer-backup.json"
SETTINGS = ("app.json", "axes.json", "games.json", "slot_counts.json",
            "slot_names.json")
MAX_FILE = 20 * 1024 * 1024         # a cover picture at most
MAX_TOTAL = 200 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^/\\\0]+")

log = logging.getLogger("understeer.backup")


class Error(Exception):
    """Not an Understeer export, or a damaged one."""


def _folders():
    return (config.CONFIG_DIR, config.CONFIG_DIR / "settings",
            config.CONFIG_DIR / "covers")


def export(dest):
    """Write the export to dest. Returns {"presets", "games", "covers"} counts.

    Raises OSError.
    """
    from . import log as session_log
    presets_dir, settings_dir, covers_dir = _folders()
    dest = pathlib.Path(dest)
    tmp = dest.with_name(dest.name + ".part")
    counts = {"presets": 0, "games": len(config.load_games()), "covers": 0}
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(
                {"format": FORMAT, "version": session_log.version()}, indent=2))
            for _name, path in config.list_presets():
                z.write(path, "presets/" + path.name)
                counts["presets"] += 1
            for name in SETTINGS:
                if (settings_dir / name).is_file():
                    z.write(settings_dir / name, "settings/" + name)
            if covers_dir.is_dir():
                for path in sorted(covers_dir.iterdir()):
                    if path.is_file() and not path.name.endswith(".part"):
                        z.write(path, "covers/" + path.name)
                        counts["covers"] += 1
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    log.info("Exported to %s: %s", dest, counts)
    return counts


def _json(data, name):
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise Error(f"{name} is damaged") from e


def read(source):
    """Check an export and return what it holds:

    {"presets": {file name: (name, raw bytes)}, "settings": {name: data},
     "covers": {file name: bytes}, "version": str}
    Raises Error, or OSError if it can't be read.
    """
    try:
        z = zipfile.ZipFile(source)
    except zipfile.BadZipFile as e:
        raise Error("not a ZIP file") from e
    with z:
        names = set(z.namelist())
        if MANIFEST not in names:
            raise Error("not an Understeer export")
        total = 0
        files = {}
        for info in z.infolist():
            if info.is_dir():
                continue
            if info.file_size > MAX_FILE:
                raise Error(f"{info.filename} is too large")
            total += info.file_size
            if total > MAX_TOTAL:
                raise Error("export too large")
            try:
                files[info.filename] = z.read(info)
            except (zipfile.BadZipFile, ValueError) as e:
                raise Error(f"{info.filename} is damaged") from e
    manifest = _json(files.pop(MANIFEST), MANIFEST)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("format"), int):
        raise Error("not an Understeer export")
    if manifest["format"] > FORMAT:
        raise Error("made by a newer Understeer")

    found = {"presets": {}, "settings": {}, "covers": {},
             "version": str(manifest.get("version") or "?")}
    for path, data in files.items():
        folder, _, name = path.partition("/")
        if not _SAFE_NAME.fullmatch(name) or name in (".", ".."):
            log.info("Import: skipping %s", path)
            continue
        if folder == "presets" and name.endswith(".json"):
            preset = _json(data, path)
            if not isinstance(preset, dict) or not isinstance(
                    preset.get("values", {}), dict):
                raise Error(f"{path} is not a preset")
            found["presets"][name] = (str(preset.get("name") or name[:-5]), data)
        elif folder == "settings" and name in SETTINGS:
            found["settings"][name] = _json(data, path)
        elif folder == "covers" and not name.endswith(".part"):
            found["covers"][name] = data
        else:
            log.info("Import: skipping %s", path)
    return found


def summary(found):
    """{"presets", "games", "covers"} counts of what read() found."""
    games = found["settings"].get("games.json")
    return {"presets": len(found["presets"]),
            "games": len(games) if isinstance(games, list) else 0,
            "covers": len(found["covers"])}


def _merge(name, old, new):
    """Settings file contents after importing new over old."""
    if name == "games.json":
        if not isinstance(new, list):
            return old
        new = [g for g in new if isinstance(g, dict) and isinstance(g.get("game"), str)]
        ids = {g["game"] for g in new}
        old = old if isinstance(old, list) else []
        return [g for g in old if isinstance(g, dict) and g.get("game") not in ids] + new
    if not isinstance(new, dict):
        return old
    merged = dict(old) if isinstance(old, dict) else {}
    merged.update(new)
    return merged


def import_(found):
    """Add what read() found to this computer. Raises OSError."""
    presets_dir, settings_dir, covers_dir = _folders()
    for name, (_title, data) in found["presets"].items():
        config.write_file(presets_dir / name, data)
    for name, new in found["settings"].items():
        merged = _merge(name, config.load_json(settings_dir / name), new)
        config.write_file(settings_dir / name, json.dumps(
            merged, indent=2, sort_keys=name != "games.json").encode("utf-8"))
    for name, data in found["covers"].items():
        config.write_file(covers_dir / name, data)
    log.info("Imported: %s", summary(found))
