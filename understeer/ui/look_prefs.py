"""Preferences for the look: colour scheme, theme, accent colour, corners."""
from gi.repository import Adw, GLib, Gtk

from .. import config, themes
from ..i18n import _
from . import theme

RADIUS_DELAY_MS = 60    # restyle at most this often while dragging


def _toggle_group(keys_labels, active, changed):
    """Linked toggle buttons, one active; changed(key) when another is chosen."""
    box = Gtk.Box(valign=Gtk.Align.CENTER)
    box.add_css_class("linked")
    first = None
    for key, label in keys_labels:
        button = Gtk.ToggleButton(label=label)
        if first is None:
            first = button
        else:
            button.set_group(first)
        button.set_active(key == active)
        button.connect("toggled", lambda b, k=key: b.get_active() and changed(k))
        box.append(button)
    return box


class LookGroups:
    """Builds the groups; `groups` go on the General preferences page."""

    def __init__(self):
        chosen_theme, scheme, accent, radius = theme.current()
        self._radius_timer = 0

        # -- colour scheme ---------------------------------------------------
        self.scheme_row = Adw.ActionRow(title=_("Colour Scheme"))
        self.scheme_row.add_suffix(_toggle_group(
            [("system", _("System")), ("light", _("Light")), ("dark", _("Dark"))],
            scheme if scheme in themes.SCHEMES else "system", self._set_scheme))
        self._update_scheme_row(chosen_theme)

        # -- themes ----------------------------------------------------------
        theme_group = Adw.PreferencesGroup(
            title=_("Theme"),
            description=_("A theme brings its own background and accent "
                          "colour."))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           min_children_per_line=2, max_children_per_line=4,
                           column_spacing=10, row_spacing=10)
        first = None
        for key, (name, desc, _forced, _colors) in themes.THEMES.items():
            button = Gtk.ToggleButton()
            button.add_css_class("theme-choice")
            if first is None:
                first = button
            else:
                button.set_group(first)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            # Not vexpand: the accent bar inside would hand it on, and in a
            # tall dialog the free space went to the previews
            preview = Gtk.Box(spacing=4, vexpand=False)
            preview.add_css_class("theme-preview")
            preview.add_css_class(f"theme-{key}")
            for accent_bar in (False, True):
                mini = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
                mini.add_css_class("theme-mini")
                if accent_bar:
                    bar = Gtk.Box(vexpand=True, valign=Gtk.Align.END)
                    bar.add_css_class("theme-mini-accent")
                    mini.append(bar)
                preview.append(mini)
            title = Gtk.Label(label=_(name), xalign=0)
            title.add_css_class("heading")
            sub = Gtk.Label(label=_(desc), xalign=0)
            sub.add_css_class("dim-label")
            sub.add_css_class("caption")
            for w in (preview, title, sub):
                box.append(w)
            button.set_child(box)
            button.set_active(key == chosen_theme)
            button.connect("toggled", lambda b, k=key: b.get_active() and
                           self._set_theme(k))
            flow.append(button)
        theme_group.add(flow)

        # -- accent colour and corners --------------------------------------
        shape = Adw.PreferencesGroup()
        accent_row = Adw.ActionRow(title=_("Accent Colour"),
                                   subtitle=_("For sliders, bars and selections"))
        swatches = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        first = None
        options = [("auto", _("Like the theme or the system"))] + [
            (key, _(name)) for key, (name, _c) in themes.ACCENTS.items()]
        for key, name in options:
            swatch = Gtk.ToggleButton()
            swatch.add_css_class("accent-swatch")
            swatch.add_css_class(f"accent-{key}")
            swatch.set_tooltip_text(name)
            swatch.update_property([Gtk.AccessibleProperty.LABEL], [name])
            if first is None:
                first = swatch
            else:
                swatch.set_group(first)
            swatch.set_active(key == (accent if accent in themes.ACCENTS else "auto"))
            swatch.connect("toggled", lambda b, k=key: b.get_active() and
                           self._set_accent(k))
            swatches.append(swatch)
        accent_row.add_suffix(swatches)
        shape.add(accent_row)

        corner_row = Adw.ActionRow(title=_("Corner Radius"),
                                   subtitle=_("Tiles, cards and buttons"))
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                         themes.RADIUS_MIN, themes.RADIUS_MAX, 1)
        scale.set_size_request(200, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.set_draw_value(False)
        scale.add_mark(themes.DEFAULT_RADIUS, Gtk.PositionType.BOTTOM, None)
        scale.set_value(radius)
        value = Gtk.Label(label=f"{radius} px", width_chars=5, xalign=1)
        value.add_css_class("numeric")
        scale.connect("value-changed", lambda s: self._set_radius(
            int(round(s.get_value())), value))
        corner_row.add_suffix(scale)
        corner_row.add_suffix(value)
        shape.add(corner_row)

        self.theme_group, self.shape_group = theme_group, shape

    # -- changes ---------------------------------------------------------------
    def _update_scheme_row(self, chosen_theme):
        forced = themes.THEMES[chosen_theme][2]
        self.scheme_row.set_sensitive(forced is None)
        self.scheme_row.set_subtitle(
            _("Set by the theme") if forced else
            _("System follows the setting of your desktop"))

    def _set_scheme(self, key):
        config.set_setting("color_scheme", None if key == "system" else key)
        theme.apply()

    def _set_theme(self, key):
        config.set_setting("theme", None if key == themes.DEFAULT_THEME else key)
        self._update_scheme_row(key)
        theme.apply()

    def _set_accent(self, key):
        config.set_setting("accent", None if key == "auto" else key)
        theme.apply()

    def _set_radius(self, radius, label):
        label.set_label(f"{radius} px")
        config.set_setting("corner_radius",
                           None if radius == themes.DEFAULT_RADIUS else radius)
        if not self._radius_timer:
            self._radius_timer = GLib.timeout_add(RADIUS_DELAY_MS, self._restyle)

    def _restyle(self):
        self._radius_timer = 0
        theme.apply()
        return GLib.SOURCE_REMOVE
