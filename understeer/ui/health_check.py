"""The health check in the axis diagnostics: the rest measurement, the findings of every axis as lines with an icon and the summary
above the tiles (see axis_health for what is checked).

Read only. It uses the axes of the window's InputMonitor (window.inputs)
and changes nothing on the device.
"""
import math

from gi.repository import GLib, Gtk

from .. import axis_health as health
from ..i18n import _

SAMPLE_MS = 50          # rest measurement: how often the values are read

ICONS = {health.OK: ("object-select-symbolic", "success"),
         health.PENDING: ("view-more-horizontal-symbolic", "dim-label"),
         health.WARNING: ("dialog-warning-symbolic", "warning")}


def finding_text(finding, angle=None):
    """One line for a finding. angle: steering angle of the wheelbase in
    degrees, to tell a movement of the wheel in degrees."""
    kind, v = finding.kind, finding.value
    if kind == health.MOVED and angle:
        return _("Moved by {n}° during the rest measurement. Let go of the "
                 "wheel and measure again; if it moves by itself, check the "
                 "force feedback.").format(n=f"{v * angle:.0f}")
    if kind == health.SHORT_TRAVEL:
        return _("Reached at most {n} %. Press it fully down; if it gets no "
                 "further, it may be blocked or need calibrating in the "
                 "firmware or the game.").format(n=f"{v * 100:.0f}")
    if kind == health.REST_OFFSET:
        return _("At {n} % when released: games may take it as slightly "
                 "pressed, e.g. as a dragging brake.").format(n=f"{v * 100:.1f}")
    if kind == health.MOVED:
        return _("Moved by {n} % during the rest measurement. Let go of the "
                 "wheel and measure again; if it moves by itself, check the "
                 "force feedback.").format(n=f"{v * 100:.1f}")
    if kind == health.NOISY:
        return _("Jitters by {n} % at rest: often a dirty potentiometer or a "
                 "drifting load cell.").format(n=f"{v * 100:.1f}")
    return {
        health.FULL_TRAVEL: _("Ready: full travel reached"),
        health.NOT_PRESSED: _("Press it fully down once"),
        health.REST_OK: _("Back at 0 % when released"),
        health.QUIET: _("Steady at rest"),
        health.REST_PENDING: _("Measure the rest values to check this"),
        health.BOTH_ENDS: _("Ready: turned to both ends"),
        health.NOT_TURNED: _("Turn it to both ends once"),
    }[kind]


def lines(findings, angle=None):
    """[(level, text)], the worst first."""
    order = sorted(findings, key=lambda f: -health.LEVELS.index(f.level))
    return [(f.level, finding_text(f, angle)) for f in order]


def summary_text(results):
    """One line about all checked axes."""
    levels = [health.worst(found) for _a, _s, found in results.values()]
    warnings = levels.count(health.WARNING)
    if warnings:
        return _("Health check: possible faults on {n} of {total} axes").format(
            n=warnings, total=len(levels))
    if health.PENDING in levels:
        return _("Health check not finished yet: measure the rest values and "
                 "move every axis to its end")
    return _("Health check: no faults found")


def finding_box():
    """The place in a tile for its finding lines; see show_lines()."""
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)


def show_lines(box, shown):
    """Fill box with a line per (level, text), each with its icon."""
    while (child := box.get_first_child()) is not None:
        box.remove(child)
    for level, text in shown:
        name, css = ICONS[level]
        line = Gtk.Box(spacing=6)
        icon = Gtk.Image.new_from_icon_name(name)
        icon.set_valign(Gtk.Align.START)
        icon.add_css_class(css)
        line.append(icon)
        # Wraps in the width the tile has, and asks for none of its own
        label = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=1,
                          hexpand=True, css_classes=["caption"])
        line.append(label)
        box.append(line)


class HealthCheck:
    """Rest values of the checked axes and the findings; on_change() is called
    when a measurement ends."""

    def __init__(self, window, on_change):
        self.window = window
        self.on_change = on_change
        self.rests = {}         # axis key -> health.Rest
        self.measurement = None
        self._timer = 0
        self._ends_at = 0

        self.measure_button = Gtk.Button(label=_("Measure Rest Values"))
        self.measure_button.add_css_class("suggested-action")
        self.measure_button.set_tooltip_text(
            _("For {n} seconds, with hands and feet off").format(
                n=health.REST_SECONDS))
        self.measure_button.connect("clicked", lambda b: self.start_measuring())
        self.status = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.summary = Gtk.Label(xalign=0, wrap=True, css_classes=["heading"])

    def results(self):
        return health.check(self.window.inputs, self.window.axis_roles,
                            self.rests)

    def show_summary(self, results):
        self.summary.set_label(summary_text(results) if results else "")
        self.summary.set_visible(bool(results))
        warn = any(health.worst(f) == health.WARNING
                   for _a, _s, f in results.values())
        (self.summary.add_css_class if warn
         else self.summary.remove_css_class)("warning")
        self.measure_button.set_sensitive(
            not self.measuring and bool(self._axes()))

    def _say(self, text):
        self.status.set_label(text)
        self.status.set_visible(bool(text))

    def reset(self):
        self.stop()
        self.rests = {}
        self._say("")

    # -- rest measurement ---------------------------------------------------
    def _axes(self):
        return [a for a, _s in health.checked_axes(self.window.inputs,
                                                   self.window.axis_roles)]

    @property
    def measuring(self):
        return self.measurement is not None

    def start_measuring(self):
        axes = self._axes()
        if self.measuring or not axes:
            return
        self.measurement = health.RestMeasurement(axes)
        self._ends_at = GLib.get_monotonic_time() + health.REST_SECONDS * 1_000_000
        self.measure_button.set_sensitive(False)
        self._timer = GLib.timeout_add(SAMPLE_MS, self._sample)
        self._sample()

    def _sample(self):
        if self.measurement is None:
            self._timer = 0
            return GLib.SOURCE_REMOVE
        axes = self._axes()
        self.measurement.sample(axes)
        left = (self._ends_at - GLib.get_monotonic_time()) / 1_000_000
        if left > 0:
            self._say(_("Measuring, don't touch the pedals or the wheel… "
                        "{n} s").format(
                n=math.ceil(left)))
            return GLib.SOURCE_CONTINUE
        self.rests.update(self.measurement.result(axes))
        self.stop()
        self._say(_("Rest values measured"))
        self.on_change()
        return GLib.SOURCE_REMOVE

    def finish_measuring(self):
        """Take the samples so far as the result (for tests)."""
        self._ends_at = 0
        self._sample()

    def stop(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.measurement = None
        self.measure_button.set_sensitive(True)

    def leave(self):
        """The tab was left: a measurement nobody watches doesn't count."""
        if self.measuring:
            self.stop()
            self._say(_("Measurement cancelled"))
