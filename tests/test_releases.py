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
    assert releases.plain_version(None) == ""
    assert releases.plain_version(5) == ""          # from a damaged settings file


def test_notes_in_english(metainfo):
    assert releases.blocks("v2.0.0", "en", metainfo) == [
        ("ul", ["New thing & more", "Fix"]), ("p", "Needs 1 < 2.")]


def test_notes_translated(metainfo):
    assert releases.blocks("2.0.0", "de", metainfo) == [
        ("ul", ["Neues Ding & mehr", "Behoben"]), ("p", "Braucht 1 < 2.")]


def test_notes_fall_back_to_english(metainfo):
    assert releases.blocks("1.9.0", "de", metainfo) == [("p", "Only English.")]
    assert releases.blocks("2.0.0", "fr", metainfo)[0] == ("ul", ["New thing & more", "Fix"])


def test_no_notes(metainfo, tmp_path):
    assert releases.blocks("1.0.0", "en", metainfo) == []        # no description
    assert releases.blocks("3.0.0", "en", metainfo) == []        # unknown
    assert releases.blocks("1.9.0", "en", tmp_path / "missing.xml") == []
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
    numbers = [releases.number(r.get("version")) for r in found]
    assert None not in numbers, "a version that can't be ordered"
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


def test_all_versions(metainfo):
    assert releases.all_versions(metainfo) == ["2.0.0", "1.9.0"]


ORDER = ["0.9", "1.9.5", "1.10.0", "2.3.3", "2.4.0-beta.1", "2.4.0-beta.2",
         "2.4.0-beta.9", "2.4.0-beta.10", "2.4.0", "2.4.1", "2.10.0-beta.1",
         "2.10.0", "10.0.0"]


def test_versions_in_order():
    keys = [releases.number(v) for v in ORDER]
    assert None not in keys
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


@pytest.mark.parametrize("older, newer", [
    ("2.3.3", "2.4.0"), ("1.9.5", "1.10.0"),                   # stable
    ("2.4.0-beta.1", "2.4.0-beta.2"), ("2.4.0-beta.9", "2.4.0-beta.10"),
    ("2.4.0-beta.2", "2.5.0-beta.1"),                          # beta
    ("2.3.3", "2.4.0-beta.1"), ("2.4.0-beta.1", "2.4.0"),      # beta and stable
    ("2.4.0-beta.99", "2.4.0"), ("2.4.0", "2.4.1-beta.1"),
    ("2.4.0-beta.1", "2.4.1"),
])
def test_compare(older, newer):
    assert releases.number(older) < releases.number(newer)
    assert releases.number("v" + older) == releases.number(older)


@pytest.mark.parametrize("a, b", [
    ("2.0", "2.0.0"), ("2", "2.0.0.0"), ("2.4.01", "2.4.1"),
    ("2.4.0-beta.01", "2.4.0-beta.1"), ("v2.4.0", "2.4.0\n"),
])
def test_same_version(a, b):
    assert releases.number(a) == releases.number(b) is not None


def test_number_of_stable_and_beta():
    assert releases.number("1.9.5") == ((1, 9, 5, 0), 1, 0)
    assert releases.number("2.4.0-beta.1") == ((2, 4, 0, 0), 0, 1)
    assert not releases.is_prerelease("2.4.0")
    assert releases.is_prerelease("2.4.0-beta.1")
    assert releases.is_prerelease("v2.4.0-beta.10")
    assert not releases.is_prerelease("2.4.0-rc1")          # no version at all


@pytest.mark.parametrize("version", [
    # unsupported test versions and builds from git
    "2.4.0-rc1", "2.4.0-rc.1", "2.4.0-alpha.1", "2.4.0-beta", "2.4.0-beta.",
    "2.4.0-beta1", "2.4.0-Beta.1", "2.4.0-beta.1.2", "2.4.0-beta.x",
    "2.4.0-beta.-1", "2.4.0+build", "1.9.5-3-gabc", "2.4.0-beta.1-3-gabc",
    "2.4.0-dirty", "2.4.0-beta.1-dirty",
    # malformed
    "", "v", "vv2.4.0", "V2.4.0", "2..0", ".2.4", "2.4.", "2.4.0.1.2",
    "-2.4.0", "2.4.x", "2.4 .0", "2. 4.0", "2.4.0 x", "1_000.0.0",
    "٢.٤.٠", "２.4.0", "2.4.0-beta.٢", "unknown", "dev", "abc1234",
    # too long: more than 6 digits in a number, or very long text
    "1234567.0.0", "2.4.0-beta.1234567", "9" * 5000, "1." * 3000 + "1",
    "2.4.0-beta." + "9" * 5000,
    # no text
    None, 5, 2.4, ["2.4.0"],
])
def test_no_version(version):
    assert releases.number(version) is None
    assert not releases.is_prerelease(version)


@pytest.fixture
def beta_metainfo(tmp_path):
    def release(version, date):
        return (f'<release version="{version}" date="{date}"><description>'
                f'<p>Notes of {version}</p><p xml:lang="de">Notizen zu '
                f'{version}</p></description></release>')
    path = tmp_path / "metainfo.xml"
    path.write_text(
        "<component><releases>"
        + release("2.4.0", "2026-12-20")
        + release("2.4.0-beta.2", "2026-12-10")
        + release("2.4.0-beta.1", "2026-12-01")
        + release("2.3.3", "2026-11-13")
        + "</releases></component>", encoding="utf-8")
    return path


def test_notes_of_a_beta(beta_metainfo):
    assert releases.has_notes("v2.4.0-beta.1", beta_metainfo)
    assert releases.markdown("v2.4.0-beta.1", beta_metainfo) == \
        "Notes of 2.4.0-beta.1"
    assert releases.blocks("2.4.0-beta.2", "de", beta_metainfo) == [
        ("p", "Notizen zu 2.4.0-beta.2")]
    assert releases.release_date("2.4.0-beta.1", beta_metainfo) == "2026-12-01"
    assert releases.all_versions(beta_metainfo) == [
        "2.4.0", "2.4.0-beta.2", "2.4.0-beta.1", "2.3.3"]


@pytest.mark.parametrize("current, last, expected", [
    ("2.4.0-beta.1", "2.3.3", True),        # stable to beta
    ("2.4.0-beta.2", "2.4.0-beta.1", True),  # beta to beta
    ("2.4.0", "2.4.0-beta.2", True),        # beta to stable
    ("2.4.0", "2.4.0-beta.1", True),
    ("2.4.0-beta.1", "2.4.0-beta.1", False),
    ("2.4.0-beta.1", "2.4.0-beta.2", False),  # back to an older beta
    ("2.4.0-beta.2", "2.4.0", False),       # back from stable to a beta
    ("2.3.3", "2.4.0-beta.1", False),       # back from a beta to stable
    ("2.4.0-beta.1-3-gabc", "2.3.3", False),  # a beta run from git
])
def test_should_announce_betas(beta_metainfo, current, last, expected):
    assert releases.should_announce(current, last, True, beta_metainfo) is expected


@pytest.mark.parametrize("last, current, expected", [
    ("2.3.3", "2.4.0-beta.1", ["2.4.0-beta.1"]),
    ("2.3.3", "2.4.0-beta.2", ["2.4.0-beta.2", "2.4.0-beta.1"]),
    ("2.4.0-beta.1", "2.4.0", ["2.4.0", "2.4.0-beta.2"]),
    ("2.3.3", "2.4.0", ["2.4.0", "2.4.0-beta.2", "2.4.0-beta.1"]),
    ("2.4.0-beta.2", "2.4.0", ["2.4.0"]),
    ("2.4.0-rc1", "2.4.0", ["2.4.0"]),          # unknown: just the current
])
def test_versions_since_betas(beta_metainfo, last, current, expected):
    assert releases.versions_since(last, current, beta_metainfo) == expected
