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


def test_unplugged_device_loses_its_axes():
    """A device that is gone takes its bars with it, not frozen ones."""
    import os
    from understeer.inputs import InputMonitor
    gone = []
    monitor = InputMonitor(lambda: None, lambda: gone.append(True))
    read, write = os.pipe()
    os.close(write)                     # reading gives nothing: unplugged
    pedals = Axis("Pedals", 2, 0, 1000, 0)
    wheel = Axis("Wheel", 0, 0, 1000, 500)
    monitor.axes = {pedals.key: pedals, wheel.key: wheel}
    monitor._keys = {read: [pedals.key]}
    monitor._on_readable(read, None, "Pedals")
    assert list(monitor.axes) == [wheel.key] and gone == [True]
