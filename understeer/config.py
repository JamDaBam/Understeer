"""Presets and app settings in ~/.config/Understeer, without depending on GTK.

Every *.json directly in CONFIG_DIR is a preset:
    {"name": "GT3 Race", "values": {"FF": 70, "SEN": 900, ...}}
"""
import json
import pathlib
import re

CONFIG_DIR  = pathlib.Path.home() / ".config" / "Understeer"
OLD_CONFIG_DIR = pathlib.Path.home() / ".config" / "SimPower"   # before rename
# Kept in a subfolder: every *.json directly in CONFIG_DIR is a preset.
AXES_FILE   = CONFIG_DIR / "settings" / "axes.json"


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
