"""Searching and filtering the community catalog, all offline."""
import json
import pathlib
import socket

import pytest

from understeer import compatibility, library, library_search as ls

CSL_DD = compatibility.connected("/sys/class/ftec_tuning/0003:0EB7:0020.0005")


def entry(entry_id, name="Preset", **meta):
    return {"id": entry_id, "meta": meta,
            "preset": {"understeer_preset": 1, "name": name, "values": {"FF": 50}}}


CATALOG = [
    entry("gt3", "GT3 Endurance", author="Ana", game="Assetto Corsa Competizione",
          wheelbase="CSL DD", description="Long stints\nat night",
          tags=["gt3", "endurance"]),
    entry("rally", "Gravel Rally", author="Björn", game="BeamNG.drive",
          wheelbase="Podium Wheel Base DD1", description="Schotter und Straße",
          tags=["rally"]),
    entry("drift", "Drift Fun", author="Chloé", game="beamng.drive",
          wheelbase="csl dd", tags=["drift", "Rally"]),
    entry("bare", "Formula Bare"),
]


def parse(items):
    raw = json.dumps({"understeer_library": 1, "presets": items}).encode()
    return library.parse_catalog(raw).entries


@pytest.fixture
def entries():
    return parse(CATALOG)


def ids(result):
    return [e.id for e in result]


def search(entries, text, **filters):
    return ids(ls.apply(entries, ls.Filters(text, **filters), CSL_DD))


# --- search -------------------------------------------------------------------

@pytest.mark.parametrize("text, found", [
    ("endurance", ["gt3"]),                 # name and tag
    ("ANA", ["gt3"]),                       # author, other case
    ("competizione", ["gt3"]),              # game
    ("podium", ["rally"]),                  # wheelbase
    ("night", ["gt3"]),                     # second line of the description
    ("drift", ["drift"]),                   # tag
    ("björn", ["rally"]), ("BJÖRN", ["rally"]),
    ("chloé", ["drift"]),
    ("STRASSE", ["rally"]),                 # ß as ss, without case
    ("beamng rally", ["rally", "drift"]),   # every word, anywhere
    ("formula", ["bare"]),                  # no metadata at all
    ("nothing like it", []),
])
def test_search(entries, text, found):
    assert search(entries, text) == found


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_search_keeps_everything_in_order(entries, text):
    assert search(entries, text) == ["gt3", "rally", "drift", "bare"]


# --- filters --------------------------------------------------------------------

def test_options_without_duplicates_and_sorted(entries):
    options = ls.options(entries)
    assert options.games == ["Assetto Corsa Competizione", "BeamNG.drive"]
    assert options.wheelbases == ["CSL DD", "Podium Wheel Base DD1"]
    assert options.tags == ["drift", "endurance", "gt3", "rally"]


def test_options_of_a_catalog_without_metadata():
    assert ls.options(parse([entry("a"), entry("b")])) == ls.Options([], [], [])


def test_game_filter(entries):
    assert search(entries, "", game="BeamNG.drive") == ["rally", "drift"]


def test_wheelbase_filter(entries):
    assert search(entries, "", wheelbase="CSL DD") == ["gt3", "drift"]


def test_tag_filter(entries):
    assert search(entries, "", tag="rally") == ["rally", "drift"]


def test_only_my_wheelbase(entries):
    assert search(entries, "", only_mine=True) == ["gt3", "drift"]
    assert ids(ls.apply(entries, ls.Filters(only_mine=True), None)) == []


@pytest.mark.parametrize("text, filters, found", [
    ("rally", {"game": "BeamNG.drive"}, ["rally", "drift"]),
    ("", {"game": "Assetto Corsa Competizione", "wheelbase": "CSL DD"}, ["gt3"]),
    ("", {"tag": "gt3", "only_mine": True}, ["gt3"]),
    ("fun", {"tag": "rally", "only_mine": True}, ["drift"]),
    ("gravel", {"only_mine": True}, []),
    ("", {"game": "BeamNG.drive", "wheelbase": "Podium Wheel Base DD1",
          "tag": "rally"}, ["rally"]),
])
def test_combined(entries, text, filters, found):
    assert search(entries, text, **filters) == found


def test_reset(entries):
    filters = ls.Filters("x", "BeamNG.drive", "CSL DD", "rally", True)
    assert ls.active(filters)
    assert not ls.active(ls.NONE) and not ls.active(ls.Filters("  "))
    assert search(entries, *ls.NONE[:1]) == ids(entries)


def test_missing_metadata_is_left_out_by_a_filter(entries):
    for key in ("game", "wheelbase", "tag"):
        assert "bare" not in search(entries, "", **{key: "x"})


def test_choices_gone_after_a_refresh_fall_back_to_all(entries):
    filters = ls.Filters("gt", "BeamNG.drive", "CSL DD", "endurance")
    newer = ls.options(parse([CATALOG[0]]))         # only the GT3 preset left
    kept = ls.keep(filters, newer)
    assert kept == ls.Filters("gt", None, "CSL DD", "endurance")


def test_refresh_changes_the_choices():
    before = ls.options(parse(CATALOG[:1]))
    after = ls.options(parse(CATALOG))
    assert before.games == ["Assetto Corsa Competizione"]
    assert "BeamNG.drive" in after.games


def test_filtering_needs_no_network(entries, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("no network")
    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    assert search(entries, "rally", tag="rally", only_mine=True) == ["drift"]


def test_five_hundred_presets_are_quick():
    import time
    many = parse([entry(f"p{i}", f"Preset {i}", game=f"Game {i % 20}",
                        wheelbase="CSL DD", tags=[f"t{i % 7}"],
                        description="word " * 100) for i in range(500)])
    texts = {e.id: ls.haystack(e) for e in many}
    start = time.perf_counter()
    for query in ("p", "preset 4", "game 3", "word word", "zzz"):
        filters = ls.Filters(query, tag="t3", only_mine=True)
        [e for e in many if ls.matches(e, filters, CSL_DD, texts[e.id])]
    assert time.perf_counter() - start < 0.5


def test_module_has_no_network_or_gtk():
    source = pathlib.Path(ls.__file__).read_text()
    for word in ("urllib", "socket", "gi.repository", "import community"):
        assert word not in source
