#!/usr/bin/env python3
"""Collect all _("...") and N_("...") strings from the understeer package.

Writes locale/understeer.pot and adds new strings to every locale/*.po
(existing translations are kept, removed strings are dropped).
Run it after changing user-visible text:  python3 tools/update_translations.py
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from understeer.i18n import parse_po  # noqa: E402  (no GTK needed)

PACKAGE = ROOT / "understeer"
LOCALE = ROOT / "locale"


def extract():
    """[(msgid, "file:line")] in order of first appearance."""
    found = {}
    for source in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        calls = sorted((node.lineno, node.args[0].value)
                       for node in ast.walk(tree)
                       if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Name)
                       and node.func.id in ("_", "N_") and node.args
                       and isinstance(node.args[0], ast.Constant)
                       and isinstance(node.args[0].value, str))
        where = source.relative_to(ROOT).as_posix()
        for line, msgid in calls:
            found.setdefault(msgid, f"{where}:{line}")
    return list(found.items())


def quote(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def render(strings, translations, header):
    out = [header]
    for msgid, ref in strings:
        out.append(f"#: {ref}")
        out.append(f"msgid {quote(msgid)}")
        out.append(f"msgstr {quote(translations.get(msgid, ''))}")
        out.append("")
    return "\n".join(out)


HEADER = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Language: {lang}\\n"
'''

if __name__ == "__main__":
    strings = extract()
    LOCALE.mkdir(exist_ok=True)
    (LOCALE / "understeer.pot").write_text(
        render(strings, {}, HEADER.format(lang="")), encoding="utf-8")
    for po in sorted(LOCALE.glob("*.po")):
        old = parse_po(po.read_text(encoding="utf-8"))
        po.write_text(render(strings, old, HEADER.format(lang=po.stem)),
                      encoding="utf-8")
        missing = [s for s, _ref in strings if not old.get(s)]
        print(f"{po.name}: {len(strings) - len(missing)}/{len(strings)} translated")
    print(f"understeer.pot: {len(strings)} strings")
