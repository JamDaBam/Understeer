"""Axis diagnostics: every absolute axis of the Fanatec input devices with
its raw and normalized value, the range the device reports, the range seen
since the last reset and the device's flat, fuzz and resolution.

Read only. It shows the axes of the window's InputMonitor (window.inputs)
and opens no input device itself; the window calls update() with its live
display and rebuild() when the devices or the axis roles change.
"""
from gi.repository import Adw, Gtk, Pango

from ..i18n import _
from ..inputs import AXIS_ROLES, kernel_name
from . import layout




def status_text(axis):
    """What the observed range says about the axis."""
    if axis.hi <= axis.lo:
        return _("No range reported")
    if not axis.moved():
        return _("Not moved yet")
    if axis.reached_min() and axis.reached_max():
        return _("Full range reached")
    if axis.reached_min():
        return _("Maximum not reached")
    if axis.reached_max():
        return _("Minimum not reached")
    return _("Neither end reached")


def axis_title(axis, roles, before_hidden=None):
    """The name shown: the role the user chose for the axis on the pedals
    tab (for a hidden axis the one it had before), else the generic name
    the kernel reports."""
    role = roles.get(axis.key, "")
    if role == "hidden":
        role = (before_hidden or {}).get(axis.key, "")
    if role and role != "hidden":
        return _(dict(AXIS_ROLES)[role])
    return _("Axis {name}").format(name=axis.name)


def _value_line(grid, row, title):
    """A small line "title  value" in grid; returns the value label."""
    name = Gtk.Label(label=title, xalign=0, css_classes=["dim-label", "caption"])
    value = Gtk.Label(xalign=1, hexpand=True, css_classes=["numeric", "caption"])
    grid.attach(name, 0, row, 1, 1)
    grid.attach(value, 1, row, 1, 1)
    return value


class AxisDiagnostics:
    """The dialog; window.axis_diagnostics while it is open."""

    def __init__(self, window):
        self.window = window
        self.widgets = {}       # axis key -> dict of labels and the bar
        # Axes hidden on the pedals tab (role "hidden") are hidden here too,
        # unless shown on purpose: a hidden axis may be the faulty one.
        self.show_hidden = False

        self.dialog = window.fit_dialog(Adw.Dialog(title=_("Axis Diagnostics")))
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        reset = Gtk.Button(label=_("Reset Measurements"))
        reset.set_tooltip_text(_("Start the observed ranges anew"))
        reset.connect("clicked", lambda b: self.reset())
        header.pack_start(reset)
        view.add_top_bar(header)

        outer = layout.margins(
            Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6), 18)
        intro = Gtk.Label(
            label=_("Move every axis to both ends. Observed shows the lowest "
                    "and highest value since the last reset. Read only: "
                    "nothing is changed on the device."),
            xalign=0, wrap=True)
        intro.add_css_class("dim-label")
        outer.append(intro)
        self.problem = Gtk.Label(xalign=0, wrap=True)
        self.problem.add_css_class("warning")
        outer.append(self.problem)
        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.append(self.column)
        self.hidden_box = Gtk.Box(spacing=12, margin_top=6)
        self.hidden_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.hidden_switch.connect("notify::active", lambda sw, _p:
                                   self._toggle_hidden(sw.get_active()))
        self.hidden_label = Gtk.Label(xalign=0)
        self.hidden_box.append(self.hidden_switch)
        self.hidden_box.append(self.hidden_label)
        outer.append(self.hidden_box)
        clamp = Adw.Clamp(maximum_size=1100, tightening_threshold=1100,
                          child=outer)
        view.set_content(Gtk.ScrolledWindow(
            child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.dialog.set_child(view)
        self.dialog.connect("closed", lambda d: self._closed())

        window.axis_diagnostics = self
        self.rebuild()
        self.dialog.present(window)

    def _closed(self):
        if self.window.axis_diagnostics is self:
            self.window.axis_diagnostics = None

    def _toggle_hidden(self, show):
        if show != self.show_hidden:
            self.show_hidden = show
            self.rebuild()

    def _hidden(self, axis):
        return self.window.axis_roles.get(axis.key) == "hidden"

    def _set_hidden(self, axis, hide):
        """Hide the axis or show it again, here and on the pedals tab (the
        window saves the role and rebuilds both)."""
        self.window._set_axis_hidden(axis.key, hide)

    def reset(self):
        self.window.inputs.reset_measurements()
        self.update()

    def rebuild(self):
        """A section per device with a small tile per axis; after the
        devices were opened again, one was unplugged or an axis got another
        role."""
        while (child := self.column.get_first_child()) is not None:
            self.column.remove(child)
        self.widgets = {}
        inputs = self.window.inputs
        problems = []
        if inputs.denied:
            problems.append(_("No read access to {devices}. Set up "
                              "permissions in the preferences.").format(
                devices=", ".join(inputs.denied)))
        elif not inputs.all_axes:
            problems.append(self.window._input_problem())
        self.problem.set_label("\n".join(problems))
        self.problem.set_visible(bool(problems))

        hidden = sum(1 for a in inputs.all_axes.values() if self._hidden(a))
        self.hidden_label.set_label(
            _("Show hidden axes ({n})").format(n=hidden))
        self.hidden_box.set_visible(bool(hidden))
        for device in inputs.devices:
            axes = sorted((a for a in inputs.all_axes.values()
                           if a.device == device and
                           (self.show_hidden or not self._hidden(a))),
                          key=lambda a: a.code)
            if axes:
                layout.section(self.column, device)
                grid = layout.tile_grid(min_per_line=1, max_per_line=4)
                for axis in axes:
                    grid.append(self._card(axis))
                self.column.append(grid)
        self.update()

    def _card(self, axis):
        """A small tile: the name (a tick when the full range was seen),
        the value as a bar, three lines of numbers and the status."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       css_classes=["card", "tile", "diag-tile"])
        head = Gtk.Box(spacing=8)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        title = Gtk.Label(label=axis_title(axis, self.window.axis_roles,
                                           self.window.roles_before_hidden),
                          xalign=0, css_classes=["heading"])
        title.set_ellipsize(Pango.EllipsizeMode.END)
        names.append(title)
        kernel = Gtk.Label(
            label=f"{kernel_name(axis.code)} · {axis.code}", xalign=0,
            css_classes=["tile-id"])
        kernel.set_tooltip_text(_("Name and code the driver reports"))
        names.append(kernel)
        done = Gtk.Image.new_from_icon_name("object-select-symbolic")
        done.set_valign(Gtk.Align.START)
        done.add_css_class("success")
        done.set_tooltip_text(_("Full range reached"))
        hidden = self._hidden(axis)
        hide = Gtk.Button(icon_name="view-reveal-symbolic" if hidden
                          else "view-conceal-symbolic",
                          valign=Gtk.Align.START,
                          css_classes=["flat", "circular"])
        hide.set_tooltip_text(_("Show this axis again") if hidden
                              else _("Hide this axis"))
        hide.connect("clicked", lambda b: self._set_hidden(axis, not hidden))
        if hidden:
            card.add_css_class("dim-label")
        head.append(names)
        head.append(done)
        head.append(hide)
        card.append(head)

        line = Gtk.Box(spacing=8)
        bar = layout.level_bar(hexpand=True, valign=Gtk.Align.CENTER)
        normalized = Gtk.Label(xalign=1, width_chars=7,
                               css_classes=["numeric"])
        normalized.set_tooltip_text(_("Normalized"))
        line.append(bar)
        line.append(normalized)
        card.append(line)

        grid = Gtk.Grid(column_spacing=12)
        w = {"bar": bar, "done": done, "hide": hide, "normalized": normalized,
             "raw": _value_line(grid, 0, _("Raw")),
             "device": _value_line(grid, 1, _("Device range")),
             "observed": _value_line(grid, 2, _("Observed"))}
        meta = (_("Flat (deadzone)") + f" {axis.flat} · " + _("Fuzz")
                + f" {axis.fuzz} · " + _("Resolution") + " "
                + (str(axis.resolution) if axis.resolution else _("Not reported")))
        if axis.flat or axis.fuzz or axis.resolution:
            card_meta = Gtk.Label(label=meta, xalign=0, wrap=True,
                                  css_classes=["dim-label", "caption"])
            grid.attach(card_meta, 0, 3, 2, 1)
        card.set_tooltip_text(meta)
        card.append(grid)
        w["status"] = Gtk.Label(xalign=0, css_classes=["caption"])
        w["status"].set_ellipsize(Pango.EllipsizeMode.END)
        card.append(w["status"])
        self.widgets[axis.key] = w
        return card

    def update(self):
        """Show the current values; called with the live display."""
        for key, w in self.widgets.items():
            axis = self.window.inputs.all_axes.get(key)
            if axis is None:
                continue
            ranged = axis.hi > axis.lo
            w["bar"].set_value(axis.normalized() if ranged else 0)
            w["bar"].set_sensitive(ranged)
            w["raw"].set_label(str(axis.value))
            w["normalized"].set_label(
                f"{axis.normalized() * 100:.1f} %" if ranged else "—")
            w["device"].set_label(f"{axis.lo} … {axis.hi}")
            observed = f"{axis.observed_min} … {axis.observed_max}"
            if ranged:
                observed += f" ({axis.coverage() * 100:.0f} %)"
            w["observed"].set_label(observed)
            full = ranged and axis.reached_min() and axis.reached_max()
            w["done"].set_opacity(1 if full else 0)
            w["status"].set_label(status_text(axis))
            for css, on in (("success", full), ("dim-label", not full)):
                (w["status"].add_css_class if on
                 else w["status"].remove_css_class)(css)
