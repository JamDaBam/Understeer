"""Presets: the Presets view, the Presets menu, and saving, applying,
renaming and deleting presets."""
import json
import logging
import pathlib

from gi.repository import Adw, Gio, GLib, Gtk

from .. import config, sharing
from ..i18n import _
from ..settings import SIM_DEFAULTS, changes

log = logging.getLogger("understeer.window")


def _menu_text(text):
    """Menus read "_" as a keyboard shortcut; keep it in names."""
    return text.replace("_", "__")


class PresetsMixin:
    """Part of TunerWindow. self.active_preset is the path of the last
    applied or saved preset, self.active_values the values stored in it."""

    def _build_presets_page(self):
        page, column = self._scroll_page()
        bring = Gtk.Box(spacing=6)
        for label, tip, action in (
                (_("Import File…"), _("Add a preset shared as a file"),
                 "win.import-preset"),
                (_("Paste Text…"), _("Add a preset shared as text or JSON"),
                 "win.paste-preset")):
            button = Gtk.Button(label=label, action_name=action,
                                tooltip_text=tip)
            button.add_css_class("flat")
            bring.append(button)
        self._section(column, _("Saved Presets"),
                      _("Saves all current settings under a name in "
                        "~/.config/Understeer/."), bring)
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
            apply_btn.connect("clicked", lambda b, p=path: self._ask_apply_preset(p))
            save_btn = Gtk.Button(label=_("Save Here"))
            save_btn.set_tooltip_text(_("Save current settings to this preset"))
            save_btn.connect("clicked", lambda b, p=path:
                             self._confirm_overwrite(p))
            rename_btn = Gtk.Button(label=_("Rename"))
            rename_btn.set_tooltip_text(_("Change the name of this preset"))
            rename_btn.connect("clicked", lambda b, p=path, n=name:
                               self._ask_rename_preset(p, n))
            share_btn = Gtk.MenuButton(icon_name="understeer-share-symbolic",
                                       menu_model=self._share_menu(path),
                                       tooltip_text=_("Share"))
            share_btn.add_css_class("flat")
            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.set_tooltip_text(_("Delete"))
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", lambda b, p=path, n=name:
                            self._confirm_delete_preset(p, n))
            # Apply on its own row: with the text buttons in one row, the
            # tiles would get too wide for two of them side by side.
            for w in (save_btn, rename_btn, share_btn, del_btn):
                buttons.append(w)
            save_btn.set_hexpand(True)
            rename_btn.set_hexpand(True)
            for w in (title, sub, apply_btn, buttons):
                tile.append(w)
            self.presets_flow.append(tile)
        self.presets_flow.append(self._sim_defaults_tile())

    @staticmethod
    def _share_menu(path):
        menu = Gio.Menu()
        for label, action in ((_("Save as File…"), "win.export-preset"),
                              (_("Copy as Text"), "win.copy-preset-text"),
                              (_("Copy as JSON"), "win.copy-preset-json")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(action,
                                             GLib.Variant.new_string(str(path)))
            menu.append_item(item)
        return menu

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
        """The Presets menu; also remembers the preset names for
        _active_name(), which runs on every change of a setting."""
        presets_found = config.list_presets()
        self._preset_names = {path: name for name, path in presets_found}
        menu = Gio.Menu()
        if self.active_preset is not None:
            update = Gio.Menu()
            update.append(_("Save to “{name}”").format(
                name=_menu_text(self._active_name())), "win.update-preset")
            menu.append_section(None, update)
        presets = Gio.Menu()
        for name, path in presets_found:
            item = Gio.MenuItem.new(_menu_text(name), None)
            item.set_action_and_target_value(
                "win.apply-preset", GLib.Variant.new_string(str(path)))
            presets.append_item(item)
        if presets.get_n_items():
            menu.append_section(None, presets)
        manage = Gio.Menu()
        manage.append(_("Save as Preset…"), "win.save-preset")
        manage.append(_("Import Preset File…"), "win.import-preset")
        manage.append(_("Paste Preset Text…"), "win.paste-preset")
        manage.append(_("Manage Presets"), "win.show-presets")
        menu.append_section(None, manage)
        self.preset_button.set_menu_model(menu)

    def _presets_changed(self):
        self._rebuild_presets_list()
        self.games_page.refresh()
        self._rebuild_preset_menu()
        self._update_preset_indicator()

    def _active_name(self):
        if self.active_preset is None:
            return ""
        return self._preset_names.get(self.active_preset, self.active_preset.stem)

    def _preset_diff(self, stored):
        """[(attr id, stored value or None, current value)] that differ."""
        return changes(stored, self._collect_values())

    def _unsaved_preset_changes(self):
        """The diff of the active preset if it has unsaved changes, else [].

        Only settings stored in the preset count; older presets may hold
        just a few values."""
        if self.active_preset is None:
            return []
        diff = self._preset_diff(self.active_values)
        return diff if any(old is not None for _a, old, _v in diff) else []

    def _update_preset_indicator(self):
        if self.active_preset is None:
            self.preset_button.set_label(_("Presets"))
            self.update_action.set_enabled(False)
            return
        modified = bool(self._unsaved_preset_changes())
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

    def _changes_list(self, diff):
        """Setting, before → after in aligned columns; scrolls when long."""
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        for attr_id, old, new in diff:
            row = self.rows[attr_id]
            item = Adw.ActionRow(title=_(row.setting["label"]))
            values = Gtk.Box(spacing=8)
            # Columns of the same width line up from row to row; long values
            # (paddle mode) wrap within theirs.
            before = row.format(old) if old is not None else "—"
            for text, xalign in ((before, 1), (row.format(new), 0)):
                values.append(Gtk.Label(label=text, xalign=xalign, wrap=True,
                                        width_chars=17, max_width_chars=17,
                                        css_classes=["numeric"]))
            values.insert_child_after(Gtk.Label(label="→", css_classes=["dim-label"]),
                                      values.get_first_child())
            values.get_last_child().add_css_class("heading")
            item.add_suffix(values)
            listbox.append(item)
        scroll = Gtk.ScrolledWindow(child=listbox, propagate_natural_height=True,
                                    max_content_height=420,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_size_request(560, -1)
        return scroll

    def _ask_preset_name(self):
        entry = Gtk.Entry(placeholder_text=_("e.g. BeamNG Drift"),
                          activates_default=True)
        dialog = Adw.AlertDialog(
            heading=_("Save as Preset"),
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
        dialog.present(self)

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

    def _confirm_overwrite(self, path, new_name=None):
        """Ask before replacing a preset file with the current settings,
        showing what changes in it."""
        path = pathlib.Path(path)
        try:
            name, stored = self._read_preset(path)
        except (OSError, json.JSONDecodeError):
            name, stored = path.stem, {}
        name = new_name or name
        diff = self._preset_diff(stored)
        body = (_("Settings that change:") if diff else
                _("The preset already matches the current settings."))
        dialog = Adw.AlertDialog(
            heading=_("Overwrite “{name}”?").format(name=name), body=body)
        if diff:
            dialog.set_extra_child(self._changes_list(diff))
            if hasattr(dialog, "set_prefer_wide_layout"):     # Libadwaita 1.6
                dialog.set_prefer_wide_layout(True)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("overwrite", _("Overwrite"))
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("overwrite",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda d, r: self._overwrite_preset(path, name)
                       if r == "overwrite" else None)
        dialog.present(self)

    def confirm_quit(self, quit_now):
        """Call quit_now(), but first offer to save unsaved changes of the
        active preset. The wheel base keeps its settings either way."""
        if self._shut_down or not self._unsaved_preset_changes():
            if self._save_dialog is None:
                quit_now()
            return
        if self._in_background:
            self.show_again()               # also reads the wheel base again
        self._ask_save_changes(
            quit_now, "quit",
            _("These settings differ from the preset. The wheel base "
              "keeps them either way."))

    def _ask_apply_preset(self, path):
        """Apply a preset chosen by the user; first offer to save unsaved
        changes of the active preset, applying would lose them."""
        path = pathlib.Path(path)
        try:
            name, _values = self._read_preset(path)
        except (OSError, json.JSONDecodeError):
            name = path.stem
        self._ask_save_changes(
            lambda: self._apply_preset(path), f"apply {path.name}",
            _("These settings differ from the preset. Then “{name}” is "
              "applied.").format(name=name))

    def _ask_save_changes(self, then, what, body):
        """Offer to save unsaved changes of the active preset into it,
        then call then(). Calls it right away without such changes."""
        if self._save_dialog is not None:
            return                          # already asking
        diff = self._unsaved_preset_changes()
        if not diff:
            then()
            return
        path, name = self.active_preset, self._active_name()
        dialog = Adw.AlertDialog(
            heading=_("Save Changes to “{name}”?").format(name=name), body=body)
        dialog.set_extra_child(self._changes_list(diff))
        if hasattr(dialog, "set_prefer_wide_layout"):         # Libadwaita 1.6
            dialog.set_prefer_wide_layout(True)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("discard", _("Don't Save"))
        dialog.add_response("save", _("Save"))
        dialog.set_close_response("cancel")
        dialog.set_default_response("save")
        dialog.set_response_appearance("discard",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            self._save_dialog = None
            log.info("Unsaved changes to preset %s before %s: %s",
                     path.name, what, response)
            if response == "save" and not self._write_preset(path, name):
                return                      # the error is shown
            if response in ("save", "discard"):
                then()

        dialog.connect("response", on_response)
        self._save_dialog = dialog
        dialog.present(self)

    def _update_active_preset(self):
        """Save to the active preset, after showing what changes in it."""
        if self.active_preset is not None:
            self._confirm_overwrite(self.active_preset)

    def _overwrite_preset(self, path, name):
        """Replace a preset with the current settings; offer undo."""
        try:
            old_text = path.read_text()
            _n, old_values = self._read_preset(path)
        except (OSError, json.JSONDecodeError):
            old_text, old_values = None, {}
        if not self._write_preset(path, name):
            return
        def undo():
            try:
                config.write_file(path, old_text)
            except OSError as e:
                self._error(_("Could not save preset"), str(e))
                return
            self._set_active_preset(path, old_values)

        self._notify(_("Preset “{name}” saved").format(name=name),
                     _("Undo") if old_text is not None else None, undo)

    # -- sharing ---------------------------------------------------------------
    def _shared(self, path):
        """(name, values) of a preset to share, or None after an error."""
        path = pathlib.Path(path)
        try:
            return self._read_preset(path)
        except (OSError, json.JSONDecodeError) as e:
            self._error(_("Could not load preset"), str(e))
            return None

    def _copy_preset(self, path, as_json):
        shared = self._shared(path)
        if shared is None:
            return
        name, values = shared
        if as_json:
            text = sharing.preset_json(name, values)
            message = _("Preset “{name}” copied as JSON")
        else:
            text = sharing.preset_text(name, values)
            message = _("Preset “{name}” copied as text")
        self.get_clipboard().set(text)
        log.info("Preset %s copied as %s", pathlib.Path(path).name,
                 "JSON" if as_json else "text")
        self._notify(message.format(name=name))

    @staticmethod
    def _preset_filters():
        presets = Gtk.FileFilter(name=_("Presets (JSON or text)"))
        for pattern in ("*.json", "*.txt"):
            presets.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(presets)
        return filters, presets

    def _export_preset(self, path):
        shared = self._shared(path)
        if shared is None:
            return
        name, values = shared
        filters, presets = self._preset_filters()
        dialog = Gtk.FileDialog(title=_("Save Preset as File"), filters=filters,
                                default_filter=presets,
                                initial_name=pathlib.Path(path).name)

        def chosen(d, result):
            try:
                file = d.save_finish(result)
            except GLib.Error:
                return                          # cancelled
            dest = file.get_path() if file else None
            if dest is None:
                return
            try:
                config.write_file(pathlib.Path(dest),
                                  sharing.preset_json(name, values))
            except OSError as e:
                self._error(_("Could not save preset"), str(e))
                return
            log.info("Preset %s saved as file", pathlib.Path(path).name)
            self._notify(_("Preset “{name}” saved as file").format(name=name),
                         _("Show Folder"),
                         lambda: Gtk.FileLauncher.new(Gio.File.new_for_path(dest))
                         .open_containing_folder(self, None, None))

        dialog.save(self, None, chosen)

    def _ask_import_preset(self):
        filters, presets = self._preset_filters()
        dialog = Gtk.FileDialog(title=_("Import Preset"), filters=filters,
                                default_filter=presets)

        def chosen(d, result):
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return                          # cancelled
            source = file.get_path() if file else None
            if source is None:
                return
            source = pathlib.Path(source)
            try:
                if source.stat().st_size > sharing.MAX_SIZE:
                    raise sharing.Error(_("This is too long to be a preset."))
                text = source.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                self._not_a_preset(_("The file holds no text."))
                return
            except sharing.Error as e:
                self._not_a_preset(str(e))
                return
            except OSError as e:
                self._error(_("Could not load preset"), str(e))
                return
            self._import_preset(text, source.stem)

        dialog.open(self, None, chosen)

    def _ask_paste_preset(self):
        """A text field to paste a shared preset into. It shows what was
        found; only the checked settings of it are added."""
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True,
                            top_margin=8, bottom_margin=8, left_margin=8,
                            right_margin=8, accepts_tab=False)
        view.add_css_class("card")
        scroll = Gtk.ScrolledWindow(child=view, min_content_height=180,
                                    max_content_height=300,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_size_request(520, -1)
        found = Gtk.Label(xalign=0, wrap=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(scroll)
        box.append(found)
        dialog = Adw.AlertDialog(
            heading=_("Paste Preset"),
            body=_("Paste the text or JSON of a shared preset here. Understeer "
                   "takes only the settings from it, nothing else."))
        dialog.set_extra_child(box)
        if hasattr(dialog, "set_prefer_wide_layout"):         # Libadwaita 1.6
            dialog.set_prefer_wide_layout(True)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add Preset"))
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        buffer = view.get_buffer()

        def text():
            return buffer.get_text(buffer.get_start_iter(),
                                   buffer.get_end_iter(), False)

        def check(*_args):
            pasted = text()
            ok = False
            if not pasted.strip():
                found.set_label("")
            else:
                try:
                    name, values = sharing.parse(pasted)
                    found.set_label(_("Found: “{name}”, settings: {n}").format(
                        name=name, n=len(values)))
                    ok = True
                except sharing.Error as e:
                    found.set_label(str(e))
            found.set_css_classes(["dim-label"] if ok or not pasted.strip()
                                  else ["error"])
            dialog.set_response_enabled("add", ok)

        buffer.connect("changed", check)
        check()

        def from_clipboard(clipboard, result):
            try:
                pasted = clipboard.read_text_finish(result)
            except GLib.Error:
                return
            # Only a preset: other text on the clipboard stays private.
            if pasted and not text():
                try:
                    sharing.parse(pasted)
                except sharing.Error:
                    return
                buffer.set_text(pasted)

        self.get_clipboard().read_text_async(None, from_clipboard)
        dialog.connect("response", lambda d, r: r == "add" and
                       self._import_preset(text()))
        dialog.present(self)
        view.grab_focus()

    def _not_a_preset(self, reason):
        log.info("Not a preset: %s", reason)
        dialog = Adw.AlertDialog(heading=_("No Preset Found"), body=reason)
        dialog.add_response("ok", _("OK"))
        dialog.present(self)

    def _import_preset(self, text, fallback_name=""):
        """Add a shared preset; asks first if one has the same name."""
        try:
            name, values = sharing.parse(text, fallback_name)
        except sharing.Error as e:
            self._not_a_preset(str(e))
            return
        values = {a: v for a, v in values.items() if a in self.rows}
        path = config.preset_path(name)
        if not path.exists():
            self._add_imported(path, name, values)
            return
        try:
            _n, stored = self._read_preset(path)
        except (OSError, json.JSONDecodeError):
            stored = {}
        same = stored == values
        diff = changes(stored, values)
        if same:
            body = _("Your preset already has the same settings. Add the "
                     "shared one anyway, under a new name?")
        elif diff:
            body = _("Replace your preset, or keep both? The shared preset "
                     "has these settings:")
        else:
            body = _("Replace your preset, or keep both?")
        dialog = Adw.AlertDialog(
            heading=_("“{name}” Already Exists").format(name=name), body=body)
        if diff:
            dialog.set_extra_child(self._changes_list(diff))
            if hasattr(dialog, "set_prefer_wide_layout"):     # Libadwaita 1.6
                dialog.set_prefer_wide_layout(True)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("keep", _("Keep Both"))
        if not same:
            dialog.add_response("replace", _("Replace"))
            dialog.set_response_appearance("replace",
                                           Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.set_default_response("keep")

        def on_response(d, response):
            if response == "replace":
                self._add_imported(path, name, values)
            elif response == "keep":
                new = sharing.free_name(
                    name, lambda n: config.preset_path(n).exists())
                self._add_imported(config.preset_path(new), new, values)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _add_imported(self, path, name, values):
        try:
            config.write_preset(path, name, values)
        except OSError as e:
            self._error(_("Could not save preset"), str(e))
            return
        log.info("Preset %r imported as %s: %s", name, path.name,
                 " ".join(f"{a}={v}" for a, v in values.items()))
        if self.active_preset == path:
            self.active_values = dict(values)
        self._presets_changed()
        self._notify(_("Preset “{name}” imported").format(name=name),
                     _("Apply"), lambda: self._ask_apply_preset(path))

    def _ask_rename_preset(self, path, name):
        entry = Gtk.Entry(text=name, activates_default=True)
        dialog = Adw.AlertDialog(
            heading=_("Rename Preset"),
            body=_("Games that use this preset keep it."))
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
        dialog.set_default_response("rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        entry.connect("changed", lambda e: dialog.set_response_enabled(
            "rename", bool(e.get_text().strip())))

        def on_response(d, response):
            if response == "rename":
                self._rename_preset(path, name, entry.get_text().strip())

        dialog.connect("response", on_response)
        dialog.present(self)
        entry.grab_focus()
        entry.select_region(0, -1)

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
        dialog = Adw.AlertDialog(
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
        dialog.present(self)
