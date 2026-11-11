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
