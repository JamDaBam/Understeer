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


# -- button diagnostics ------------------------------------------------------

def _buttons_reading(data, *buttons, name="Wheel"):
    """A monitor whose (only) device has these buttons and delivers data."""
    import os
    from understeer.inputs import InputMonitor
    changes = []
    monitor = InputMonitor(lambda: changes.append(True), lambda: None)
    read, write = os.pipe()
    os.write(write, data)
    os.close(write)
    monitor.buttons = {b.key: b for b in buttons}
    monitor._keys = {read: [b.key for b in buttons]}
    monitor.devices = [name]
    monitor._on_readable(read, None, name)
    return monitor, changes


def _buttons(*codes, device="Wheel"):
    from understeer.inputs import Button
    return [Button(device, code) for code in codes]


def test_single_button_press_and_release():
    from understeer.inputs import EV_KEY
    a, b = _buttons(0x130, 0x131)
    monitor, changes = _buttons_reading(_events((EV_KEY, 0x130, 1)), a, b)
    assert changes and a.pressed and a.tested and a.presses == 1
    assert not b.pressed and not b.tested
    _buttons_reading(_events((EV_KEY, 0x130, 0)), a)
    assert not a.pressed and a.tested       # released, stays tested


def test_simultaneous_presses():
    from understeer.inputs import EV_KEY
    a, b, c = _buttons(0x120, 0x2c0, 0x3b)
    _buttons_reading(_events((EV_KEY, 0x120, 1), (EV_KEY, 0x2c0, 1),
                             (EV_KEY, 0x3b, 1)), a, b, c)
    assert a.pressed and b.pressed and c.pressed
    _buttons_reading(_events((EV_KEY, 0x2c0, 0)), a, b, c)
    assert a.pressed and not b.pressed and c.pressed


def test_repeated_presses_are_counted():
    """Each press counts once; auto repeat (2) and a second "down" without
    a release are no new presses."""
    from understeer.inputs import EV_KEY
    a, = _buttons(0x130)
    _buttons_reading(_events((EV_KEY, 0x130, 1), (EV_KEY, 0x130, 2),
                             (EV_KEY, 0x130, 1), (EV_KEY, 0x130, 0),
                             (EV_KEY, 0x130, 1), (EV_KEY, 0x130, 0)), a)
    assert a.presses == 2 and not a.pressed


def test_short_pulse_is_tested():
    """An encoder sends press and release in one read: still tested."""
    from understeer.inputs import EV_KEY
    a, = _buttons(0x3b)
    _buttons_reading(_events((EV_KEY, 0x3b, 1), (EV_KEY, 0x3b, 0)), a)
    assert a.tested and not a.pressed


def test_key_events_leave_the_axes_alone():
    """A key event of a code the device did not report changes nothing; a
    key and an axis with the same number stay apart."""
    from understeer.inputs import EV_KEY
    a, = _buttons(0x130)
    axis = Axis("Wheel", 0x130, 0, 1000, 0)
    monitor, _c = _buttons_reading(_events((EV_KEY, 0x131, 1),
                                           (EV_KEY, 0x130, 1)), a)
    monitor.all_axes[axis.key] = axis
    assert a.tested and list(monitor.buttons) == [a.key]
    assert axis.value == 0 and axis.key != a.key


def test_reset_buttons():
    from understeer.inputs import EV_KEY
    a, b = _buttons(0x130, 0x131)
    monitor, _c = _buttons_reading(_events(
        (EV_KEY, 0x130, 1), (EV_KEY, 0x131, 1), (EV_KEY, 0x131, 0)), a, b)
    monitor.reset_buttons()
    assert not a.tested and not b.tested
    assert a.pressed            # still held: still shown as held
    a.update(0)
    a.update(1)
    assert a.presses == 1


def test_unplugged_device_loses_its_buttons():
    import os
    from understeer.inputs import Button, InputMonitor
    gone = []
    monitor = InputMonitor(lambda: None, lambda: gone.append(True))
    read, write = os.pipe()
    os.close(write)
    pedal_button = Button("Pedals", 0x120, pressed=True)
    wheel_button = Button("Wheel", 0x130)
    monitor.buttons = {pedal_button.key: pedal_button,
                       wheel_button.key: wheel_button}
    monitor.devices = ["Wheel", "Pedals"]
    monitor._keys = {read: [pedal_button.key]}
    monitor.last_button = pedal_button.key
    monitor._on_readable(read, None, "Pedals")
    assert list(monitor.buttons) == [wheel_button.key]
    assert monitor.last_button is None
    assert monitor.devices == ["Wheel"] and gone == [True]


def test_buttons_are_read_with_their_state(monkeypatch):
    """Every key bit the device reports becomes a button (many, also far
    up), the ones held now start pressed but untested."""
    from understeer import inputs
    reported = [0x120, 0x12c, 0x291, 0x2c0, 0x2ec]
    held = [0x2c0]
    def ioctl(_fd, request, buf, _mutate):
        nr, size = request & 0xff, (request >> 16) & 0x3fff
        assert size == len(buf) == inputs.KEY_MAX // 8 + 1
        bits = held if nr == inputs.EVIOCGKEY else reported
        assert nr in (inputs.EVIOCGKEY, 0x20 + inputs.EV_KEY)
        buf[:] = sum(1 << c for c in bits).to_bytes(size, "little")
    monkeypatch.setattr(inputs.fcntl, "ioctl", ioctl)
    monitor = inputs.InputMonitor(lambda: None)
    monitor._read_buttons(7, "Wheel")
    assert sorted(b.code for b in monitor.buttons.values()) == reported
    pressed = [b.code for b in monitor.buttons.values() if b.pressed]
    assert pressed == held
    assert not any(b.tested for b in monitor.buttons.values())
    assert monitor._keys[7] == [f"Wheel:key:{c}" for c in reported]


def test_many_buttons():
    from understeer.inputs import bits_set
    mask = (1 << 0x300) - (1 << 0x100)
    assert bits_set(mask.to_bytes(96, "little")) == list(range(0x100, 0x300))
    assert bits_set(bytes(96)) == []


def test_key_names():
    from understeer.inputs import key_name
    assert key_name(0x120) == "BTN_TRIGGER"
    assert key_name(0x130) == "BTN_SOUTH"
    assert key_name(0x2c0) == "BTN_TRIGGER_HAPPY1"
    assert key_name(0x2e7) == "BTN_TRIGGER_HAPPY40"
    assert key_name(0x3b) == "KEY_F1"
    assert key_name(0x1d2) == "KEY_FN_F1"
    assert key_name(0x226) == "BTN_GRIPL2"
    assert key_name(0x12c) == "BTN_0x12c"
    assert key_name(0x05) == "KEY_0x005"


def test_button_numbers_and_texts():
    pytest.importorskip("gi.repository.Adw")
    from understeer.i18n import _
    from understeer.inputs import Button
    from understeer.ui.button_diagnostics import (held_text, numbered,
                                                  summary_text)
    buttons = {b.key: b for b in (Button("Wheel", 0x2c0), Button("Wheel", 0x120),
                                  Button("Pedals", 0x120))}
    groups, numbers = numbered(buttons, ["Wheel", "Pedals", "Gone"])
    assert [(d, [b.code for b in bs]) for d, bs in groups] == [
        ("Wheel", [0x120, 0x2c0]), ("Pedals", [0x120])]
    assert numbers == {"Wheel:key:288": 1, "Wheel:key:704": 2,
                       "Pedals:key:288": 1}
    wheel = groups[0][1]
    assert held_text(wheel, numbers) == _("No button held down")
    wheel[0].update(1)
    wheel[1].update(1)
    assert held_text(wheel, numbers) == _("Held down: {buttons}").format(
        buttons=", ".join(_("Button {n}").format(n=n) for n in (1, 2)))
    assert summary_text(wheel) == _("{tested} of {total} buttons tested").format(
        tested=2, total=2)


def test_buttons_are_numbered_as_sdl_does():
    """SDL (native games, Proton) numbers BTN_JOYSTICK and up first, the
    lower codes (e.g. KEY_F1 of a newer driver) after them."""
    pytest.importorskip("gi.repository.Adw")
    from understeer.inputs import Button
    from understeer.ui.button_diagnostics import numbered
    codes = [0x3b, 0x2c0, 0x100, 0x120, 0x1d2, 0x134]
    buttons = {b.key: b for b in (Button("Wheel", c) for c in codes)}
    groups, numbers = numbered(buttons, ["Wheel"])
    assert [b.code for b in groups[0][1]] == [0x120, 0x134, 0x1d2, 0x2c0,
                                              0x3b, 0x100]
    assert numbers["Wheel:key:59"] == 5         # KEY_F1 after the buttons


def test_describe_with_sdl_number():
    pytest.importorskip("gi.repository.Adw")
    from understeer.i18n import _
    from understeer.inputs import Button
    from understeer.ui.button_diagnostics import describe
    button = Button("Wheel", 0x124)
    assert describe(button, {button.key: 5}) == " · ".join((
        _("Button {n}").format(n=5), "SDL 4", "BTN_TOP2", "292"))


def test_last_pressed_button():
    """The button pressed last, for the detail line: a release or auto
    repeat does not change it, a reset or unplugging forgets it."""
    from understeer.inputs import EV_KEY
    a, b = _buttons(0x130, 0x131)
    monitor, _c = _buttons_reading(_events(
        (EV_KEY, 0x130, 1), (EV_KEY, 0x131, 1), (EV_KEY, 0x131, 0),
        (EV_KEY, 0x130, 2)), a, b)
    assert monitor.last_button == b.key
    monitor.reset_buttons()
    assert monitor.last_button is None


def test_last_text():
    pytest.importorskip("gi.repository.Adw")
    from understeer.i18n import _
    from understeer.inputs import Button
    from understeer.ui.button_diagnostics import last_text
    button = Button("Wheel", 0x2e7)
    button.update(1)
    assert last_text(button, {button.key: 56}) == _(
        "Last pressed: {button} · pressed {n}×").format(
        button=" · ".join((_("Button {n}").format(n=56), "SDL 55",
                           "BTN_TRIGGER_HAPPY40", "743")), n=1)
    assert last_text(None, {}) == _("Press a button to see its name and code")
