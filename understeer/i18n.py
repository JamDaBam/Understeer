"""Translations from locale/<lang>.po, without depending on GTK.

All user-visible text goes through _() (see README for adding a language).
"""
import os

from . import DATA_DIR

LOCALE_DIR = DATA_DIR / "locale"


def _unquote_po(s):
    s = s.strip()
    if not (s.startswith('"') and s.endswith('"')):
        return ""
    return (s[1:-1].replace('\\n', '\n').replace('\\t', '\t')
            .replace('\\"', '"').replace('\\\\', '\\'))


def parse_po(text):
    """Minimal .po reader (msgid/msgstr with continuation lines)."""
    catalog, msgid, msgstr, field = {}, None, None, None

    def flush():
        if msgid and msgstr:
            catalog[msgid] = msgstr

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("msgid "):
            flush()
            msgid, msgstr, field = _unquote_po(line[6:]), None, "id"
        elif line.startswith("msgstr "):
            msgstr, field = _unquote_po(line[7:]), "str"
        elif line.startswith('"'):
            if field == "id":
                msgid += _unquote_po(line)
            elif field == "str":
                msgstr += _unquote_po(line)
        elif not line or line.startswith("#"):
            field = None if not line else field
    flush()
    return catalog


def _wanted_languages():
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value:
            langs = []
            for entry in value.split(":"):
                entry = entry.split(".")[0].split("@")[0]
                if entry and entry not in ("C", "POSIX"):
                    langs += [entry, entry.split("_")[0]]
            return langs
    return []


def load_catalog(locale_dir=None):
    locale_dir = locale_dir or LOCALE_DIR
    for lang in _wanted_languages():
        po = locale_dir / f"{lang}.po"
        if po.is_file():
            try:
                return parse_po(po.read_text(encoding="utf-8"))
            except OSError:
                pass
    return {}


_CATALOG = load_catalog()


def _(text):
    return _CATALOG.get(text, text)


def N_(text):
    """Mark a string for translation; it is translated where it is shown."""
    return text
