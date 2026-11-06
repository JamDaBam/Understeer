#!/usr/bin/env python3
"""Collect all _("...") and N_("...") strings from understeer.py.

Writes locale/understeer.pot and adds new strings to every locale/*.po
(existing translations are kept, removed strings are dropped).
Run it after changing user-visible text:  python3 tools/update_translations.py
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SOURCE = ROOT / "understeer.py"
LOCALE = ROOT / "locale"


def parse_po(text):
    # Same reader the app uses, copied to avoid importing GTK here.
    catalog, msgid, msgstr, field = {}, None, None, None

    def unq(s):
        s = s.strip()
        return (s[1:-1].replace('\\n', '\n').replace('\\"', '"')
                .replace('\\\\', '\\')) if s.startswith('"') else ""

    def flush():
        if msgid:
            catalog[msgid] = msgstr or ""

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("msgid "):
            flush()
            msgid, msgstr, field = unq(line[6:]), None, "id"
        elif line.startswith("msgstr "):
            msgstr, field = unq(line[7:]), "str"
        elif line.startswith('"'):
            if field == "id":
                msgid += unq(line)
            elif field == "str":
                msgstr += unq(line)
    flush()
    return catalog


def extract():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("_", "N_") and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            found.setdefault(node.args[0].value, node.lineno)
    return sorted(found.items(), key=lambda kv: kv[1])


def quote(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def render(strings, translations, header):
    out = [header]
    for msgid, line in strings:
        out.append(f"#: understeer.py:{line}")
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
        missing = [s for s, _l in strings if not old.get(s)]
        print(f"{po.name}: {len(strings) - len(missing)}/{len(strings)} translated")
    print(f"understeer.pot: {len(strings)} strings")
