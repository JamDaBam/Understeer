"""Release notes from the metainfo file."""
import xml.etree.ElementTree as ET

import pytest

from understeer import releases

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<component>
  <releases>
    <release version="2.0.0" date="2026-12-01">
      <description>
        <ul>
          <li>New <em>thing</em> &amp; more</li>
          <li xml:lang="de">Neues <em>Ding</em> &amp; mehr</li>
          <li>Fix</li>
          <li xml:lang="de">Behoben</li>
        </ul>
        <p>Needs 1 &lt; 2.</p>
        <p xml:lang="de">Braucht 1 &lt; 2.</p>
      </description>
    </release>
    <release version="1.9.0" date="2026-11-10">
      <description><p>Only English.</p></description>
    </release>
    <release version="1.0.0" date="2026-01-01"/>
  </releases>
</component>
"""


@pytest.fixture
def metainfo(tmp_path):
    path = tmp_path / "metainfo.xml"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def test_plain_version():
    assert releases.plain_version("v1.8.1") == "1.8.1"
    assert releases.plain_version("1.8.1\n") == "1.8.1"
    assert releases.plain_version("v1.8.1-3-gabc") == "1.8.1-3-gabc"


def test_notes_in_english(metainfo):
    assert releases.notes("v2.0.0", "en", metainfo) == (
        "<ul><li>New thing &amp; more</li><li>Fix</li></ul>"
        "<p>Needs 1 &lt; 2.</p>")


def test_notes_translated(metainfo):
    assert releases.notes("2.0.0", "de", metainfo) == (
        "<ul><li>Neues Ding &amp; mehr</li><li>Behoben</li></ul>"
        "<p>Braucht 1 &lt; 2.</p>")


def test_notes_fall_back_to_english(metainfo):
    assert releases.notes("1.9.0", "de", metainfo) == "<p>Only English.</p>"
    assert releases.notes("2.0.0", "fr", metainfo).startswith("<ul><li>New")


def test_no_notes(metainfo, tmp_path):
    assert releases.notes("1.0.0", "en", metainfo) is None      # no description
    assert releases.notes("3.0.0", "en", metainfo) is None      # unknown
    assert releases.notes("1.9.0", "en", tmp_path / "missing.xml") is None
    assert not releases.has_notes("1.0.0", metainfo)
    assert releases.has_notes("1.9.0", metainfo)


def test_markdown(metainfo):
    assert releases.markdown("2.0.0", metainfo) == (
        "- New thing & more\n- Fix\n\nNeeds 1 < 2.")
    assert releases.markdown("3.0.0", metainfo) is None


@pytest.mark.parametrize("current, last, used, expected", [
    ("2.0.0", "1.9.0", True, True),         # update
    ("v2.0.0", "v1.9.0", True, True),
    ("2.0.0", "2.0.0", True, False),        # same version again
    ("1.9.0", "2.0.0", True, False),        # went back to an older one
    ("2.0.0", None, True, True),            # update from before 1.9.0
    ("2.0.0", None, False, False),          # first start ever
    ("1.0.0", "0.9.0", True, False),        # no notes to show
    ("v2.0.0-3-gabc", "1.9.0", True, False),  # run from git
])
def test_should_announce(metainfo, current, last, used, expected):
    assert releases.should_announce(current, last, used, metainfo) is expected


def test_was_used(tmp_path):
    assert not releases.was_used(tmp_path)
    (tmp_path / "settings").mkdir()
    assert not releases.was_used(tmp_path)
    (tmp_path / "settings" / "app.json").write_text("{}")
    assert releases.was_used(tmp_path)


def test_shipped_metainfo():
    """Newest release first, every one with notes, every line translated."""
    root = ET.parse(releases.METAINFO).getroot()
    found = root.findall("releases/release")
    assert found, "no <releases> in the metainfo file"
    numbers = [tuple(map(int, r.get("version").split("."))) for r in found]
    assert numbers == sorted(numbers, reverse=True)
    dates = [r.get("date") for r in found]
    assert dates == sorted(dates, reverse=True)
    lang = releases._LANG
    for release in found:
        description = release.find("description")
        assert description is not None, release.get("version")
        for parent in [description, *description.findall("ul"), *description.findall("ol")]:
            english = [e for e in parent if e.tag in ("p", "li") and lang not in e.attrib]
            german = [e for e in parent if e.tag in ("p", "li") and e.get(lang) == "de"]
            assert len(english) == len(german), release.get("version")


def test_blocks_and_date(metainfo):
    assert releases.blocks("2.0.0", "de", metainfo)[0] == (
        "ul", ["Neues Ding & mehr", "Behoben"])
    assert releases.blocks("1.0.0", None, metainfo) == []
    assert releases.release_date("v2.0.0", metainfo) == "2026-12-01"
    assert releases.release_date("3.0.0", metainfo) is None


@pytest.mark.parametrize("last, current, expected", [
    (None, "2.0.0", ["2.0.0"]),
    ("1.9.0", "2.0.0", ["2.0.0"]),
    ("1.0.0", "2.0.0", ["2.0.0", "1.9.0"]),     # skipped one, 1.0.0 has no notes
    ("0.5", "2.0.0", ["2.0.0", "1.9.0"]),
    ("1.0.0", "1.9.0", ["1.9.0"]),
    ("2.0.0", "2.0.0", ["2.0.0"]),               # asked from the menu
    ("garbage", "2.0.0", ["2.0.0"]),
])
def test_versions_since(metainfo, last, current, expected):
    assert releases.versions_since(last, current, metainfo) == expected
