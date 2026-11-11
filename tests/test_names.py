from understeer import names
from understeer.names import join_names, short


def test_short_names_stay():
    assert short("Drift") == "Drift"
    assert short("x" * 40) == "x" * 40


def test_long_names_end_in_an_ellipsis():
    name = "Assetto Corsa Competizione Endurance Championship Season 2026"
    cut = short(name)
    assert len(cut) == 40 and cut.endswith("…") and name.startswith(cut[:-1])
    assert short("Assetto Corsa Competizione", 15) == "Assetto Corsa…"   # no space before …
    assert short("x" * 100, 10) == "x" * 9 + "…"


def test_join_names():
    assert join_names(["BeamNG.drive", "y" * 50], 20) == "BeamNG.drive, " + "y" * 19 + "…"


def test_free_name():
    taken = {"GT3", "GT3 (2)"}
    assert names.free_name("Rally", taken.__contains__) == "Rally"
    assert names.free_name("GT3", taken.__contains__) == "GT3 (3)"


def test_one_line():
    assert names.one_line("  Beam\tNG\n Drift ", 80) == "Beam NG Drift"
    assert names.one_line("Spa\x00 GT3", 80) == "Spa GT3"
    assert names.one_line("abcdef", 3) == "abc"


def test_menu_label():
    assert names.menu_label("My_Setup") == "My__Setup"
    assert names.menu_label("x" * 60).endswith("…")
