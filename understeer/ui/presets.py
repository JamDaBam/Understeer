"""Presets: the Presets view, the Presets menu, and saving, applying,
renaming and deleting presets."""
import json
import logging
import pathlib

from gi.repository import Adw, Gio, GLib, Gtk

from .. import config, sharing
from ..i18n import _
from ..settings import SETTINGS, SIM_DEFAULTS, TABS, changes, format_value

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
            info_btn = Gtk.Button(icon_name="help-about-symbolic",
                                  tooltip_text=_("Show Values"))
            info_btn.add_css_class("flat")
            info_btn.connect("clicked", lambda b, p=path: self._show_preset(p))
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
            for w in (save_btn, rename_btn, info_btn, share_btn, del_btn):
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

    def _apply_preset(self, path, game=None, connected=False):
        """Apply a preset; game: the game it is applied for, connected: the
        wheel base just got connected while the game runs."""
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
        if game and connected:
            outcome, failed = self._apply_values(
                values,
                _("Wheel base connected: preset “{name}” for {game} applied")
                .format(game=game, name=name),
                _("Wheel base connected: preset “{name}” for {game} loaded — "
                  "click Apply to write it").format(game=game, name=name))
            self._notify_game_preset(game, name, outcome, failed, connected)
        elif game:
            # Without a wheel base, it is written once one is connected
            # (_apply_running_game_preset).
            outcome, failed = self._apply_values(
                values,
                _("{game} started: preset “{name}” applied").format(
                    game=game, name=name) if self.device_path else
                _("{game} started: preset “{name}” is applied once the wheel "
                  "base is connected").format(game=game, name=name),
                _("{game} started: preset “{name}” loaded — click Apply to "
                  "write it").format(game=game, name=name))
            self._notify_game_preset(game, name, outcome, failed)
        else:
            self._apply_values(
                values, _("Preset “{name}” applied").format(name=name),
                _("Preset “{name}” loaded — click Apply to write it").format(
                    name=name))

    def _show_preset(self, path):
        """The values of a preset, by tab, without applying it; those that
        differ from the current settings are marked."""
        path = pathlib.Path(path)
        try:
            name, values = self._read_preset(path)
        except (OSError, json.JSONDecodeError) as e:
            self._error(_("Could not load preset"), str(e))
            return
        current = self._collect_values()
        differ = {a for a, v in values.items() if a in current and current[a] != v}
        width, height = self._dialog_size(520, 600, 760)
        dialog = Adw.Dialog(title=name, content_width=width, content_height=height)
        apply_btn = Gtk.Button(label=_("Apply"))
        apply_btn.add_css_class("suggested-action")

        def apply(_button):
            dialog.close()
            self._ask_apply_preset(path)

        apply_btn.connect("clicked", apply)
        header = Adw.HeaderBar()
        header.pack_start(apply_btn)
        page = Adw.PreferencesPage()
        if differ:
            note = _("Settings that differ from the current ones: {n}. Their "
                     "current value is shown below them.").format(n=len(differ))
        else:
            note = _("The current settings match this preset.")
        page.set_description(note)
        for tab, title in TABS:
            # Group titles are markup: "Pedals & Paddles"
            group = Adw.PreferencesGroup(title=GLib.markup_escape_text(_(title)))
            for setting in SETTINGS:
                attr_id = setting["id"]
                if setting["page"] != tab or attr_id not in values:
                    continue
                row = Adw.ActionRow(title=_(setting["label"]))
                row.set_use_markup(False)
                value = Gtk.Label(label=format_value(setting, values[attr_id]),
                                  css_classes=["numeric"])
                if attr_id in differ:
                    row.set_subtitle(_("Now: {value}").format(
                        value=format_value(setting, current[attr_id])))
                    value.add_css_class("heading")
                    value.add_css_class("accent")
                row.add_suffix(value)
                group.add(row)
            if any(s["page"] == tab and s["id"] in values for s in SETTINGS):
                page.add(group)
        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(header)
        dialog.set_child(toolbar)
        log.info("Showing the values of preset %s", path.name)
        dialog.present(self)

    def _apply_running_game_preset(self):
        """After a wheel base got connected: apply the preset of a running
        game, chosen before there was a wheel base to write it to. Returns
        whether there was one."""
        for game in self.games_page.games:
            if game["game"] not in self._game_watch.running or not game["auto"]:
                continue
            path = config.game_preset_path(game)
            if path is not None:
                log.info("Wheel base connected while %s runs", game["name"])
                self._apply_preset(path, game=game["name"], connected=True)
                return True
        return False

    def _notify_game_preset(self, game, name, outcome, failed, connected=False):
        """While the window is closed, tell with a notification of the
        desktop what became of the preset of a game that started, or that
        runs while the wheel base got connected."""
        if not self._in_background or not config.get_setting(
                "game_notifications", True):
            return
        bodies = {
            "written": _("Preset “{name}” applied."),
            "pending": _("Preset “{name}” loaded, but not written: open "
                         "Understeer and click Apply."),
            "failed": _("Preset “{name}” could not be written completely. "
                        "Failed: {names}"),
            "no device": _("Preset “{name}” chosen. It is applied as soon as "
                           "the wheel base is connected."),
        }
        title = (_("Wheel base connected, {game} runs") if connected
                 else _("{game} started")).format(game=game)
        log.info("Notification for %s: preset %s %s%s", game, name, outcome,
                 " after connecting" if connected else "")
        self._desktop_notify(
            "game-preset", title,
            bodies[outcome].format(name=name, names=", ".join(failed)))

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
        dialog = Gtk.FileDialog(title=_("Import Presets"), filters=filters,
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
                    raise sharing.Error(_("This is too long to hold presets."))
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
            try:
                result = sharing.find_presets(text, source.stem)
            except sharing.Error as e:
                self._not_a_preset(str(e))
                return
            if len(result.presets) == 1 and not result.skipped:
                self._import_presets(result.presets)
            else:                           # choose which ones
                self._ask_paste_preset(text, source.stem)

        dialog.open(self, None, chosen)

    def _ask_paste_preset(self, text=None, fallback_name=""):
        """A text field to paste shared presets into, e.g. a whole forum
        thread. Below it, every preset found, with its values and a check
        box; only the checked settings of the checked ones are added.

        Not an Adw.AlertDialog: all of that is a window handle, so a double
        click to select a word would maximize the window."""
        # Big: a pasted thread and the presets found in it should be easy
        # to read. Libadwaita makes it smaller in a small window.
        width, height = self._dialog_size(720, 640, 1100)
        dialog = Adw.Dialog(title=_("Paste Presets"), content_width=width,
                            content_height=height)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda b: dialog.close())
        add = Gtk.Button(label=_("Add Preset"))
        add.add_css_class("suggested-action")
        header = Adw.HeaderBar(show_start_title_buttons=False,
                               show_end_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(add)

        intro = Gtk.Label(
            label=_("Paste shared presets here, as text or JSON, also a whole "
                    "forum thread. Understeer finds every preset in it and "
                    "takes only their settings, nothing else."),
            xalign=0, wrap=True)
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True,
                            top_margin=8, bottom_margin=8, left_margin=8,
                            right_margin=8, accepts_tab=False)
        scroll = Gtk.ScrolledWindow(child=view, min_content_height=200,
                                    vexpand=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class("card")
        scroll.set_overflow(Gtk.Overflow.HIDDEN)    # round corners
        status = Gtk.Label(xalign=0, wrap=True)
        found_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                 valign=Gtk.Align.START)
        found_list.add_css_class("boxed-list")
        found_scroll = Gtk.ScrolledWindow(
            child=found_list, min_content_height=200, vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER, visible=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=18, margin_start=18,
                      margin_end=18)
        for w in (intro, scroll, status, found_scroll):
            box.append(w)
        toolbar = Adw.ToolbarView(content=box)
        toolbar.add_top_bar(header)
        dialog.set_child(toolbar)
        buffer = view.get_buffer()
        checks = []                 # [(sharing.Found, Gtk.CheckButton)]
        timer = [0]

        def pasted_text():
            return buffer.get_text(buffer.get_start_iter(),
                                   buffer.get_end_iter(), False)

        def update_add(*_args):
            n = sum(c.get_active() for _f, c in checks)
            add.set_label(_("Add Preset") if len(checks) == 1
                          else _("Add ({n})").format(n=n))
            add.set_sensitive(n > 0)

        def scan():
            timer[0] = 0
            found_list.remove_all()
            checks.clear()
            pasted = pasted_text()
            if not pasted.strip():
                status.set_label("")
                status.set_css_classes([])
            else:
                try:
                    result = sharing.find_presets(pasted, fallback_name)
                except sharing.Error as e:
                    status.set_label(str(e))
                    status.set_css_classes(["error"])
                else:
                    status.set_label(self._found_text(result))
                    status.set_css_classes(["dim-label"])
                    for found in result.presets:
                        row, check = self._found_row(found, result.presets)
                        check.connect("toggled", update_add)
                        checks.append((found, check))
                        found_list.append(row)
            # The text field gets the room the list doesn't need
            found_scroll.set_visible(bool(checks))
            scroll.set_vexpand(not checks)
            update_add()
            return GLib.SOURCE_REMOVE

        def changed(*_args):
            if timer[0]:
                GLib.source_remove(timer[0])
                timer[0] = 0
            if buffer.get_char_count() < 20000:
                scan()                      # small: right away
            else:
                timer[0] = GLib.timeout_add(300, scan)

        def from_clipboard(clipboard, result):
            try:
                pasted = clipboard.read_text_finish(result)
            except GLib.Error:
                return
            # Only presets: other text on the clipboard stays private.
            if pasted and not pasted_text():
                try:
                    sharing.find_presets(pasted)
                except sharing.Error:
                    return
                buffer.set_text(pasted)

        def on_add(_button):
            if timer[0]:
                GLib.source_remove(timer[0])
                scan()
            chosen = [f for f, c in checks if c.get_active()]
            dialog.close()
            self._import_presets(chosen)

        def closed(*_args):
            if timer[0]:
                GLib.source_remove(timer[0])
                timer[0] = 0

        buffer.connect("changed", changed)
        add.connect("clicked", on_add)
        dialog.connect("closed", closed)
        if text:
            buffer.set_text(text)
        else:
            scan()
            self.get_clipboard().read_text_async(None, from_clipboard)
        dialog.present(self)
        view.grab_focus()

    @staticmethod
    def _found_text(result):
        parts = [_("Presets found: {n}").format(n=len(result.presets))]
        if result.skipped:
            parts.append(_("skipped without known settings: {n}").format(
                n=result.skipped))
        if result.cut:
            parts.append(_("only the first {max} are taken").format(
                max=sharing.MAX_PRESETS))
        return " · ".join(parts)

    def _found_row(self, found, all_found):
        """A row for a preset found: check box, name, its values to unfold."""
        subtitle = _("Settings: {n}").format(n=len(found.values))
        if sum(f.name == found.name for f in all_found) > 1:
            subtitle = _("Version {n}").format(n=found.version) + " · " + subtitle
        row = Adw.ExpanderRow(title=found.name, subtitle=subtitle)
        row.set_use_markup(False)           # names come from others
        check = Gtk.CheckButton(active=True, valign=Gtk.Align.CENTER,
                                tooltip_text=_("Add this preset"))
        row.add_prefix(check)
        for setting in SETTINGS:
            if setting["id"] in found.values:
                item = Adw.ActionRow(title=_(setting["label"]))
                item.add_suffix(Gtk.Label(
                    label=format_value(setting, found.values[setting["id"]]),
                    css_classes=["numeric"]))
                row.add_row(item)
        return row, check

    def _not_a_preset(self, reason):
        log.info("Not a preset: %s", reason)
        dialog = Adw.AlertDialog(heading=_("No Preset Found"), body=reason)
        dialog.add_response("ok", _("OK"))
        dialog.present(self)

    def _import_preset(self, text, fallback_name=""):
        """Add the presets in text; asks first for names already taken."""
        try:
            result = sharing.find_presets(text, fallback_name)
        except sharing.Error as e:
            self._not_a_preset(str(e))
            return
        self._import_presets(result.presets)

    def _import_presets(self, found, added=None):
        """Add presets found, one after another; for a name already taken,
        ask whether to replace it or keep both."""
        added = [] if added is None else added
        if not found:
            self._imported(added)
            return
        first, rest = found[0], found[1:]
        name = first.name
        values = {a: v for a, v in first.values.items() if a in self.rows}
        path = config.preset_path(name)

        def add(path, name):
            if self._write_imported(path, name, values):
                added.append((path, name))

        if not path.exists():
            add(path, name)
            self._import_presets(rest, added)
            return
        try:
            _n, stored = self._read_preset(path)
        except (OSError, json.JSONDecodeError):
            stored = {}
        same = stored == values
        diff = changes(stored, values)
        if same:
            body = _("Your preset already has the same settings. Replace it "
                     "anyway, or keep both?")
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
        dialog.add_response("cancel", _("Skip") if rest else _("Cancel"))
        dialog.add_response("keep", _("Keep Both"))
        dialog.add_response("replace", _("Replace"))
        dialog.set_response_appearance("replace",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.set_default_response("keep")

        def on_response(d, response):
            if response == "replace":
                add(path, name)
            elif response == "keep":
                new = sharing.free_name(
                    name, lambda n: config.preset_path(n).exists())
                add(config.preset_path(new), new)
            self._import_presets(rest, added)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _write_imported(self, path, name, values):
        try:
            config.write_preset(path, name, values)
        except OSError as e:
            self._error(_("Could not save preset"), str(e))
            return False
        log.info("Preset %r imported as %s: %s", name, path.name,
                 " ".join(f"{a}={v}" for a, v in values.items()))
        if self.active_preset == path:
            self.active_values = dict(values)
        return True

    def _imported(self, added):
        if not added:
            return
        self._presets_changed()
        if len(added) == 1:
            path, name = added[0]
            self._notify(_("Preset “{name}” imported").format(name=name),
                         _("Apply"), lambda: self._ask_apply_preset(path))
        else:
            self._notify(_("Presets imported: {n}").format(n=len(added)))

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
