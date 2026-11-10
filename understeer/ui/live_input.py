"""Live input on the Tuning view: steering angle and pedal axes."""
from gi.repository import GLib, Gtk

from .. import config
from ..i18n import _
from ..inputs import AXIS_ROLES
from .live import WheelView

STRIP_PEDALS = 4        # pedals shown in the live strip


class LiveInputMixin:
    """Part of TunerWindow: the live strip above the tabs and the pedal
    axes on the pedals tab. Uses self.inputs (an InputMonitor)."""

    def _build_live_strip(self):
        """Steering angle and pedals, always in view above the tabs."""
        self.live_strip = strip = Gtk.Box(spacing=18)
        strip.add_css_class("card")
        strip.add_css_class("tile")
        strip.add_css_class("live-tile")
        steering = Gtk.Box(spacing=18, hexpand=True)
        self.wheel_view = WheelView("understeer-steering-symbolic")
        steering.append(self.wheel_view)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       hexpand=True, valign=Gtk.Align.CENTER)
        caption = Gtk.Label(label=_("Current Angle"), xalign=0)
        caption.add_css_class("dim-label")
        self.steer_label = Gtk.Label(xalign=0)
        self.steer_label.add_css_class("live-value")
        self.steer_adj = Gtk.Adjustment(lower=-1, upper=1)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                          adjustment=self.steer_adj)
        scale.set_draw_value(False)
        scale.set_has_origin(False)
        scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
        scale.set_can_target(False)     # display only
        scale.set_focusable(False)
        self.steer_status = Gtk.Label(xalign=0, wrap=True)
        self.steer_status.add_css_class("dim-label")
        for w in (caption, self.steer_label, scale, self.steer_status):
            info.append(w)
        steering.append(info)
        strip.append(steering)

        self.live_divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        strip.append(self.live_divider)
        self.strip_pedals = Gtk.Box(spacing=18, homogeneous=True, hexpand=True,
                                    valign=Gtk.Align.CENTER)
        strip.append(self.strip_pedals)
        self.strip_widgets = {}
        return strip

    def _build_live_pedals(self, column):
        reset = Gtk.Button(label=_("Reset Max"))
        reset.add_css_class("flat")
        reset.connect("clicked", lambda b: self._reset_peaks())
        self._section(column, _("Pedal Axes"),
                      _("Press a pedal to see which bar moves and choose "
                        "what it is. Max shows the highest value since the "
                        "last reset."), suffix=reset)
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        tile.add_css_class("card")
        tile.add_css_class("tile")
        tile.add_css_class("live-tile")
        self.axes_empty = Gtk.Label(xalign=0, wrap=True)
        self.axes_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                     homogeneous=True, min_children_per_line=2,
                                     max_children_per_line=8,
                                     column_spacing=18, row_spacing=18)
        self.hidden_box = Gtk.Box(spacing=12)
        self.hidden_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.hidden_switch.connect("notify::active", lambda sw, _p:
                                   self._toggle_hidden(sw.get_active()))
        self.hidden_label = Gtk.Label(xalign=0)
        self.hidden_box.append(self.hidden_switch)
        self.hidden_box.append(self.hidden_label)
        for w in (self.axes_empty, self.axes_flow, self.hidden_box):
            tile.append(w)
        column.append(tile)

    def _start_inputs(self):
        if getattr(self, "_in_background", False):
            return          # nobody sees the live display; show_again() starts it
        self.inputs.start()
        self._rebuild_axes_list()
        self._update_inputs()
        self._update_permission_row()

    def _input_problem(self):
        if self.inputs.denied:
            return _("No read access to the input device. Set up permissions "
                     "in the preferences.")
        return _("No Fanatec input device found.")

    def _rebuild_axes_list(self):
        self.axes_flow.remove_all()
        self.axis_widgets = {}
        steering = self.inputs.steering_axis()
        axes = [a for a in self.inputs.axes.values() if a is not steering]
        self.axes_empty.set_label(self._input_problem())
        self.axes_empty.set_visible(not axes)
        self.axes_flow.set_visible(bool(axes))
        self.hidden_box.set_visible(False)
        if not axes:
            self._rebuild_strip_pedals(axes)
            return

        role_keys = [k for k, _l in AXIS_ROLES]
        hidden = 0
        for axis in axes:
            role = self.axis_roles.get(axis.key, "")
            if role == "hidden":
                hidden += 1
                if not self.show_hidden_axes:
                    continue
            label = dict(AXIS_ROLES).get(role) if role and role != "hidden" else None
            pedal = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            pedal.add_css_class("pedal")
            name = Gtk.Label(label=_(label) if label else
                             _("Axis {name}").format(name=axis.name))
            name.add_css_class("heading")
            bar = Gtk.LevelBar(min_value=0, max_value=1,
                               orientation=Gtk.Orientation.VERTICAL,
                               inverted=True, halign=Gtk.Align.CENTER)
            for offset in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH,
                           Gtk.LEVEL_BAR_OFFSET_FULL):
                bar.remove_offset_value(offset)
            value = Gtk.Label()
            value.add_css_class("pedal-value")
            peak = Gtk.Label()
            peak.add_css_class("dim-label")
            choose = Gtk.DropDown.new_from_strings(
                [_(l) for _k, l in AXIS_ROLES])
            choose.set_selected(role_keys.index(role) if role in role_keys else 0)
            choose.set_tooltip_text(_("What this axis is"))
            choose.connect("notify::selected", lambda d, _p, k=axis.key:
                           self._set_axis_role(k, role_keys[d.get_selected()]))
            source = Gtk.Label(label=f"{axis.device} · {axis.name}")
            source.set_ellipsize(3)             # Pango.EllipsizeMode.END
            source.add_css_class("tile-id")
            for w in (name, bar, value, peak, choose, source):
                pedal.append(w)
            self.axes_flow.append(pedal)
            self.axis_widgets[axis.key] = (bar, value, peak)

        if hidden:
            self.hidden_label.set_label(
                _("Show hidden axes ({n})").format(n=hidden))
            self.hidden_box.set_visible(True)
        self._rebuild_strip_pedals(axes)
        self._update_inputs()

    def _rebuild_strip_pedals(self, axes):
        """The pedals in the live strip: assigned ones in the order of
        AXIS_ROLES, else the axes not hidden, at most STRIP_PEDALS."""
        while (child := self.strip_pedals.get_first_child()) is not None:
            self.strip_pedals.remove(child)
        self.strip_widgets = {}
        order = [k for k, _l in AXIS_ROLES if k and k != "hidden"]
        roles = {a.key: self.axis_roles.get(a.key, "") for a in axes}
        shown = sorted((a for a in axes if roles[a.key] in order),
                       key=lambda a: order.index(roles[a.key]))
        if not shown:
            shown = [a for a in axes if roles[a.key] != "hidden"]
        if not shown:
            label = Gtk.Label(label=_("No pedals found"), xalign=0)
            label.add_css_class("dim-label")
            self.strip_pedals.append(label)
            return
        for axis in shown[:STRIP_PEDALS]:
            role = roles[axis.key]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            name = Gtk.Label(xalign=0, label=_(dict(AXIS_ROLES)[role]) if role
                             else _("Axis {name}").format(name=axis.name))
            name.add_css_class("dim-label")
            bar = Gtk.LevelBar(min_value=0, max_value=1)
            bar.add_css_class("strip-bar")
            for offset in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH,
                           Gtk.LEVEL_BAR_OFFSET_FULL):
                bar.remove_offset_value(offset)
            value = Gtk.Label(xalign=0)
            value.add_css_class("strip-value")
            for w in (name, bar, value):
                box.append(w)
            self.strip_pedals.append(box)
            self.strip_widgets[axis.key] = (bar, value)

    def _set_axis_role(self, key, role):
        if role:
            self.axis_roles[key] = role
        else:
            self.axis_roles.pop(key, None)
        config.save_axis_roles(self.axis_roles)
        # Rebuild after the dropdown's own signal has finished.
        GLib.idle_add(lambda: self._rebuild_axes_list() and False)

    def _toggle_hidden(self, show):
        if show != self.show_hidden_axes:
            self.show_hidden_axes = show
            GLib.idle_add(lambda: self._rebuild_axes_list() and False)

    def _reset_peaks(self):
        for axis in self.inputs.axes.values():
            axis.peak = axis.fraction()
        self._update_inputs()

    def _on_input(self):
        # Wheels report up to 1000 updates per second; redraw at ~30 fps.
        if not self._input_timer:
            self._input_timer = GLib.timeout_add(33, self._update_inputs)

    def _update_inputs(self):
        self._input_timer = 0
        axis = self.inputs.steering_axis()
        if axis:
            c = axis.centered()
            self.steer_adj.set_value(c)
            # The full axis range covers the configured steering angle.
            sen = self.rows["SEN"].get() if "SEN" in self.rows else 0
            v, unit = (round(c * sen / 2), "°") if sen > 0 else (round(c * 100), " %")
            self.steer_label.set_label(f"{v:+d}{unit}" if v else f"0{unit}")
            self.steer_status.set_label(_("Turn the wheel to see the angle"))
            self.wheel_view.set_angle(c * (sen / 2 if sen > 0 else 450))
        else:
            self.steer_label.set_label("—")
            self.steer_status.set_label(self._input_problem())
            self.wheel_view.set_angle(0)
        for key, (bar, value, peak) in self.axis_widgets.items():
            a = self.inputs.axes.get(key)
            if a:
                f = a.fraction()
                bar.set_value(f)
                value.set_label(f"{f * 100:.0f} %")
                peak.set_label(_("Max {n} %").format(n=f"{a.peak * 100:.0f}"))
        for key, (bar, value) in self.strip_widgets.items():
            a = self.inputs.axes.get(key)
            if a:
                bar.set_value(a.fraction())
                value.set_label(f"{a.fraction() * 100:.0f} %")
        return GLib.SOURCE_REMOVE
