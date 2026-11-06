"""One row per setting: a slider or a choice with undo and info buttons."""
from gi.repository import Adw, Gtk

from ..i18n import _
from ..settings import format_value


class SettingRow:
    """Wraps the widgets of one setting and exposes get/set of its value."""

    def __init__(self, setting, on_change):
        self.setting = setting
        self.on_change = on_change
        self.baseline = None
        self._silent = False
        kind = setting.get("kind", "slider")

        if kind == "choice":
            self.values = [v for v, _l in setting["choices"]]
            row = Adw.ComboRow()
            row.set_model(Gtk.StringList.new(
                [_(label) for _v, label in setting["choices"]]))
            row.connect("notify::selected", lambda *a: self._changed())
            self.combo = row
        else:
            row = Adw.ActionRow()
            lo, hi = setting["lo"], setting["hi"]
            self.adj = Gtk.Adjustment(value=max(lo, 0), lower=lo, upper=hi,
                                      step_increment=1, page_increment=10)
            scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                              adjustment=self.adj)
            scale.set_size_request(240, -1)
            scale.set_valign(Gtk.Align.CENTER)
            scale.set_draw_value(False)
            if lo < 0 < hi:
                # No fill from the left: 0 ("off") sits in the middle.
                scale.set_has_origin(False)
                scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
            self.value_label = Gtk.Label(xalign=1)
            self.value_label.set_width_chars(9)
            self.value_label.add_css_class("numeric")
            row.add_suffix(scale)
            row.add_suffix(self.value_label)
            self.adj.connect("value-changed", lambda *a: self._changed())

        row.set_title(_(setting["label"]))
        row.set_subtitle(setting["id"])
        row.set_tooltip_text(_(setting["desc"]))

        self.revert_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self.revert_btn.set_tooltip_text(_("Undo changes to this setting"))
        self.revert_btn.set_valign(Gtk.Align.CENTER)
        self.revert_btn.add_css_class("flat")
        self.revert_btn.connect("clicked", lambda b: self.revert())
        row.add_suffix(self.revert_btn)

        info = Gtk.MenuButton(icon_name="help-about-symbolic")
        info.set_valign(Gtk.Align.CENTER)
        info.add_css_class("flat")
        info.set_tooltip_text(_("About this setting"))
        pop_label = Gtk.Label(label=_(setting["desc"]), wrap=True,
                              max_width_chars=40)
        for side in ("top", "bottom", "start", "end"):
            getattr(pop_label, f"set_margin_{side}")(8)
        popover = Gtk.Popover()
        popover.set_child(pop_label)
        info.set_popover(popover)
        row.add_suffix(info)

        self.row = row
        self._update_label()
        self.revert_btn.set_opacity(0)
        self.revert_btn.set_sensitive(False)

    # -- value access ------------------------------------------------------
    def get(self):
        if hasattr(self, "combo"):
            return self.values[self.combo.get_selected()]
        return int(round(self.adj.get_value()))

    def set(self, value, silent=True):
        self._silent = silent
        try:
            if hasattr(self, "combo"):
                if value in self.values:
                    self.combo.set_selected(self.values.index(value))
            else:
                self.adj.set_value(value)
        finally:
            self._silent = False
        self._update_label()

    def set_baseline(self, value):
        self.baseline = value
        self._update_label()

    def revert(self):
        if self.baseline is not None and self.baseline != self.get():
            self.set(self.baseline, silent=False)
            self.on_change(self)

    def set_supported(self, supported):
        self.row.set_sensitive(supported)
        self.row.set_subtitle(
            self.setting["id"] if supported else
            _("Not supported by your hardware") + f"  ·  {self.setting['id']}")

    # -- internals ---------------------------------------------------------
    def _changed(self):
        self._update_label()
        if not self._silent:
            self.on_change(self)

    def format(self, v):
        """Human-readable value, e.g. "900°", "70 %", "Peak"."""
        return format_value(self.setting, v)

    def _update_label(self):
        if hasattr(self, "value_label"):
            self.value_label.set_label(self.format(self.get()))
        modified = self.baseline is not None and self.get() != self.baseline
        self.revert_btn.set_opacity(1 if modified else 0)
        self.revert_btn.set_sensitive(modified)
