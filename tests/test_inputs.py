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


# -- axis diagnostics --------------------------------------------------------

def _events(*events):
    """Bytes as read from an evdev node: (type, code, value) each."""
    from understeer.inputs import EVENT
    return b"".join(EVENT.pack(0, 0, t, c, v) for t, c, v in events)


def _monitor_reading(data, *axes, name="Pedals"):
    """A monitor whose (only) device delivers data once."""
    import os
    from understeer.inputs import InputMonitor
    changes = []
    monitor = InputMonitor(lambda: changes.append(True))
    read, write = os.pipe()
    os.write(write, data)
    os.close(write)
    for axis in axes:
        monitor.all_axes[axis.key] = axis
        if axis.code < 0x10 and axis.hi > axis.lo:
            monitor.axes[axis.key] = axis
    monitor._keys = {read: [a.key for a in axes]}
    monitor.devices = [name]
    monitor._on_readable(read, None, name)
    return monitor, read, changes


def test_absinfo_is_parsed():
    import struct
    from understeer.inputs import parse_absinfo
    info = parse_absinfo(struct.pack("6i", 32767, 0, 65535, 16, 128, 0))
    assert info == {"value": 32767, "minimum": 0, "maximum": 65535,
                    "fuzz": 16, "flat": 128, "resolution": 0}


def test_axes_of_all_kinds_are_read(monkeypatch):
    """Steering, throttle, brake and a hat: all in all_axes, only the
    ranged non-hat axes in axes (the live display stays as it was)."""
    import struct
    from understeer import inputs
    reported = {0: (32767, 0, 65535, 0, 0, 0),      # steering
                1: (0, 0, 65535, 0, 0, 0),          # throttle
                2: (65535, 0, 65535, 0, 0, 0),      # brake, resting at max
                5: (0, 0, 0, 0, 0, 0),              # no range
                0x10: (0, -1, 1, 0, 0, 0)}          # hat
    def ioctl(_fd, request, buf, _mutate):
        nr = request & 0xff
        if nr == 0x20 + inputs.EV_ABS:
            buf[:8] = sum(1 << c for c in reported).to_bytes(8, "little")
        else:
            buf[:] = struct.pack("6i", *reported[nr - 0x40])
    monkeypatch.setattr(inputs.fcntl, "ioctl", ioctl)
    monitor = inputs.InputMonitor(lambda: None)
    monitor._read_axes(7, "Wheel")
    assert sorted(a.code for a in monitor.all_axes.values()) == [0, 1, 2, 5, 0x10]
    assert sorted(a.code for a in monitor.axes.values()) == [0, 1, 2]
    hat = monitor.all_axes["Wheel:16"]
    assert (hat.lo, hat.hi, hat.name) == (-1, 1, "Hat0X")
    assert monitor._keys[7] == [f"Wheel:{c}" for c in (0, 1, 2, 5, 0x10)]


def test_metadata_is_kept():
    axis = Axis("Wheel", 0, 0, 65535, 100, fuzz=16, flat=128, resolution=5)
    assert (axis.fuzz, axis.flat, axis.resolution) == (16, 128, 5)


def test_normalized_is_not_inverted():
    axis = Axis("Pedals", 2, 0, 1000, 1000)     # rests at the maximum
    assert axis.inverted and axis.fraction() == 0
    assert axis.normalized() == 1
    axis.value = 250
    assert axis.normalized() == 0.25


def test_min_max_are_tracked():
    from understeer.inputs import EV_ABS
    throttle = Axis("Pedals", 1, 0, 1000, 0)
    brake = Axis("Pedals", 2, 0, 1000, 1000)
    monitor, _fd, changes = _monitor_reading(_events(
        (EV_ABS, 1, 400), (EV_ABS, 1, 1000), (EV_ABS, 1, 200),
        (EV_ABS, 2, 300)), throttle, brake)
    assert changes
    assert (throttle.value, throttle.observed_min, throttle.observed_max) == (200, 0, 1000)
    assert throttle.reached_min() and throttle.reached_max()
    assert throttle.coverage() == 1
    assert (brake.observed_min, brake.observed_max) == (300, 1000)
    assert brake.reached_max() and not brake.reached_min()
    assert brake.coverage() == 0.7
    assert throttle.peak == 1      # the live display's max goes on as before


def test_additional_axes_are_tracked():
    from understeer.inputs import EV_ABS
    hat = Axis("Wheel", 0x10, -1, 1, 0)
    monitor, _fd, _c = _monitor_reading(_events(
        (EV_ABS, 0x10, -1), (EV_ABS, 0x10, 1)), hat, name="Wheel")
    assert (hat.observed_min, hat.observed_max) == (-1, 1)
    assert "Wheel:16" not in monitor.axes


def test_reset_measurements():
    from understeer.inputs import EV_ABS
    throttle = Axis("Pedals", 1, 0, 1000, 0)
    monitor, _fd, _c = _monitor_reading(_events(
        (EV_ABS, 1, 1000), (EV_ABS, 1, 300)), throttle)
    monitor.reset_measurements()
    assert (throttle.observed_min, throttle.observed_max) == (300, 300)
    assert not throttle.moved()
    assert throttle.coverage() == 0


def test_range_near_the_ends_counts_as_reached():
    axis = Axis("Pedals", 1, 0, 1000, 5)
    axis.update(995)
    assert axis.reached_min() and axis.reached_max()
    axis = Axis("Pedals", 1, 0, 1000, 50)
    axis.update(900)
    assert not axis.reached_min() and not axis.reached_max()


def test_axis_without_range():
    axis = Axis("Wheel", 5, 0, 0, 0)
    assert axis.coverage() == 0 and axis.normalized() == 0


def test_unplugged_device_leaves_the_diagnostics():
    import os
    from understeer.inputs import InputMonitor
    monitor = InputMonitor(lambda: None, lambda: None)
    read, write = os.pipe()
    os.close(write)
    hat = Axis("Pedals", 0x10, -1, 1, 0)
    wheel = Axis("Wheel", 0, 0, 1000, 500)
    monitor.all_axes = {hat.key: hat, wheel.key: wheel}
    monitor.devices = ["Wheel", "Pedals"]
    monitor._keys = {read: [hat.key]}
    monitor._on_readable(read, None, "Pedals")
    assert list(monitor.all_axes) == [wheel.key]
    assert monitor.devices == ["Wheel"]


def test_status_text():
    pytest.importorskip("gi.repository.Adw")
    from understeer.i18n import _
    from understeer.ui.axis_diagnostics import status_text
    axis = Axis("Pedals", 1, 0, 1000, 0)
    assert status_text(axis) == _("Not moved yet")
    axis.update(500)
    assert status_text(axis) == _("Maximum not reached")
    axis.update(1000)
    assert status_text(axis) == _("Full range reached")
    assert status_text(Axis("Wheel", 5, 0, 0, 0)) == _("No range reported")


def test_kernel_names():
    from understeer.inputs import kernel_name
    assert kernel_name(6) == "ABS_THROTTLE"
    assert kernel_name(0x10) == "ABS_HAT0X"
    assert kernel_name(0x28) == "ABS_MISC"
    assert kernel_name(0x30) == "ABS_0x30"


def test_diagnostics_show_the_chosen_role():
    """An axis the kernel calls Throttle but the user made the handbrake
    is shown as the handbrake."""
    pytest.importorskip("gi.repository.Adw")
    from understeer.i18n import _
    from understeer.ui.axis_diagnostics import axis_title
    axis = Axis("Wheel", 6, 0, 255, 0)
    assert axis_title(axis, {axis.key: "handbrake"}) == _("Handbrake")
    assert axis_title(axis, {}) == _("Axis {name}").format(name="Throttle")
    assert axis_title(axis, {axis.key: "hidden"}) == _("Axis {name}").format(name="Throttle")
    assert axis_title(axis, {axis.key: "hidden"},
                      {axis.key: "handbrake"}) == _("Handbrake")
