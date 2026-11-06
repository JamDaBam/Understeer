import pytest

pytest.importorskip("gi")      # inputs.py uses GLib for the device watches
from understeer.inputs import Axis  # noqa: E402


def test_pedal_travel():
    axis = Axis("Pedals", 2, 0, 1000, 0)
    assert axis.fraction() == 0
    axis.value = 250
    assert axis.fraction() == 0.25
    axis.value = 1000
    assert axis.fraction() == 1


def test_pedal_resting_at_maximum_is_inverted():
    axis = Axis("Pedals", 2, 0, 1000, 990)
    assert axis.inverted
    axis.value = 1000
    assert axis.fraction() == 0
    axis.value = 0
    assert axis.fraction() == 1


def test_fraction_is_clamped():
    axis = Axis("Pedals", 2, 0, 100, 0)
    axis.value = 150
    assert axis.fraction() == 1
    axis.value = -20
    assert axis.fraction() == 0


def test_steering_centre():
    axis = Axis("Wheel", 0, 0, 65535, 32767)
    assert axis.centered() == pytest.approx(0, abs=1e-4)
    axis.value = 0
    assert axis.centered() == -1
    axis.value = 65535
    assert axis.centered() == 1


def test_names():
    assert Axis("Pedals", 2, 0, 1, 0).name == "Z"
    assert Axis("Pedals", 40, 0, 1, 0).name == "40"
    assert Axis("Pedals", 2, 0, 1, 0).key == "Pedals:2"
