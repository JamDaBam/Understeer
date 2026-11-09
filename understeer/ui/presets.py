"""Presets: the Presets view, the Presets menu, and saving, applying,
renaming and deleting presets."""
import json
import logging
import pathlib

from gi.repository import Adw, Gio, GLib, Gtk

from .. import config
from ..i18n import _
from ..settings import SIM_DEFAULTS

log = logging.getLogger("understeer.window")


class PresetsMixin:
    """Part of TunerWindow. self.active_preset is the path of the last
    applied or saved preset, self.active_values the values stored in it."""

    def _build_presets_page(self):
        page, column = self._scroll_page()
        self._section(column, _("Saved Presets"),
                      _("Saves all current settings under a name in "
                        "~/.config/Understeer/."))
        self.presets_flow = self._tile_grid()
        column.append(self.presets_flow)
        self._rebuild_presets_list()
        return page

    def _rebuild_presets_list(self):
        self.presets_flow.remove_all()
        presets = config.list_presets()

        new = Gtk.Button(action_name="win.save-preset")
        new.add_css_class("card")
        new.add_css_class("tile")
        new.add_css_class("new-preset")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                        valign=Gtk.Align.CENTER)
        plus = Gtk.Image.new_from_icon_name("list-add-symbolic")
        plus.set_pixel_size(32)
        plus.add_css_class("tile-icon")
        plus.set_halign(Gtk.Align.CENTER)
        inner.append(plus)
        inner.append(Gtk.Label(label=_("Save as Preset…"), css_classes=["heading"]))
        if not presets:
            hint = Gtk.Label(label=_("Saved presets appear here and in the "
                                     "Presets menu at the top."),
                             wrap=True, justify=Gtk.Justification.CENTER)
            hint.add_css_class("dim-label")
            inner.append(hint)
        new.set_child(inner)
        self.presets_flow.append(new)

        for name, path in presets:
            active = self.active_preset is not None and path == self.active_preset
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            tile.add_css_class("card")
            tile.add_css_class("tile")
            tile.add_css_class("preset-tile")
            if active:
                tile.add_css_class("active")
            title = Gtk.Label(label=name, xalign=0, wrap=True)
            title.add_css_class("preset-name")
            sub = Gtk.Label(label=_("Active preset") if active else path.name,
                            xalign=0, vexpand=True, valign=Gtk.Align.START)
            sub.set_ellipsize(3)                # Pango.EllipsizeMode.END
            sub.add_css_class("dim-label")
            buttons = Gtk.Box(spacing=6)
            apply_btn = Gtk.Button(label=_("Apply"))
            apply_btn.add_css_class("suggested-action")
            apply_btn.connect("clicked", lambda b, p=path: self._apply_preset(p))
            save_btn = Gtk.Button(label=_("Save Here"))
            save_btn.set_tooltip_text(_("Save current settings to this preset"))
            save_btn.connect("clicked", lambda b, p=path:
                             self._confirm_overwrite(p))
            rename_btn = Gtk.Button(label=_("Rename"))
            rename_btn.set_tooltip_text(_("Change the name of this preset"))
            rename_btn.connect("clicked", lambda b, p=path, n=name:
                               self._ask_rename_preset(p, n))
            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.set_tooltip_text(_("Delete"))
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", lambda b, p=path, n=name:
                            self._confirm_delete_preset(p, n))
            # Apply on its own row: with the text buttons in one row, the
            # tiles would get too wide for two of them side by side.
            for w in (save_btn, rename_btn, del_btn):
                buttons.append(w)
            save_btn.set_hexpand(True)
            rename_btn.set_hexpand(True)
            for w in (title, sub, apply_btn, buttons):
                tile.append(w)
            self.presets_flow.append(tile)
        self.presets_flow.append(self._sim_defaults_tile())

    def _sim_defaults_tile(self):
        """A built-in preset that can't be changed or deleted."""
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tile.add_css_class("card")
        tile.add_css_class("tile")
        tile.add_css_class("preset-tile")
        title = Gtk.Label(label=_("Sim Racing Defaults"), xalign=0, wrap=True)
        title.add_css_class("preset-name")
        sub = Gtk.Label(label=_("Built in · 900°, 70 % force feedback, no "
                                "spring or damper"),
                        xalign=0, wrap=True, vexpand=True, valign=Gtk.Align.START)
        sub.add_css_class("dim-label")
        apply_btn = Gtk.Button(label=_("Apply"))
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", lambda b: self._apply_values(
            SIM_DEFAULTS, _("Sim racing defaults applied")))
        for w in (title, sub, apply_btn):
            tile.append(w)
        return tile

    def _rebuild_preset_menu(self):
        menu = Gio.Menu()
        if self.active_preset is not None:
            update = Gio.Menu()
            update.append(_("Save to “{name}”").format(
                name=self._active_name()), "win.update-preset")
            menu.append_section(None, update)
        presets = Gio.Menu()
        for name, path in config.list_presets():
            item = Gio.MenuItem.new(name, None)
            item.set_action_and_target_value(
                "win.apply-preset", GLib.Variant.new_string(str(path)))
            presets.append_item(item)
        if presets.get_n_items():
            menu.append_section(None, presets)
        manage = Gio.Menu()
        manage.append(_("Save as Preset…"), "win.save-preset")
        manage.append(_("Manage Presets"), "win.show-presets")
        menu.append_section(None, manage)
        self.preset_button.set_menu_model(menu)

    def _presets_changed(self):
        self._rebuild_presets_list()
        self.games_page.refresh()
        self._rebuild_preset_menu()
        self._update_preset_indicator()

    def _active_name(self):
        for name, path in config.list_presets():
            if path == self.active_preset:
                return name
        return self.active_preset.stem if self.active_preset else ""

    def _preset_diff(self, stored):
        """[(attr id, stored value or None, current value)] that differ."""
        return [(a, stored.get(a), v) for a, v in self._collect_values().items()
                if stored.get(a) != v]

    def _update_preset_indicator(self):
        if self.active_preset is None:
            self.preset_button.set_label(_("Presets"))
            self.update_action.set_enabled(False)
            return
        # Only settings stored in the preset count; older presets may hold
        # just a few values.
        modified = any(old is not None
                       for _a, old, _v in self._preset_diff(self.active_values))
        name = self._active_name()
        self.preset_button.set_label(f"{name} •" if modified else name)
        self.preset_button.set_tooltip_text(
            _("Preset “{name}” has unsaved changes").format(name=name)
            if modified else _("Apply or save a preset"))
        self.update_action.set_enabled(modified)

    def _collect_values(self):
        return {a: r.get() for a, r in self.rows.items()
                if r.row.get_sensitive()}

    def _read_preset(self, path):
        """Return (name, values) of a preset file; raises on bad files."""
        name, values = config.read_preset(path)
        return name, {k: v for k, v in values.items() if k in self.rows}

    def _set_active_preset(self, path, values):
        self.active_preset = pathlib.Path(path) if path else None
        self.active_values = dict(values)
        self._presets_changed()

    def _apply_preset(self, path, game=None):
        path = pathlib.Path(path)
        try:
            name, values = self._read_preset(path)
        except (OSError, json.JSONDecodeError) as e:
            self._error(_("Could not load preset"), str(e))
            return
        log.info("Applying preset %s (%s)%s: %s", name, path.name,
                 f" for {game}" if game else "",
                 " ".join(f"{a}={v}" for a, v in values.items()))
        self.active_preset, self.active_values = path, dict(values)
        self._rebuild_preset_menu()
        self._rebuild_presets_list()
        if game:
            self._apply_values(
                values,
                _("{game} started: preset “{name}” applied").format(
                    game=game, name=name),
                _("{game} started: preset “{name}” loaded — click Apply to "
                  "write it").format(game=game, name=name))
        else:
            self._apply_values(
                values, _("Preset “{name}” applied").format(name=name),
                _("Preset “{name}” loaded — click Apply to write it").format(
                    name=name))

    def _ask_preset_name(self):
        entry = Gtk.Entry(placeholder_text=_("e.g. BeamNG Drift"),
                          activates_default=True)
        dialog = Adw.MessageDialog(
            transient_for=self, heading=_("Save as Preset"),
            body=_("Saves all current settings under this name."))
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_default_response("save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "save":
                self._save_preset(entry.get_text().strip())

        dialog.connect("response", on_response)
        dialog.present()

    def _save_preset(self, name):
        if not name:
            self._error(_("No Name"), _("Enter a name for the preset."))
            return
        path = config.preset_path(name)
        if path.exists():
            self._confirm_overwrite(path, new_name=name)
            return
        if self._write_preset(path, name):
            self._notify(_("Preset “{name}” saved").format(name=name))

    def _write_preset(self, path, name):
        values = self._collect_values()
        try:
            config.write_preset(path, name, values)
        except OSError as e:
            self._error(_("Could not save preset"), str(e))
            return False
        self._set_active_preset(path, values)
        return True

    def _describe_diff(self, diff, limit=6):
        lines = []
        for attr_id, old, new in diff[:limit]:
            row = self.rows[attr_id]
            before = row.format(old) if old is not None else "—"
            lines.append(f"{_(row.setting['label'])}: {before} → {row.format(new)}")
        if len(diff) > limit:
            lines.append(_("and {n} more").format(n=len(diff) - limit))
        return "\n".join(lines)

    def _confirm_overwrite(self, path, new_name=None):
        """Ask before replacing a preset file with the current settings."""
        try:
            name, stored = self._read_preset(path)
        except (OSError, json.JSONDecodeError):
            name, stored = path.stem, {}
        name = new_name or name
        diff = self._preset_diff(stored)
        body = (_("Settings that change:") + "\n" + self._describe_diff(diff)
                if diff else _("The preset already matches the current "
                               "settings."))
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Overwrite “{name}”?").format(name=name), body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("overwrite", _("Overwrite"))
        dialog.set_response_appearance("overwrite",
                                       Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, response):
            if response == "overwrite" and self._write_preset(path, name):
                self._notify(_("Preset “{name}” saved").format(name=name))

        dialog.connect("response", on_response)
        dialog.present()

    def _update_active_preset(self):
        """Save to the active preset without asking; offer undo instead."""
        path = self.active_preset
        if path is None:
            return
        try:
            old_text = path.read_text()
            name, old_values = self._read_preset(path)
        except (OSError, json.JSONDecodeError):
            old_text, name, old_values = None, self._active_name(), {}
        if not self._write_preset(path, name):
            return
        def undo():
            try:
                config.write_file(path, old_text)
            except OSError as e:
                self._error(_("Could not save preset"), str(e))
                return
            self._set_active_preset(path, old_values)

        self._notify(_("Saved to “{name}”").format(name=name),
                     _("Undo") if old_text is not None else None, undo)

    def _ask_rename_preset(self, path, name):
        entry = Gtk.Entry(text=name, activates_default=True)
        dialog = Adw.MessageDialog(
            transient_for=self, heading=_("Rename Preset"),
            body=_("Games that use this preset keep it."))
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
        dialog.set_default_response("rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        entry.connect("changed", lambda e: dialog.set_response_enabled(
            "rename", bool(e.get_text().strip())))
        entry.grab_focus()
        entry.select_region(0, -1)

        def on_response(d, response):
            if response == "rename":
                self._rename_preset(path, name, entry.get_text().strip())

        dialog.connect("response", on_response)
        dialog.present()

    def _rename_preset(self, path, old_name, new_name):
        path = pathlib.Path(path)
        if not new_name or new_name == old_name:
            return
        try:
            new_path = config.rename_preset(path, new_name)
        except FileExistsError:
            self._error(_("Name Already Used"),
                        _("Another preset is already called “{name}”.").format(
                            name=new_name))
            return
        except (OSError, ValueError) as e:
            self._error(_("Could not rename preset"), str(e))
            return
        log.info("Preset %s renamed to %r (%s)", path.name, new_name, new_path.name)
        if self.active_preset == path:
            self.active_preset = new_path
        self.games_page.games = config.load_games()
        self._presets_changed()
        self._notify(_("Preset renamed to “{name}”").format(name=new_name))

    def _confirm_delete_preset(self, path, name):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete “{name}”?").format(name=name),
            body=_("The preset file will be removed. This cannot be undone."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, response):
            if response == "delete":
                try:
                    pathlib.Path(path).unlink(missing_ok=True)
                except OSError as e:
                    self._error(_("Could not delete preset"), str(e))
                if self.active_preset == pathlib.Path(path):
                    self.active_preset, self.active_values = None, {}
                self._presets_changed()

        dialog.connect("response", on_response)
        dialog.present()
