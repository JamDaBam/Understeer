"""One tile per setting: icon, name, large value and the control below."""
from gi.repository import Gtk, Pango

from ..i18n import _
from ..settings import format_value
from .layout import margins


class SettingTile:
    """Wraps the widgets of one setting and exposes get/set of its value.

    `row` is the tile widget; its sensitivity tells whether the hardware
    supports the setting.
    """

    def __init__(self, setting, on_change):
        self.setting = setting
        self.on_change = on_change
        self.baseline = None
        self._silent = False
        # Auto (value 0 of a setting with "auto") is no place on the
        # slider: the slider then sits at its end and the value says Auto
        self._auto = False
        self._placing = False       # set() moves the slider, not the user
        kind = setting.get("kind", "slider")

        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       css_classes=["card", "tile"])
        tile.set_tooltip_text(_(setting["desc"]))

        # Icon, name, undo and info
        head = Gtk.Box(spacing=10)
        icon = Gtk.Image.new_from_icon_name(setting.get("icon", "emblem-system-symbolic"))
        icon.add_css_class("tile-icon")
        icon.set_valign(Gtk.Align.START)
        title = Gtk.Label(label=_(setting["label"]), xalign=0, wrap=True,
                          hexpand=True)
        title.add_css_class("tile-title")
        # Small natural widths, so the tile width from the stylesheet
        # decides how many tiles fit next to each other.
        title.set_max_width_chars(8)

        self.revert_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self.revert_btn.set_tooltip_text(_("Undo changes to this setting"))
        self.revert_btn.set_valign(Gtk.Align.START)
        self.revert_btn.add_css_class("flat")
        self.revert_btn.add_css_class("circular")
        self.revert_btn.connect("clicked", lambda b: self.revert())

        info = self.info_btn = Gtk.MenuButton(icon_name="help-about-symbolic")
        info.set_valign(Gtk.Align.START)
        info.add_css_class("flat")
        info.add_css_class("circular")
        info.set_tooltip_text(_("About this setting"))
        pop_label = margins(Gtk.Label(label=_(setting["desc"]), wrap=True,
                                      max_width_chars=40), 8)
        popover = Gtk.Popover()
        popover.set_child(pop_label)
        info.set_popover(popover)
        for w in (icon, title, self.revert_btn, info):
            head.append(w)
        tile.append(head)
        self.head = head

        # Large value with the sysfs name next to it
        value_line = Gtk.Box(spacing=8)
        self.value_label = Gtk.Label(xalign=0, hexpand=True)
        self.value_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.value_label.add_css_class("tile-value")
        self.value_label.set_max_width_chars(4)
        self.id_label = Gtk.Label(label=setting["id"], xalign=1)
        self.id_label.set_valign(Gtk.Align.END)
        self.id_label.set_margin_bottom(6)
        self.id_label.add_css_class("tile-id")
        value_line.append(self.value_label)
        value_line.append(self.id_label)
        tile.append(value_line)
        self.value_line = value_line
        self.compact = False

        # Control
        if kind == "choice":
            self.values = [v for v, _l in setting["choices"]]
            labels = [_(label) for _v, label in setting["choices"]]
            if len(labels) == 2:
                control = Gtk.Box(homogeneous=True)
                control.add_css_class("linked")
                self.toggles = []
                for label in labels:
                    b = Gtk.ToggleButton(label=label)
                    if self.toggles:
                        b.set_group(self.toggles[0])
                    control.append(b)
                    self.toggles.append(b)
                self.toggles[0].set_active(True)
                for b in self.toggles:
                    b.connect("toggled", lambda b: b.get_active() and self._changed())
            else:
                self.combo = Gtk.DropDown.new_from_strings(labels)
                self.combo.connect("notify::selected", lambda *a: self._changed())
                control = self.combo
        else:
            lo, hi = setting["lo"], setting["hi"]
            self.adj = Gtk.Adjustment(value=max(lo, 0), lower=lo, upper=hi,
                                      step_increment=1, page_increment=10)
            control = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                adjustment=self.adj, hexpand=True)
            control.set_draw_value(False)
            if lo < 0 < hi:
                # No fill from the left: 0 ("off") sits in the middle.
                control.set_has_origin(False)
                control.add_mark(0, Gtk.PositionType.BOTTOM, None)
            self.adj.connect("value-changed", lambda *a: self._slid())
        tile.append(control)

        self.row = tile
        self._update_label()

    def set_compact(self, compact):
        """Compact: the value moves up next to the name and the info button
        and the sysfs name are left out (the tooltip still explains the
        setting), so the tile is two rows shorter."""
        if compact == self.compact:
            return
        self.compact = compact
        self.value_label.unparent()
        if compact:
            self.value_label.set_hexpand(False)
            self.value_label.set_valign(Gtk.Align.CENTER)
            self.value_label.set_ellipsize(Pango.EllipsizeMode.NONE)
            self.head.append(self.value_label)
        else:
            self.value_label.set_hexpand(True)
            self.value_label.set_valign(Gtk.Align.FILL)
            self.value_label.set_ellipsize(Pango.EllipsizeMode.END)
            self.value_line.prepend(self.value_label)
        self.head.set_spacing(6 if compact else 10)
        self.info_btn.set_visible(not compact)
        self._update_id_line()
        self._update_label()

    def _update_id_line(self):
        # In compact tiles only "not supported" is shown there.
        self.value_line.set_visible(not self.compact or not self.row.get_sensitive())

    def add_extra(self, widget):
        """Add a widget below the control, e.g. quick select buttons."""
        self.row.append(widget)

    # -- value access ------------------------------------------------------
    def get(self):
        if hasattr(self, "toggles"):
            for value, b in zip(self.values, self.toggles):
                if b.get_active():
                    return value
            return self.values[0]
        if hasattr(self, "combo"):
            return self.values[self.combo.get_selected()]
        if self._auto:
            return 0
        return int(round(self.adj.get_value()))

    def set(self, value, silent=True):
        """Show value; a slider takes the nearest value it has. Unless
        silent, on_change() follows if the value changed."""
        before = self.get()
        self._silent = self._placing = True
        try:
            if hasattr(self, "toggles"):
                if value in self.values:
                    self.toggles[self.values.index(value)].set_active(True)
            elif hasattr(self, "combo"):
                if value in self.values:
                    self.combo.set_selected(self.values.index(value))
            else:
                self._auto = bool(self.setting.get("auto")) and value == 0
                self.adj.set_value(self.adj.get_upper() if self._auto else value)
        finally:
            self._silent = self._placing = False
        self._update_label()
        if not silent and self.get() != before:
            self.on_change(self)

    def set_upper(self, hi):
        """The largest value the hardware takes, e.g. the steering angle
        of the connected wheelbase; a larger value shown is cut to it."""
        if not hasattr(self, "adj") or hi == self.adj.get_upper():
            return
        value = self.get()
        self.adj.set_upper(hi)
        self.set(value)

    def set_baseline(self, value):
        self.baseline = value
        self._update_label()

    def revert(self):
        if self.baseline is not None and self.baseline != self.get():
            self.set(self.baseline, silent=False)

    def set_supported(self, supported):
        self.row.set_sensitive(supported)
        self.id_label.set_label(
            self.setting["id"] if supported else
            _("Not supported by your hardware") + f"  ·  {self.setting['id']}")
        self._update_id_line()

    # -- internals ---------------------------------------------------------
    def _slid(self):
        if not self._placing:
            self._auto = False          # moved by the user: a number again
        self._changed()

    def _changed(self):
        self._update_label()
        if not self._silent:
            self.on_change(self)

    def format(self, v):
        """Human-readable value, e.g. "900°", "70 %", "Peak"."""
        return format_value(self.setting, v)

    def _update_label(self):
        self.value_label.set_label(self.format(self.get()))
        modified = self.baseline is not None and self.get() != self.baseline
        self.revert_btn.set_opacity(1 if modified else 0)
        self.revert_btn.set_sensitive(modified)
        # Compact tiles have no room to keep for it.
        self.revert_btn.set_visible(modified or not self.compact)
        if modified:
            self.row.add_css_class("modified")
        else:
            self.row.remove_css_class("modified")
