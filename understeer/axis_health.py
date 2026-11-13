"""Health check of the pedals and the steering wheel, without GTK.

Read only: it looks at the axes of the window's InputMonitor and says, in
findings, what their values suggest:

- a pedal that never gets to the end of its travel (blocked, worn, or in
  need of calibrating in the firmware or the game),
- a pedal that doesn't come back to its released position when let go
  (a brake that drags),
- a pedal whose value jitters while nobody touches it (dirty
  potentiometer, a load cell that drifts),
- whether the wheel was turned to both ends, and whether it stands still
  while let go. (Not whether it rests in the centre: nobody sets it
  straight to the exact degree by hand.)

The travel comes from the observed range the axis diagnostics show too;
the rest values from a short measurement the user starts with hands and
feet off (RestMeasurement). Nothing is calibrated or changed.
"""
from dataclasses import dataclass

from .inputs import FULL_RANGE_TOLERANCE

OK = "ok"
PENDING = "pending"     # still to do: press, turn, measure
WARNING = "warning"     # looks like a fault
LEVELS = (OK, PENDING, WARNING)     # from good to bad

# What a finding is about; the page has a text for each
FULL_TRAVEL = "full_travel"
SHORT_TRAVEL = "short_travel"         # value: travel reached, 0..1
NOT_PRESSED = "not_pressed"
REST_OK = "rest_ok"
REST_OFFSET = "rest_offset"           # value: travel at rest, 0..1
NOISY = "noisy"                       # value: jitter at rest, 0..1 of the range
QUIET = "quiet"
REST_PENDING = "rest_pending"
BOTH_ENDS = "both_ends"
NOT_TURNED = "not_turned"
MOVED = "moved"                       # value: steering moved at rest, 0..1

REST_SECONDS = 3
PRESS_MIN = 0.05        # travel below this counts as not pressed yet (jitter)
REST_LIMIT = 0.02       # travel at rest still counted as released
NOISE_LIMIT = 0.01      # jitter at rest still counted as quiet


@dataclass(frozen=True)
class Finding:
    level: str
    kind: str
    value: float | None = None


@dataclass(frozen=True)
class Rest:
    """What one axis did during the rest measurement."""
    lo: int
    hi: int
    mean: float


def travel(axis):
    """How far toward its pressed end the pedal was seen, 0..1."""
    span = axis.hi - axis.lo
    if span <= 0:
        return 0.0
    far = (axis.hi - axis.observed_min if axis.inverted
           else axis.observed_max - axis.lo)
    return min(max(far / span, 0.0), 1.0)


def _rest_travel(axis, rest):
    """Pedal travel 0..1 at its most pressed during the measurement."""
    span = axis.hi - axis.lo
    far = axis.hi - rest.lo if axis.inverted else rest.hi - axis.lo
    return min(max(far / span, 0.0), 1.0)


def check_pedal(axis, rest=None):
    """Findings for a pedal (or another axis with a travel)."""
    found = []
    if travel(axis) < PRESS_MIN:
        found.append(Finding(PENDING, NOT_PRESSED))
    elif travel(axis) >= 1 - FULL_RANGE_TOLERANCE:
        found.append(Finding(OK, FULL_TRAVEL))
    else:
        found.append(Finding(WARNING, SHORT_TRAVEL, travel(axis)))
    if rest is None:
        found.append(Finding(PENDING, REST_PENDING))
        return found
    at_rest = _rest_travel(axis, rest)
    found.append(Finding(WARNING, REST_OFFSET, at_rest) if at_rest > REST_LIMIT
                 else Finding(OK, REST_OK))
    noise = (rest.hi - rest.lo) / (axis.hi - axis.lo)
    found.append(Finding(WARNING, NOISY, noise) if noise > NOISE_LIMIT
                 else Finding(OK, QUIET))
    return found


def check_steering(axis, rest=None):
    """Findings for the steering axis: turned to both ends, and still
    while let go (where it stands doesn't matter)."""
    found = [Finding(OK, BOTH_ENDS) if axis.reached_min() and axis.reached_max()
             else Finding(PENDING, NOT_TURNED)]
    if rest is None:
        found.append(Finding(PENDING, REST_PENDING))
        return found
    moved = (rest.hi - rest.lo) / (axis.hi - axis.lo)
    found.append(Finding(WARNING, MOVED, moved) if moved > NOISE_LIMIT
                 else Finding(OK, QUIET))
    return found


def ready(findings):
    """The axis was moved through its whole range: its findings count."""
    return any(f.kind in (FULL_TRAVEL, BOTH_ENDS) for f in findings)


def worst(findings):
    """The worst level among findings (OK for none)."""
    return max((f.level for f in findings), key=LEVELS.index, default=OK)


PEDAL_ROLES = {"throttle", "brake", "clutch", "handbrake"}


def checked_axes(monitor, roles):
    """[(axis, is_steering)] to check: the steering axis first, then the
    axes the user gave a pedal role on the pedals tab. Axes without a role
    are not checked: wheels and pedals report axes nothing is connected to."""
    steering = monitor.steering_axis()
    axes = [a for a in monitor.axes.values() if a.hi > a.lo and (
        a is steering or roles.get(a.key) in PEDAL_ROLES)]
    axes.sort(key=lambda a: (a is not steering, monitor.devices.index(a.device)
                             if a.device in monitor.devices else 99, a.code))
    return [(a, a is steering) for a in axes]


def check(monitor, roles, rests):
    """{axis key: (axis, is_steering, [Finding])} for checked_axes()."""
    return {axis.key: (axis, steering,
                       (check_steering if steering else check_pedal)(
                           axis, rests.get(axis.key)))
            for axis, steering in checked_axes(monitor, roles)}


class RestMeasurement:
    """Values of the axes while nobody touches them; sample() is called
    regularly by the caller for REST_SECONDS."""

    def __init__(self, axes):
        # key -> [lowest, highest, sum, samples]; only what sample() saw
        self._seen = {a.key: None for a in axes}

    def sample(self, axes):
        for axis in axes:
            if axis.key not in self._seen:
                continue        # plugged in meanwhile: not measured
            seen = self._seen[axis.key]
            if seen is None:
                self._seen[axis.key] = [axis.value, axis.value, axis.value, 1]
                continue
            seen[0] = min(seen[0], axis.value)
            seen[1] = max(seen[1], axis.value)
            seen[2] += axis.value
            seen[3] += 1

    def result(self, axes):
        """{key: Rest} of the axes that were measured and are still there."""
        present = {a.key for a in axes}
        return {key: Rest(seen[0], seen[1], seen[2] / seen[3])
                for key, seen in self._seen.items()
                if seen and key in present}
