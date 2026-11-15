"""Comparing a community preset's wheelbase with the connected one."""
import pytest

from understeer import compatibility as c
from understeer.library import Meta

CSL_DD = "/sys/class/ftec_tuning/0003:0EB7:0020.0005"
DD1 = "/sys/class/ftec_tuning/0003:0EB7:0006.0001"
CSL_ELITE = "/sys/class/ftec_tuning/0003:0EB7:0E03.0007"
UNKNOWN_PRODUCT = "/sys/class/ftec_tuning/0003:0EB7:0999.0002"


def meta(wheelbase=None, drive=None, torque_nm=None):
    return Meta(None, None, wheelbase, drive, torque_nm, None, ())


# --- the connected wheelbase -------------------------------------------------

def test_connected_wheelbase_from_the_folder_name_only(tmp_path):
    # The folder doesn't exist: nothing is read from it
    assert c.connected(CSL_DD) == c.Device("CSL DD", 0x0020, "direct_drive")
    assert c.connected(CSL_ELITE) == c.Device("CSL Elite Wheel Base", 0x0E03, None)


def test_no_wheelbase():
    assert c.connected(None) is None
    assert c.connected("") is None


@pytest.mark.parametrize("path", [UNKNOWN_PRODUCT, "/sys/class/ftec_tuning/odd"])
def test_unknown_wheelbase(path):
    assert c.connected(path).model is None


# --- model names ---------------------------------------------------------------

@pytest.mark.parametrize("text, model", [
    ("CSL DD", "CSL DD"), ("csl dd", "CSL DD"), ("CSL-DD", "CSL DD"),
    ("  Podium Wheel Base DD1 ", "Podium Wheel Base DD1"),
    ("clubsport wheel base v2.5", "ClubSport Wheel Base V2.5"),
])
def test_known_models(text, model):
    assert c.known_model(text) == model


@pytest.mark.parametrize("text", [
    None, "", "CSL Elite",          # fits two models: with and without PS4
    "ClubSport Wheel Base",          # V2 or V2.5
    "GT DD Pro", "ClubSport DD",     # not a name Understeer has
    "DD", "CSL DD 8Nm",              # a part, or more than the name
    "Podium Steering Wheel BMW M4 GT3",   # a steering wheel, not a wheelbase
])
def test_ambiguous_or_unknown_names(text):
    assert c.known_model(text) is None


# --- classification ---------------------------------------------------------------

def test_exact_model_match():
    result = c.classify(meta("csl dd"), c.connected(CSL_DD))
    assert (result.level, result.reason, result.preset_model) == \
        (c.SAME, None, "CSL DD")


def test_different_known_model():
    result = c.classify(meta("Podium Wheel Base DD1"), c.connected(CSL_DD))
    assert (result.level, result.preset_model) == (c.DIFFERENT,
                                                   "Podium Wheel Base DD1")


def test_same_drive_but_different_model_is_different():
    result = c.classify(meta("Podium Wheel Base DD1", "direct_drive"),
                        c.connected(CSL_DD))
    assert (result.level, result.same_drive) == (c.DIFFERENT, True)


def test_same_torque_but_different_model_is_different():
    result = c.classify(meta("Podium Wheel Base DD1", None, 8),
                        c.connected(CSL_DD))
    assert result.level == c.DIFFERENT and result.same_drive is None


def test_other_drive_is_told():
    result = c.classify(meta("CSL Elite Wheel Base", "belt"), c.connected(CSL_DD))
    assert (result.level, result.same_drive) == (c.DIFFERENT, False)


def test_belt_drive_of_the_connected_one_is_not_guessed():
    result = c.classify(meta("CSL DD", "direct_drive"), c.connected(CSL_ELITE))
    assert (result.level, result.same_drive) == (c.DIFFERENT, None)


def test_drive_alone_never_makes_a_match():
    for drive in ("direct_drive", "belt"):
        assert c.classify(meta(None, drive, 8), c.connected(CSL_DD)).level == c.UNKNOWN
        assert c.classify(meta("GT DD Pro", drive), c.connected(CSL_DD)).level \
            == c.UNKNOWN


@pytest.mark.parametrize("wheelbase, path, reason", [
    ("CSL DD", None, c.NO_DEVICE),
    ("CSL DD", UNKNOWN_PRODUCT, c.UNKNOWN_DEVICE),
    (None, CSL_DD, c.NO_MODEL),
    ("", CSL_DD, c.NO_MODEL),
    ("CSL Elite", CSL_ELITE, c.UNKNOWN_MODEL),      # ambiguous
    ("GT DD Pro", CSL_DD, c.UNKNOWN_MODEL),
])
def test_unknown(wheelbase, path, reason):
    result = c.classify(meta(wheelbase), c.connected(path))
    assert (result.level, result.reason) == (c.UNKNOWN, reason)


def test_disconnect_and_reconnect():
    m = meta("CSL DD")
    assert c.classify(m, c.connected(CSL_DD)).level == c.SAME
    assert c.classify(m, c.connected(None)).level == c.UNKNOWN
    # Plugged in again: another number after the dot, the same model
    assert c.classify(m, c.connected(CSL_DD.replace(".0005", ".0009"))).level == c.SAME


def test_nothing_is_read_from_the_device(monkeypatch):
    def no_io(*args, **kwargs):
        raise AssertionError("no file access")
    monkeypatch.setattr("builtins.open", no_io)
    monkeypatch.setattr("os.path.exists", no_io)
    monkeypatch.setattr("os.listdir", no_io)
    c.classify(meta("CSL DD"), c.connected(CSL_DD))
