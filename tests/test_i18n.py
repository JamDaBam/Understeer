import pytest

from understeer import i18n


def test_parse_po_with_continuation_lines():
    catalog = i18n.parse_po(
        'msgid ""\nmsgstr ""\n"Language: de\\n"\n\n'
        '#: x.py:1\nmsgid "Steering"\nmsgstr "Lenkung"\n\n'
        'msgid "Long "\n"text"\nmsgstr "Langer "\n"Text"\n')
    assert catalog == {"Steering": "Lenkung", "Long text": "Langer Text"}


def test_parse_po_escapes():
    catalog = i18n.parse_po('msgid "a\\nb \\"c\\""\nmsgstr "x\\ty\\\\z"\n')
    assert catalog == {'a\nb "c"': "x\ty\\z"}


def test_parse_po_skips_untranslated():
    assert i18n.parse_po('msgid "Steering"\nmsgstr ""\n') == {}


@pytest.mark.parametrize("env, expected", [
    ({"LANG": "de_DE.UTF-8"}, "Lenkung"),
    ({"LANGUAGE": "en_US:de"}, "Steering"),        # English first stays English
    ({"LANGUAGE": "fr_FR:de"}, "Lenkung"),         # missing language: next one
    ({"LANG": "fr_FR.UTF-8"}, None),               # no translation: source text
    ({"LANG": "C"}, None),
    ({"LANGUAGE": "en", "LANG": "de_DE.UTF-8"}, "Steering"),
])
def test_language_choice(tmp_path, monkeypatch, env, expected):
    (tmp_path / "de.po").write_text('msgid "Steering"\nmsgstr "Lenkung"\n')
    (tmp_path / "en.po").write_text('msgid "Steering"\nmsgstr "Steering"\n')
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert i18n.load_catalog(tmp_path).get("Steering") == expected
