"""The shipped .po files match the code and keep their placeholders."""
import importlib.util
import pathlib
import re

import pytest

from understeer.i18n import parse_po

ROOT = pathlib.Path(__file__).resolve().parent.parent
PO_FILES = sorted((ROOT / "locale").glob("*.po"))
PLACEHOLDER = re.compile(r"\{\w*\}")


@pytest.fixture(scope="module")
def code_strings():
    spec = importlib.util.spec_from_file_location(
        "update_translations", ROOT / "tools" / "update_translations.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return {msgid for msgid, _ref in tool.extract()}


def test_template_is_up_to_date(code_strings):
    pot = (ROOT / "locale" / "understeer.pot").read_text(encoding="utf-8")
    # parse_po skips empty msgstr, so fill them in to read the msgids
    in_pot = set(parse_po(pot.replace('msgstr ""', 'msgstr "x"'))) - {""}
    assert in_pot == code_strings, "run python3 tools/update_translations.py"


@pytest.mark.parametrize("po", PO_FILES, ids=lambda p: p.name)
def test_every_string_is_translated(po, code_strings):
    catalog = parse_po(po.read_text(encoding="utf-8"))
    assert sorted(code_strings - set(catalog)) == []


@pytest.mark.parametrize("po", PO_FILES, ids=lambda p: p.name)
def test_placeholders_are_kept(po):
    catalog = parse_po(po.read_text(encoding="utf-8"))
    broken = {msgid: msgstr for msgid, msgstr in catalog.items()
              if sorted(PLACEHOLDER.findall(msgid)) != sorted(PLACEHOLDER.findall(msgstr))}
    assert broken == {}
