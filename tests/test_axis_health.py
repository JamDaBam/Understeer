import pytest

pytest.importorskip("gi")      # inputs.py uses GLib for the device watches
from understeer import axis_health as h  # noqa: E402
from understeer.inputs import Axis, InputMonitor  # noqa: E402


def kinds(findings):
    return [(f.level, f.kind) for f in findings]


def pedal(rest_value=0, lo=0, hi=1000):
    return Axis("FANATEC Pedals", 2, lo, hi, rest_value)


def measured(axis, values):
    """Rest of axis after the values were seen while nobody touched it."""
    m = h.RestMeasurement([axis])
    for v in values:
        axis.value = v
        m.sample([axis])
    return m.result([axis])[axis.key]


def test_pedal_not_pressed_yet():
    assert kinds(h.check_pedal(pedal())) == [
        (h.PENDING, h.NOT_PRESSED), (h.PENDING, h.REST_PENDING)]


def test_jitter_is_not_pressing():
    axis = pedal(rest_value=1000)       # rests at its maximum, as Fanatec gas
    for v in (999, 1000, 998, 1000):
        axis.update(v)
    assert axis.moved()
    assert h.check_pedal(axis)[0] == h.Finding(h.PENDING, h.NOT_PRESSED)
    axis.update(900)
    assert h.check_pedal(axis)[0].kind == h.SHORT_TRAVEL


def test_full_travel():
    axis = pedal()
    axis.update(1000)
    assert h.check_pedal(axis)[0] == h.Finding(h.OK, h.FULL_TRAVEL)


def test_full_travel_within_tolerance():
    axis = pedal()
    axis.update(995)
    assert h.check_pedal(axis)[0].kind == h.FULL_TRAVEL


def test_short_travel():
    axis = pedal()
    axis.update(930)
    first = h.check_pedal(axis)[0]
    assert (first.level, first.kind) == (h.WARNING, h.SHORT_TRAVEL)
    assert first.value == pytest.approx(0.93)


def test_inverted_pedal_travel():
    axis = pedal(rest_value=1000)       # rests at its maximum
    assert axis.inverted
    axis.update(400)
    assert h.travel(axis) == pytest.approx(0.6)
    axis.update(0)
    assert h.check_pedal(axis)[0].kind == h.FULL_TRAVEL


def test_quiet_pedal_at_rest():
    axis = pedal()
    axis.update(1000)
    rest = measured(axis, [0, 2, 1, 0, 3])
    assert kinds(h.check_pedal(axis, rest)) == [
        (h.OK, h.FULL_TRAVEL), (h.OK, h.REST_OK), (h.OK, h.QUIET)]
    assert h.worst(h.check_pedal(axis, rest)) == h.OK


def test_dragging_brake():
    axis = pedal()
    rest = measured(axis, [40, 41, 40])
    found = h.check_pedal(axis, rest)
    drag = [f for f in found if f.kind == h.REST_OFFSET][0]
    assert drag.level == h.WARNING and drag.value == pytest.approx(0.041)


def test_noisy_pedal():
    axis = pedal()
    rest = measured(axis, [0, 15, 3, 12])
    noisy = [f for f in h.check_pedal(axis, rest) if f.kind == h.NOISY][0]
    assert noisy.level == h.WARNING and noisy.value == pytest.approx(0.015)
    assert h.worst(h.check_pedal(axis, rest)) == h.WARNING


def test_inverted_pedal_at_rest():
    axis = pedal(rest_value=1000)
    rest = measured(axis, [1000, 999, 1000])
    assert kinds(h.check_pedal(axis, rest))[1:] == [
        (h.OK, h.REST_OK), (h.OK, h.QUIET)]
    rest = measured(axis, [950, 950])
    assert (h.WARNING, h.REST_OFFSET) in kinds(h.check_pedal(axis, rest))


def test_steering_both_ends_and_still_at_rest():
    wheel = Axis("FANATEC Wheel", 0, 0, 65535, 32767)
    assert kinds(h.check_steering(wheel)) == [
        (h.PENDING, h.NOT_TURNED), (h.PENDING, h.REST_PENDING)]
    wheel.update(0)
    assert h.check_steering(wheel)[0].kind == h.NOT_TURNED
    wheel.update(65535)
    assert h.ready(h.check_steering(wheel))
    rest = measured(wheel, [36000, 36010, 36000])   # off centre: no matter
    assert kinds(h.check_steering(wheel, rest)) == [
        (h.OK, h.BOTH_ENDS), (h.OK, h.QUIET)]
    rest = measured(wheel, [30000, 36000])          # turned while measuring
    moved = h.check_steering(wheel, rest)[1]
    assert (moved.level, moved.kind) == (h.WARNING, h.MOVED)
    assert moved.value == pytest.approx(6000 / 65535)


def test_ready_once_the_whole_range_was_seen():
    axis = pedal()
    assert not h.ready(h.check_pedal(axis))
    axis.update(930)
    assert not h.ready(h.check_pedal(axis))
    axis.update(1000)
    assert h.ready(h.check_pedal(axis))


def test_worst_order():
    f = h.Finding
    assert h.worst([]) == h.OK
    assert h.worst([f(h.OK, h.QUIET), f(h.PENDING, h.REST_PENDING)]) == h.PENDING
    assert h.worst([f(h.PENDING, h.NOT_PRESSED), f(h.WARNING, h.NOISY)]) == h.WARNING


def monitor_with(*axes, devices=("FANATEC Wheel", "FANATEC Pedals")):
    monitor = InputMonitor(lambda: None)
    monitor.devices = list(devices)
    for a in axes:
        monitor.axes[a.key] = a
        monitor.all_axes[a.key] = a
    return monitor


def test_checked_axes_steering_and_axes_with_a_role():
    wheel = Axis("FANATEC Wheel", 0, 0, 65535, 32767)
    gas = Axis("FANATEC Pedals", 1, 0, 1000, 0)
    brake = Axis("FANATEC Pedals", 2, 0, 1000, 0)
    spare = Axis("FANATEC Pedals", 3, 0, 1000, 0)  # nothing connected
    flat = Axis("FANATEC Pedals", 5, 0, 0, 0)      # no range
    monitor = monitor_with(brake, spare, gas, flat, wheel)
    roles = {gas.key: "throttle", brake.key: "brake", flat.key: "clutch"}
    result = h.check(monitor, roles, {})
    assert list(result) == [wheel.key, gas.key, brake.key]
    assert result[wheel.key][1] is True
    hidden = dict(roles, **{brake.key: "hidden"})
    assert h.checked_axes(monitor, hidden) == [(wheel, True), (gas, False)]
    assert h.checked_axes(monitor, {}) == [(wheel, True)]
    assert h.checked_axes(monitor, {wheel.key: "hidden"}) == []


def test_no_axes():
    assert h.check(monitor_with(devices=()), {}, {}) == {}


def test_rest_measurement_leaves_out_gone_and_new_axes():
    gas = Axis("FANATEC Pedals", 1, 0, 1000, 0)
    brake = Axis("FANATEC Pedals", 2, 0, 1000, 10)
    m = h.RestMeasurement([gas, brake])
    later = Axis("FANATEC Pedals", 3, 0, 1000, 0)
    m.sample([gas, brake, later])
    result = m.result([gas, later])          # brake unplugged meanwhile
    assert list(result) == [gas.key]
    assert result[gas.key] == h.Rest(0, 0, 0)


def test_rest_measurement_without_samples():
    gas = Axis("FANATEC Pedals", 1, 0, 1000, 0)
    assert h.RestMeasurement([gas]).result([gas]) == {}


def test_report_text_only_when_measured():
    wheel = Axis("FANATEC Wheel", 0, 0, 65535, 32767)
    gas = Axis("FANATEC Pedals", 1, 0, 1000, 1000)      # rests at the maximum
    brake = Axis("FANATEC Pedals", 2, 0, 1000, 0)
    monitor = monitor_with(wheel, gas, brake)
    roles = {gas.key: "throttle", brake.key: "brake"}
    assert h.report_text(h.check(monitor, roles, {}), roles) == ""
    assert h.report_text({}, roles) == ""
    gas.update(0)
    brake.update(930)
    rests = {brake.key: measured(brake, [40, 41])}
    text = h.report_text(h.check(monitor, roles, rests), roles)
    assert text.splitlines() == [
        "Possible faults on 1 of 3 axes",
        "Steering (FANATEC Wheel, X): not turned to both ends yet · rest not "
        "measured · range 0…65535, seen 32767…32767",
        "Throttle (FANATEC Pedals, Y): full travel · rest not measured · "
        "range 0…1000, seen 0…1000",
        "Brake (FANATEC Pedals, Z): (!) reached at most 93 % · (!) at 4.1 % "
        "when released · still at rest · range 0…1000, seen 0…930",
    ]


def test_report_text_without_faults():
    gas = Axis("FANATEC Pedals", 1, 0, 1000, 0)
    gas.update(1000)
    roles = {gas.key: "throttle"}
    monitor = monitor_with(gas)
    text = h.report_text(h.check(monitor, roles, {}), roles)
    assert text.splitlines()[0] == "No faults found (not finished)"
