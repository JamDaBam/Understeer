"""The texts of the Share with Community dialog, without a display; the
dialog itself runs in ui_smoke.py."""
import pathlib

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from understeer.ui import share_dialog
except (ImportError, ValueError) as e:      # no GTK 4 / Libadwaita
    pytest.skip(str(e), allow_module_level=True)

from understeer import i18n, library, submission  # noqa: E402
from understeer.submission import Problem  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
GENERIC = "The preset can't be prepared for sharing."


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("en")


def problems_of(**changes):
    meta = dict(author="Alex", game="Test", wheelbase="CSL DD",
                description="Calm.")
    values = changes.pop("values", {"FF": 70})
    name = changes.pop("name", "GT3")
    meta.update(changes)
    with pytest.raises(submission.Invalid) as e:
        submission.prepare(name, values, **meta)
    return e.value.problems


@pytest.mark.parametrize("changes, expected", [
    (dict(game=""), "Game is required."),
    (dict(author=None), "Author is required."),
    (dict(name=" "), "Preset Name is required."),
    (dict(description=""), "Description is required."),
    (dict(wheelbase=None), "Choose the wheelbase the preset was made on."),
    (dict(wheelbase="Moza R9"), "Choose one of the listed wheelbases."),
    (dict(author="x" * 41), "Author is too long: at most 40 characters."),
    (dict(name="🏎" * 51), "Preset Name is too long."),
    (dict(description="a\n" * 21), "Description has too many lines: at most 20."),
    (dict(game="A\nB"), "Game must be one line."),
    (dict(game="A\x00B"), "Game contains invisible or control characters."),
    (dict(game=5), "Game must be text."),
    (dict(game="www.example.org"), "Game must not contain a web address."),
    (dict(author="a@example.org"), "Author must not contain an e-mail address."),
    (dict(description="~/x/y"), "Description must not contain a file path."),
    (dict(tags=["two words"]),
     "Tags are short words of a–z, 0–9 and -, separated by commas."),
    (dict(tags=[f"t{i}" for i in range(11)]), "At most 10 tags."),
    (dict(values={"ZZZ": 1}),
     "The preset has a setting this version can't share: ZZZ."),
    (dict(values={"FF": 101}), "Force Feedback Strength has a value that isn't valid."),
    (dict(values={"FF": float("nan")}),
     "Force Feedback Strength has a value that isn't valid."),
    (dict(values={"SEN": 905}), "Steering Angle is 905°, which the wheelbase "
                                "keeps as 900°. Save the preset with 900° first."),
    (dict(values={}), "The preset has no settings."),
])
def test_problem_text(changes, expected):
    texts = [share_dialog.problem_text(p) for p in problems_of(**changes)]
    assert texts == [expected]


def test_every_problem_has_its_own_text():
    """Every code submission.py can give for a field the dialog shows has a
    specific message, none falls back to the generic one."""
    source = (ROOT / "understeer" / "submission.py").read_text(encoding="utf-8")
    for code in ("required", "too_long", "too_many_lines", "line_breaks",
                 "control_characters", "not_text", "web_address",
                 "email_address", "file_path", "unknown_wheelbase",
                 "unsupported_setting", "invalid_value", "adjusted_value",
                 "no_settings", "too_many_settings"):
        assert f'"{code}"' in source, code
    assert share_dialog.problem_text(Problem("values", "too_many_settings", 64)) \
        == "The preset has too many settings."
    assert share_dialog.problem_text(Problem("entry", "not_accepted")) == GENERIC


def test_warning_texts():
    assert share_dialog.warning_text(Problem("description", "looks_like_serial")) \
        == ("Description contains something that looks like a serial number. "
            "Make sure it isn't one of yours.")
    assert share_dialog.warning_text(Problem("author", "private_word")) == \
        "Check Author once more."


def test_texts_in_german():
    i18n.set_language("de")
    assert share_dialog.problem_text(Problem("game", "required")) == \
        "Spiel wird benötigt."
    assert share_dialog.problem_text(Problem("values", "adjusted_value",
                                             ("SEN", 905, 900))) == \
        ("Lenkwinkel ist 905°, die Wheelbase übernimmt aber 900°. Speichere "
         "das Preset zuerst mit 900°.")
    assert i18n._("Share with Community") == "In Community teilen"
    assert i18n._("Continue on GitHub") == "Auf GitHub fortfahren"


def test_every_drive_has_a_name():
    """The review page shows each drive a catalog entry can have."""
    assert set(share_dialog.DRIVE_NAMES) == set(library.DRIVES)
    assert set(submission.WHEELBASE_DRIVES.values()) <= set(library.DRIVES)


@pytest.mark.parametrize("language, field, names", [
    ("en", "Drive", {"belt": "Belt", "direct_drive": "Direct drive"}),
    ("de", "Antrieb", {"belt": "Riemenantrieb", "direct_drive": "Direct Drive"}),
])
def test_drive_names(language, field, names):
    """The same words as the library uses for the drive of a preset."""
    i18n.set_language(language)
    assert i18n._(share_dialog.FIELDS["drive"]) == field
    assert {drive: i18n._(name) for drive, name
            in share_dialog.DRIVE_NAMES.items()} == names


@pytest.mark.parametrize("text, tags", [
    ("", []), ("gt3", ["gt3"]), (" GT3 , endurance,, ", ["GT3", "endurance"]),
    ("two words", ["two words"])])
def test_split_tags(text, tags):
    assert share_dialog.split_tags(text) == tags


def test_dialog_sends_and_writes_nothing():
    """The dialog only reads the saved preset and the cached catalog: no
    network, file writing, wheelbase access or shell. The browser is only
    opened in open_uri(), the clipboard only written in copy(), both only
    from a button the user clicks."""
    source = (ROOT / "understeer" / "ui" / "share_dialog.py").read_text(
        encoding="utf-8")
    for name in ("urllib", "http", "download", "refresh(", "write_preset",
                 "write_file", "write_values", "device", "_collect_values",
                 "_apply", "getpass", "environ", "socket", "subprocess",
                 "webbrowser", "xdg-open", "spawn", "os.system"):
        assert name not in source, name
    assert source.count("Gtk.UriLauncher") == 1
    assert source.count("get_clipboard()") == 1
    assert source.count("open_uri(self, plan.url, opened)") == 1


@pytest.mark.parametrize("field, label", [
    ("preset-json", "Catalog Entry (JSON)"), ("description", "Description"),
    ("preset-name", "Preset name"), ("wheelbase", "Wheelbase model")])
def test_github_labels(field, label):
    """Fields to copy are named as the form on GitHub names them."""
    assert share_dialog.GITHUB_LABELS[field] == label


def test_github_messages_in_both_languages():
    texts = {"Continue on GitHub…": "Auf GitHub fortfahren…",
             "GitHub opened. Review the information and submit the issue "
             "there.": "GitHub wurde geöffnet. Prüfe dort die Angaben und "
                       "reiche den Beitrag selbst ein.",
             "The browser could not be opened: {error}":
                 "Der Browser konnte nicht geöffnet werden: {error}"}
    for english, german in texts.items():
        assert i18n._(english) == english
        i18n.set_language("de")
        assert i18n._(english) == german
        i18n.set_language("en")
    assert "future update" not in (ROOT / "understeer" / "ui" /
                                   "share_dialog.py").read_text(encoding="utf-8")
