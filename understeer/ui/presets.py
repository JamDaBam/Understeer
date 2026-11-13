"""Presets: the Presets view, the Presets menu, and saving, applying,
renaming and deleting presets."""
import logging
import pathlib

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from .. import config, sharing, trash
from ..i18n import _
from ..names import free_name, menu_label
from ..settings import (SETTINGS, SETTINGS_BY_ID, SIM_DEFAULTS, TABS, changes,
                        format_value)
from . import files, layout
from .names import cap_row, full_name_tip, join_names, name_label, short
from .selection import Selection

log = logging.getLogger("understeer.window")


def _outcome_text(name, outcome, failed, no_device):
    """What became of an applied preset, for a notification of the
    desktop; no_device: the text for "no device", with {name}."""
    if outcome == "written":
        text = _("Preset “{name}” applied.")
    elif outcome == "pending":
        text = _("Preset “{name}” loaded, but not written: open Understeer "
                 "and click Apply.")
    elif outcome == "failed":
        text = _("Preset “{name}” could not be written completely. "
                 "Failed: {names}")
    else:
        text = no_device
    return text.format(name=short(name), names=", ".join(failed))


class PresetsMixin:
    """Part of TunerWindow. self.active_preset is the path of the last
    applied or saved preset, self.active_values the values stored in it."""

    def _build_presets_page(self):
        page, column = layout.scroll_page()
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
        self.preset_selection = Selection(
            _("Delete"), lambda: self._rebuild_presets_list(),
            lambda keys: self._delete_presets([pathlib.Path(k) for k in keys]))
        bring.append(self._trash_button("preset"))
        bring.append(self.preset_selection.button)
        layout.section(column, _("Saved Presets"),
                      _("Saves all current settings under a name in "
                        "~/.config/Understeer/."), bring)
        # The presets without a folder, then a heading and tiles per folder;
        # all of them and the tiles above in the same columns
        self.preset_columns = layout.columns()
        self.presets_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=12)
        column.append(self.presets_box)
        self._rebuild_presets_list()
        return self.preset_selection.page(page)

    def _rebuild_presets_list(self):
        while (child := self.presets_box.get_first_child()) is not None:
            self.presets_box.remove(child)
        groups = config.list_presets_by_folder(empty=True)
        presets = [p for _f, group in groups for p in group]
        loose = groups[0][1] if groups and groups[0][0] is None else []
        choosing = self.preset_selection.active
        self.preset_selection.available([str(p) for _n, p in presets])
        # Making new things and the built-in preset, no drop target as a whole
        actions = layout.tile_grid(max_per_line=7, columns=self.preset_columns)
        self.presets_box.append(actions)
        self.folder_flows = {}
        actions.append(layout.dashed_tile(
            "list-add-symbolic", _("Save as Preset…"),
            None if presets else _("Saved presets appear here and in the "
                                   "Presets menu at the top."),
            action="win.save-preset"))
        folder_tile = layout.dashed_tile(
            "folder-new-symbolic", _("New Folder…"),
            _("Or drag a preset here"), clicked=lambda: self._ask_new_folder())
        self._drop_target(folder_tile, self._ask_new_folder)
        actions.append(folder_tile)
        actions.append(self._sim_defaults_tile())
        actions.set_sensitive(not choosing)     # nothing to choose there
        # The presets without a folder; dropping one here takes it out
        self.presets_flow = layout.tile_grid(max_per_line=7, columns=self.preset_columns)
        if loose or len(groups) > (1 if loose else 0):
            self.presets_box.append(self._preset_section(None, loose))
        for folder, group in groups:
            if folder is not None:
                self.presets_box.append(self._preset_section(folder, group))

    def _preset_section(self, folder, group):
        """A folder (None: the presets without one): its heading and its
        tiles; presets dropped on it go in."""
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        section.add_css_class("preset-folder")
        section.append(self._folder_head(folder, group))
        flow = self.presets_flow if folder is None else \
            layout.tile_grid(max_per_line=7, columns=self.preset_columns)
        if group:
            for name, path in group:
                flow.append(self.preset_selection.wrap(
                    self._preset_tile(name, path), str(path)))
            section.append(flow)
        else:
            empty = Gtk.Label(label=_("Drag presets here"), wrap=True)
            empty.add_css_class("dim-label")
            empty.add_css_class("folder-empty")
            section.append(empty)
        if folder is not None:
            self.folder_flows[folder] = flow if group else None
        self._drop_target(section, lambda path: self._drop_preset(path, folder))
        return section

    def _folder_head(self, folder, group):
        """Heading of a folder: its name, how many presets, rename, delete;
        for folder None (no folder) just the name and count."""
        head = Gtk.Box(spacing=8, margin_start=6)
        icon = Gtk.Image.new_from_icon_name(
            "folder-symbolic" if folder is not None else "view-grid-symbolic")
        icon.add_css_class("dim-label")
        head.append(icon)
        label = name_label(Gtk.Label(label=folder if folder is not None else
                                     _("Without Folder"), xalign=0), chars=40)
        if folder is not None:
            full_name_tip(label, folder, 30)
        label.add_css_class("heading")
        head.append(label)
        count = Gtk.Label(label=_("Presets: {n}").format(n=len(group)), xalign=0)
        count.add_css_class("dim-label")
        count.add_css_class("caption")
        head.append(count)
        if folder is None:
            return head
        rename = Gtk.Button(icon_name="document-edit-symbolic",
                            tooltip_text=_("Rename Folder"))
        rename.add_css_class("flat")
        rename.connect("clicked", lambda b: self._ask_rename_folder(folder))
        head.append(rename)
        delete = Gtk.Button(icon_name="user-trash-symbolic",
                            tooltip_text=_("Delete Folder"))
        delete.add_css_class("flat")
        delete.connect("clicked", lambda b: self._ask_delete_folder(folder, len(group)))
        head.append(delete)
        return head

    # -- dragging presets into folders ---------------------------------------
    def _drag_source(self, tile, path):
        """Let a preset tile be dragged, also when grabbed on a button."""
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        grab = {}

        def prepare(src, x, y):
            if self.preset_selection.active:
                return None                 # a click chooses then
            grab["at"] = (x, y)
            return Gdk.ContentProvider.new_for_value(str(path))

        def begin(src, _drag):
            x, y = grab.get("at", (0, 0))
            src.set_icon(Gtk.WidgetPaintable.new(tile), int(x), int(y))
            tile.add_css_class("dragged")

        source.connect("prepare", prepare)
        source.connect("drag-begin", begin)
        source.connect("drag-end", lambda *a: tile.remove_css_class("dragged"))
        tile.add_controller(source)

    @staticmethod
    def _drop_target(widget, dropped):
        """widget takes dragged presets: dropped(path) on a drop."""
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)

        def drop(_target, value, _x, _y):
            path = pathlib.Path(value) if isinstance(value, str) else None
            if path is None or path.parent != config.CONFIG_DIR \
                    or path.suffix != ".json" or not path.is_file():
                return False
            # After the drop has ended: the tiles are built again
            GLib.idle_add(lambda: dropped(path) and False)
            return True

        target.connect("drop", drop)
        widget.add_controller(target)

    def _drop_preset(self, path, folder):
        """A preset dropped into folder (None: out of any)."""
        self._set_preset_folder(path, self._preset_name(path), folder)

    def _preset_tile(self, name, path):
        """A tile of a saved preset: its name with a pencil and a menu for
        the rarer things, then Apply and Save Here."""
        active = self.active_preset is not None and path == self.active_preset
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       css_classes=["card", "tile", "preset-tile"])
        if active:
            tile.add_css_class("active")
        # Two lines at most, asking for the room of a usual name: a long
        # name made every tile as wide
        # It takes the room of the row, as at a game: with a filler after
        # the pencil, a long name got less and broke inside a word
        title = name_label(Gtk.Label(label=name, xalign=0, hexpand=True),
                           lines=2, chars=16)
        full_name_tip(title, name, 16)
        title.add_css_class("preset-name")
        # Right after the name, like at a folder and a game
        rename_btn = Gtk.Button(icon_name="document-edit-symbolic",
                                valign=Gtk.Align.CENTER,
                                tooltip_text=_("Change the name of this preset"))
        rename_btn.add_css_class("flat")
        rename_btn.connect("clicked", lambda b, p=path, n=name:
                           self._ask_rename_preset(p, n))
        head = Gtk.Box(spacing=6)
        head.append(title)
        if not self.preset_selection.active:     # a check mark is there then
            head.append(rename_btn)
            head.append(self._preset_menu(path))
        else:
            head.set_margin_end(36)
        sub = name_label(Gtk.Label(label=_("Active preset") if active else path.name,
                                   xalign=0, vexpand=True, valign=Gtk.Align.START))
        sub.add_css_class("dim-label")
        apply_btn = Gtk.Button(label=_("Apply"))
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", lambda b, p=path: self._ask_apply_preset(p))
        save_btn = Gtk.Button(label=_("Save Here"))
        save_btn.set_tooltip_text(_("Save current settings to this preset"))
        save_btn.connect("clicked", lambda b, p=path:
                         self._confirm_overwrite(p))
        for w in (head, sub, apply_btn, save_btn):
            tile.append(w)
        tile.set_tooltip_text(_("Drag onto a folder to put it there"))
        self._drag_source(tile, path)
        return tile

    def _preset_menu(self, path):
        """The ⋮ button of a preset tile, like the one of a game."""
        target = GLib.Variant.new_string(str(path))
        menu = Gio.Menu()
        values = Gio.MenuItem.new(_("Show Values"), None)
        values.set_action_and_target_value("win.show-preset", target)
        menu.append_item(values)
        menu.append_submenu(_("Share"), self._share_menu(path))
        delete = Gio.Menu()
        item = Gio.MenuItem.new(_("Delete"), None)
        item.set_action_and_target_value("win.delete-preset", target)
        delete.append_item(item)
        menu.append_section(None, delete)
        button = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                                valign=Gtk.Align.START,
                                tooltip_text=_("Values, Share and More"))
        button.add_css_class("flat")
        return button

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
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       css_classes=["card", "tile", "preset-tile"])
        title = Gtk.Label(label=_("Sim Racing Defaults"), xalign=0, wrap=True)
        title.add_css_class("preset-name")
        sub = Gtk.Label(label=_("Built in · {angle}, {strength} force feedback, "
                                "no spring or damper").format(
                                    angle=format_value(SETTINGS_BY_ID["SEN"],
                                                       SIM_DEFAULTS["SEN"]),
                                    strength=format_value(SETTINGS_BY_ID["FF"],
                                                          SIM_DEFAULTS["FF"])),
                        xalign=0, wrap=True, vexpand=True, valign=Gtk.Align.START,
                        max_width_chars=30)     # else the tiles get too wide
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
        groups = config.list_presets_by_folder()
        self._preset_names = {path: name for _f, group in groups
                              for name, path in group}
        menu = Gio.Menu()
        if self.active_preset is not None:
            update = Gio.Menu()
            update.append(_("Save to “{name}”").format(
                name=menu_label(self._active_name())), "win.update-preset")
            update.append(_("Remove “{name}” from Slot").format(
                name=menu_label(self._active_name())), "win.remove-preset-from-slot")
            menu.append_section(None, update)
        presets = Gio.Menu()
        for folder, group in groups:
            target = presets
            if folder is not None:              # a folder: a submenu
                target = Gio.Menu()
                presets.append_submenu(menu_label(folder), target)
            for name, path in group:
                item = Gio.MenuItem.new(menu_label(name), None)
                item.set_action_and_target_value(
                    "win.apply-preset", GLib.Variant.new_string(str(path)))
                target.append_item(item)
        if presets.get_n_items():
            menu.append_section(None, presets)
        app = self.get_application()
        if app is not None and hasattr(app, "tray"):
            app.tray.update_menu()          # the same presets there
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
            self.preset_label.set_label(_("Presets"))
            self.update_action.set_enabled(False)
            return
        modified = bool(self._unsaved_preset_changes())
        name = self._active_name()
        self.preset_label.set_label(f"{name} •" if modified else name)
        self.preset_button.set_tooltip_text(
            _("Preset “{name}” has unsaved changes").format(name=short(name))
            if modified else _("Apply or save a preset"))
        self.update_action.set_enabled(modified)

    def _collect_values(self):
        return {a: r.get() for a, r in self.rows.items()
                if r.row.get_sensitive()}

    def _preset_name(self, path):
        """The name of the preset at path, its file name if it can't be read."""
        try:
            return self._read_preset(path)[0]
        except (OSError, ValueError):
            return pathlib.Path(path).stem

    def _read_preset(self, path):
        """Return (name, values) of a preset file: the settings shown here,
        with valid values. Raises OSError or ValueError."""
        name, values = config.read_preset(path)
        return name, {k: v for k, v in sharing.clean_values(values).items()
                      if k in self.rows}

    def _follow_slot(self):
        """After the values were read: if the wheelbase is on another slot
        now (chosen here, on the wheelbase or after switching the tuning
        menu), the preset active before stays with the slot it was written
        to, and the preset last active in the new slot, if any, is active
        again. Only for this session, like the active preset itself. With
        the option on, the display shows the name of that preset, or is
        cleared.

        Slots are told apart by the mode of the tuning menu too: the
        Standard menu has setups of its own.
        """
        slot = self.device_values.get("SLOT")
        if slot is None:
            return
        key, old = (self.tuning_mode.advanced, slot), self._preset_slot
        if key == old:
            return
        self._preset_slot = key
        if old is None:
            return          # the first slot read: the active preset is in it
        if self.active_preset is not None:
            self._slot_presets[old] = self.active_preset
        else:
            self._slot_presets.pop(old, None)
        path, name, values = self._slot_presets.get(key), None, {}
        if path is not None:
            try:
                name, values = self._read_preset(path)
            except (OSError, ValueError):
                log.info("Preset %s of slot %s is gone", path.name, slot or "auto")
                del self._slot_presets[key]
                path = None
        if path != self.active_preset:
            log.info("Slot %s: preset %s", slot or "auto",
                     path.name if path else "none")
            self._set_active_preset(path, values)
        self._show_preset_name(name)        # or clear the display

    def _remove_preset_from_slot(self):
        """Take the active preset out of the slot: no preset is active any
        more, here or when coming back to the slot. Nothing is written: the
        values stay on the wheelbase and the preset stays as it is. With
        the option on, the display is cleared as for a slot without one."""
        if self.active_preset is None:
            return
        name = self._active_name()
        slot = self._preset_slot[1] if self._preset_slot else None
        log.info("Preset %s removed from slot %s", self.active_preset.name,
                 "unknown" if slot is None else slot or "auto")
        if self._preset_slot is not None:
            self._slot_presets.pop(self._preset_slot, None)
        self._set_active_preset(None, {})
        self._show_preset_name(None)
        self._notify(_("“{name}” removed from the slot, its values stay").format(
            name=short(name)))

    def _set_active_preset(self, path, values):
        self.active_preset = pathlib.Path(path) if path else None
        self.active_values = dict(values)
        self._presets_changed()

    def _apply_preset(self, path, game=None, connected=False):
        """Apply a preset; game: the game it is applied for, connected: the
        wheelbase just got connected while the game runs."""
        path = pathlib.Path(path)
        try:
            name, values = self._read_preset(path)
        except (OSError, ValueError) as e:
            if self._in_background:
                self.show_again()           # from the tray: show the error
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
                _("Wheelbase connected: preset “{name}” for {game} applied")
                .format(game=short(game), name=short(name)),
                _("Wheelbase connected: preset “{name}” for {game} loaded — "
                  "click Apply to write it").format(game=short(game), name=short(name)))
            self._notify_game_preset(game, name, outcome, failed, connected)
        elif game:
            # Without a wheelbase, it is written once one is connected
            # (_apply_running_game_preset).
            outcome, failed = self._apply_values(
                values,
                _("{game} started: preset “{name}” applied").format(
                    game=short(game), name=short(name)) if self.device_path else
                _("{game} started: preset “{name}” is applied once the "
                  "wheelbase is connected").format(game=short(game), name=short(name)),
                _("{game} started: preset “{name}” loaded — click Apply to "
                  "write it").format(game=short(game), name=short(name)))
            self._notify_game_preset(game, name, outcome, failed)
        else:
            outcome, failed = self._apply_values(
                values, _("Preset “{name}” applied").format(name=short(name)),
                _("Preset “{name}” loaded — click Apply to write it").format(
                    name=short(name)))
            if self._in_background:         # chosen in the tray menu
                self._notify_tray_preset(name, outcome, failed)
        if outcome == "written":
            self._show_preset_name(name)    # by hand or for a game
        if outcome in ("written", "no device"):
            # The preset is what the tiles compare to from now on: nothing
            # is marked as changed, and Undo goes back to the preset. Not
            # while it waits for Apply: Discard goes back to the wheelbase.
            for row in self.rows.values():
                row.set_baseline(row.get())

    def apply_from_tray(self, path):
        """A preset chosen in the tray menu. Unsaved changes of the active
        preset would be lost: then the window opens and asks first."""
        log.info("Preset %s chosen in the tray menu", pathlib.Path(path).name)
        if self._unsaved_preset_changes() or self._save_dialog is not None:
            self.show_again()
        self._ask_apply_preset(path)

    def _notify_tray_preset(self, name, outcome, failed):
        """While the window is closed, the only sign of what became of a
        preset chosen in the tray menu."""
        log.info("Notification for the tray: preset %s %s", name, outcome)
        self._desktop_notify("tray-preset", "Understeer", _outcome_text(
            name, outcome, failed,
            _("No wheelbase is connected, so preset “{name}” was not "
              "written.")))

    def _show_preset(self, path):
        """The values of a preset, by tab, without applying it; those that
        differ from the current settings are marked."""
        path = pathlib.Path(path)
        try:
            name, values = self._read_preset(path)
        except (OSError, ValueError) as e:
            self._error(_("Could not load preset"), str(e))
            return
        current = self._collect_values()
        differ = {a for a, v in values.items() if a in current and current[a] != v}
        dialog = self.fit_dialog(Adw.Dialog(title=short(name)))
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
        """After a wheelbase got connected: apply the preset of a running
        game, chosen before there was a wheelbase to write it to. Returns
        whether there was one."""
        for game in self.games_page.games:
            if game["game"] not in self._game_watch.running or not game["auto"]:
                continue
            path = config.game_preset_path(game)
            if path is not None:
                log.info("Wheelbase connected while %s runs", game["name"])
                self._apply_preset(path, game=game["name"], connected=True)
                return True
        return False

    def _notify_game_preset(self, game, name, outcome, failed, connected=False):
        """While the window is closed, tell with a notification of the
        desktop what became of the preset of a game that started, or that
        runs while the wheelbase got connected."""
        if not self._in_background or not config.get_setting(
                "game_notifications", True):
            return
        title = (_("Wheelbase connected, {game} runs") if connected
                 else _("{game} started")).format(game=short(game))
        log.info("Notification for %s: preset %s %s%s", game, name, outcome,
                 " after connecting" if connected else "")
        self._desktop_notify("game-preset", title, _outcome_text(
            name, outcome, failed,
            _("Preset “{name}” chosen. It is applied as soon as the "
              "wheelbase is connected.")))

    def _show_changes(self, dialog, diff):
        """Show diff in an Adw.AlertDialog, wide enough for its columns."""
        dialog.set_extra_child(self._changes_list(diff))
        if hasattr(dialog, "set_prefer_wide_layout"):         # Libadwaita 1.6
            dialog.set_prefer_wide_layout(True)

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
        """Save as a new preset: a name and, if wanted, a folder; the folder
        of the active preset is suggested, for a variant of it."""
        entry = Adw.EntryRow(title=_("Name"), activates_default=True)
        folder = self._folder_entry(
            config.preset_folder(self.active_preset)
            if self.active_preset is not None else None)
        return self._name_dialog(
            _("Save as Preset"), _("Saves all current settings under this name."),
            [entry, folder], _("Save"),
            lambda: self._save_preset(entry.get_text().strip(), folder.get_text()),
            entry, _("Enter a name for the preset."),
            self._suggestion_list(folder, _("Pick a Folder")))

    def _save_preset(self, name, folder=None):
        """folder: the one entered. A preset of that name is replaced and
        moves there; with none entered it stays in its folder."""
        if not name:
            return                          # the dialog asks for one
        path = config.preset_path(name)
        folder = config.existing_folder(folder)
        if path.exists():
            self._confirm_overwrite(path, new_name=name,
                                    folder=folder or config.KEEP)
            return
        if self._write_preset(path, name, folder):
            self._notify(_("Preset “{name}” saved").format(name=short(name)))

    # -- folders ---------------------------------------------------------------
    def _name_dialog(self, title, body, rows, action, done, name_row=None,
                     message=None, suggestions=None):
        """A dialog to enter names: Cancel and the action in the header bar,
        the rows below with body as description, then suggestions.

        name_row must not be empty: until it has a name the action is off,
        and once emptied it turns red with message below it, as the
        SteamGridDB key does. done() is called on the action."""
        dialog = Adw.Dialog(title=title, content_width=440)
        header = Adw.HeaderBar(show_start_title_buttons=False,
                               show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda b: dialog.close())
        ok = Gtk.Button(label=action)
        ok.add_css_class("suggested-action")
        header.pack_start(cancel)
        header.pack_end(ok)
        dialog.ok_button = ok                   # for the tests
        dialog.set_default_widget(ok)

        # The fields stay in place; only a long list of suggestions scrolls
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         margin_top=12, margin_bottom=18, margin_start=18,
                         margin_end=18)
        about = Gtk.Label(label=body, wrap=True, xalign=0)
        about.add_css_class("dim-label")
        column.append(about)
        fields = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        fields.add_css_class("boxed-list")
        for row in rows:
            fields.append(row)
        near = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        near.append(fields)
        column.append(near)
        if name_row is not None:
            # Its place is kept while hidden, so nothing moves when it shows
            hint = Gtk.Label(label=message, wrap=True, xalign=0, opacity=0,
                             margin_start=12)
            hint.add_css_class("error")
            hint.add_css_class("caption")
            near.append(hint)

            def changed(*_args):
                empty = not name_row.get_text().strip()
                ok.set_sensitive(not empty)
                hint.set_opacity(1 if empty else 0)
                if empty:
                    name_row.add_css_class("error")
                else:
                    name_row.remove_css_class("error")

            ok.set_sensitive(bool(name_row.get_text().strip()))
            name_row.connect("changed", changed)
        if suggestions is not None:
            column.append(suggestions)

        def on_ok(_button):
            dialog.close()
            done()

        ok.connect("clicked", on_ok)
        view = Adw.ToolbarView(content=column)
        view.add_top_bar(header)
        dialog.set_child(view)
        dialog.present(self)
        (name_row or rows[0]).grab_focus()
        return dialog

    @staticmethod
    def _folder_entry(folder):
        return Adw.EntryRow(title=_("Folder (optional)"), text=folder or "",
                            activates_default=True)

    def _folder_suggestions(self, games_only=False):
        """[(name, "folder" or "game")] to pick for a folder: the folders
        there are, then the games without a folder of their name."""
        folders = config.list_folders()
        found = [] if games_only else [(f, "folder") for f in folders]
        seen = {f.casefold() for f in folders}
        for game in getattr(self.games_page, "games", []):
            name = config.clean_folder(game.get("name"))
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                found.append((name, "game"))
        return found

    def _suggestion_list(self, entry, title, games_only=False):
        """Rows below the fields: a click puts the name into entry; the one
        entered is checked. None if there is nothing to suggest."""
        found = self._folder_suggestions(games_only)
        if not found:
            return None
        heading = Gtk.Label(label=title, xalign=0, margin_top=6)
        heading.add_css_class("heading")
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        checks = []
        for name, kind in found:
            row = cap_row(Adw.ActionRow(title=name, activatable=True,
                                        use_markup=False))
            row.add_prefix(Gtk.Image.new_from_icon_name(
                "folder-symbolic" if kind == "folder"
                else "applications-games-symbolic"))
            check = Gtk.Image.new_from_icon_name("object-select-symbolic")
            row.add_suffix(check)
            checks.append((name, check))
            row.connect("activated", lambda r, n=name: (
                entry.set_text(n), entry.grab_focus(), entry.set_position(-1)))
            rows.append(row)

        def mark(*_args):
            text = entry.get_text().strip().casefold()
            for name, check in checks:
                check.set_opacity(1 if name.casefold() == text else 0)

        entry.connect("changed", mark)
        mark()
        # Up to four rows as they are; more scroll in here, with half a
        # row showing that there are more
        scroll = Gtk.ScrolledWindow(child=rows, propagate_natural_height=True,
                                    max_content_height=4 * 56 + 36,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_overflow(Gtk.Overflow.HIDDEN)    # keep the round corners
        scroll.add_css_class("suggestions")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(heading)
        box.append(scroll)
        return box

    def _ask_new_folder(self, path=None):
        """Make a folder; path: a preset dropped on "New Folder", put in it."""
        name = self._preset_name(path) if path is not None else None
        entry = self._folder_entry(None)
        entry.set_title(_("Name"))
        return self._name_dialog(
            _("New Folder"),
            _("“{name}” is put into it.").format(name=short(name)) if name else
            _("A folder groups presets here and in the menus, e.g. several "
              "for one game. Drag presets onto it."),
            [entry], _("Create"),
            lambda: self._new_folder(entry.get_text(), path, name),
            entry, _("Enter a name for the folder."),
            self._suggestion_list(entry, _("From Your Games"), games_only=True))

    def _new_folder(self, folder, path=None, name=None):
        folder = config.clean_folder(folder)
        if folder is None:
            return                          # the dialog asks for one
        if path is not None:
            config.add_folder(folder)
            self._set_preset_folder(path, name, folder)
            return
        existed = folder.casefold() in {f.casefold() for f in config.list_folders()}
        folder = config.add_folder(folder)
        log.info("Folder %r made", folder)
        self._presets_changed()
        self._notify((_("Folder “{folder}” already exists") if existed else
                      _("Folder “{folder}” made")).format(folder=short(folder)))

    def _ask_delete_folder(self, folder, count):
        """Delete a folder, keeping its presets or with them. Asks only if
        it holds some."""
        if not count:
            self._delete_folder(folder)
            return
        paths = [p for _n, p in dict(config.list_presets_by_folder(
            empty=True)).get(folder, [])]
        files = {p.name for p in paths}
        body = _("Presets in it: {n}. Keep them, shown without a folder, or "
                 "delete them too? Deleted presets go to the trash.").format(n=count)
        body += self._games_note(files)
        dialog = Adw.AlertDialog(
            heading=_("Delete Folder “{folder}”?").format(folder=short(folder)),
            body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("keep", _("Keep Presets"))
        dialog.add_response("all", _("Delete with Presets"))
        dialog.set_default_response("keep")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("all", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, response):
            if response == "keep":
                self._delete_folder(folder)
            elif response == "all":
                self._delete_presets(paths, folder=folder)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _delete_folder(self, folder):
        try:
            moved = config.delete_folder(folder)
        except (OSError, ValueError) as e:
            self._error(_("Could not delete folder"), str(e))
            self._presets_changed()
            return
        log.info("Folder %r deleted, %d presets taken out", folder, moved)
        self._presets_changed()
        self._notify(_("Folder “{folder}” deleted").format(folder=short(folder)))

    def _set_preset_folder(self, path, name, folder):
        folder = config.existing_folder(folder)
        if folder == config.preset_folder(path):
            return
        try:
            config.set_preset_folder(path, folder)
        except (OSError, ValueError) as e:
            self._error(_("Could not move preset"), str(e))
            return
        log.info("Preset %s moved to folder %r", pathlib.Path(path).name, folder)
        self._presets_changed()
        self._notify(_("“{name}” moved to “{folder}”").format(
            name=short(name), folder=short(folder)) if folder else
            _("“{name}” is in no folder now").format(name=short(name)))

    def _ask_rename_folder(self, folder):
        entry = Adw.EntryRow(title=_("Name"), text=folder, activates_default=True)
        dialog = self._name_dialog(
            _("Rename Folder “{folder}”").format(folder=short(folder)),
            _("With the name of another folder, both become one. Left empty, "
              "the presets are taken out of the folder."),
            [entry], _("Rename"), lambda: self._rename_folder(folder, entry.get_text()))
        entry.select_region(0, -1)
        return dialog

    def _rename_folder(self, old, new):
        new = config.existing_folder(new, besides=old)
        if new == old:
            return
        try:
            moved = config.rename_folder(old, new)
        except (OSError, ValueError) as e:
            self._error(_("Could not rename folder"), str(e))
            self._presets_changed()             # some may have moved
            return
        log.info("Folder %r renamed to %r (%d presets)", old, new, moved)
        self._presets_changed()
        self._notify(_("Folder “{old}” renamed to “{new}”").format(
                         old=short(old), new=short(new)) if new else
                     _("Presets taken out of folder “{old}”").format(old=short(old)))

    def _write_preset(self, path, name, folder=config.KEEP):
        values = self._collect_values()
        try:
            config.write_preset(path, name, values, folder)
        except OSError as e:
            self._error(_("Could not save preset"), str(e))
            return False
        self._set_active_preset(path, values)
        return True

    def _confirm_overwrite(self, path, new_name=None, folder=config.KEEP):
        """Ask before replacing a preset file with the current settings,
        showing what changes in it. folder: where it goes, as at
        config.write_preset."""
        path = pathlib.Path(path)
        try:
            name, stored = self._read_preset(path)
        except (OSError, ValueError):
            name, stored = path.stem, {}
        name = new_name or name
        diff = self._preset_diff(stored)
        body = (_("Settings that change:") if diff else
                _("The preset already matches the current settings."))
        dialog = Adw.AlertDialog(
            heading=_("Overwrite “{name}”?").format(name=short(name)), body=body)
        if diff:
            self._show_changes(dialog, diff)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("overwrite", _("Overwrite"))
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("overwrite",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda d, r: self._overwrite_preset(
            path, name, folder) if r == "overwrite" else None)
        dialog.present(self)

    def confirm_quit(self, quit_now):
        """Call quit_now(), but first offer to save unsaved changes of the
        active preset. The wheelbase keeps its settings either way."""
        if self._shut_down or not self._unsaved_preset_changes():
            if self._save_dialog is None:
                quit_now()
            return
        if self._in_background:
            self.show_again()               # also reads the wheelbase again
        self._ask_save_changes(
            quit_now, "quit",
            _("These settings differ from the preset. The wheelbase "
              "keeps them either way."))

    def _ask_apply_preset(self, path):
        """Apply a preset chosen by the user; first offer to save unsaved
        changes of the active preset, applying would lose them."""
        path = pathlib.Path(path)
        name = self._preset_name(path)
        self._ask_save_changes(
            lambda: self._apply_preset(path), f"apply {path.name}",
            _("These settings differ from the preset. Then “{name}” is "
              "applied.").format(name=short(name)))

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
            heading=_("Save Changes to “{name}”?").format(name=short(name)), body=body)
        self._show_changes(dialog, diff)
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

    def _overwrite_preset(self, path, name, folder=config.KEEP):
        """Replace a preset with the current settings; offer undo."""
        try:
            old_text = path.read_text(encoding="utf-8")
            _n, old_values = self._read_preset(path)
        except (OSError, ValueError):
            old_text, old_values = None, {}
        if not self._write_preset(path, name, folder):
            return
        def undo():
            try:
                config.write_file(path, old_text)
            except OSError as e:
                self._error(_("Could not save preset"), str(e))
                return
            self._set_active_preset(path, old_values)

        self._notify(_("Preset “{name}” saved").format(name=short(name)),
                     _("Undo") if old_text is not None else None, undo)

    # -- sharing ---------------------------------------------------------------
    def _shared(self, path):
        """(name, values, folder) of a preset to share, or None after an
        error."""
        path = pathlib.Path(path)
        try:
            return (*self._read_preset(path), config.preset_folder(path))
        except (OSError, ValueError) as e:
            self._error(_("Could not load preset"), str(e))
            return None

    def _copy_preset(self, path, as_json):
        shared = self._shared(path)
        if shared is None:
            return
        name, values, folder = shared
        if as_json:
            text = sharing.preset_json(name, values, folder)
            message = _("Preset “{name}” copied as JSON")
        else:
            text = sharing.preset_text(name, values, folder)
            message = _("Preset “{name}” copied as text")
        self.get_clipboard().set(text)
        log.info("Preset %s copied as %s", pathlib.Path(path).name,
                 "JSON" if as_json else "text")
        self._notify(message.format(name=short(name)))

    @staticmethod
    def _preset_dialog(title, **props):
        """A file dialog for shared presets."""
        return files.file_dialog(title, _("Presets (JSON or text)"),
                                 ("*.json", "*.txt"), **props)

    def _export_preset(self, path):
        shared = self._shared(path)
        if shared is None:
            return
        name, values, folder = shared
        dialog = self._preset_dialog(_("Save Preset as File"),
                                     initial_name=pathlib.Path(path).name)

        def chosen(d, result):
            dest = files.chosen_path(d.save_finish, result)
            if dest is None:
                return                          # cancelled
            try:
                config.write_file(pathlib.Path(dest),
                                  sharing.preset_json(name, values, folder))
            except OSError as e:
                self._error(_("Could not save preset"), str(e))
                return
            log.info("Preset %s saved as file", pathlib.Path(path).name)
            self._notify(_("Preset “{name}” saved as file").format(name=short(name)),
                         _("Show Folder"), lambda: files.show_in_folder(self, dest))

        dialog.save(self, None, chosen)

    def _ask_import_preset(self):
        dialog = self._preset_dialog(_("Import Presets"))

        def chosen(d, result):
            source = files.chosen_path(d.open_finish, result)
            if source is None:
                return                          # cancelled
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
            self._import_text(text, source.stem)

        dialog.open(self, None, chosen)

    def _ask_paste_preset(self, text=None, fallback_name=""):
        """A text field to paste shared presets into, e.g. a whole forum
        thread. Below it, every preset found, with its values and a check
        box; only the checked settings of the checked ones are added.

        Not an Adw.AlertDialog: all of that is a window handle, so a double
        click to select a word would maximize the window."""
        # Big: a pasted thread and the presets found in it should be easy
        # to read. Libadwaita makes it smaller in a small window.
        dialog = self.fit_dialog(Adw.Dialog(title=_("Paste Presets")))
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
        if found.folder:
            subtitle = _("Folder: {folder}").format(folder=short(found.folder)) + \
                " · " + subtitle
        row = cap_row(Adw.ExpanderRow(title=found.name, subtitle=subtitle))
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

    def _import_text(self, text, fallback_name=""):
        """Add the presets in a shared text or file: one right away (asking
        first if its name is taken), more to choose from."""
        try:
            result = sharing.find_presets(text, fallback_name)
        except sharing.Error as e:
            self._not_a_preset(str(e))
            return
        if len(result.presets) == 1 and not result.skipped:
            self._import_presets(result.presets)
        else:                           # choose which ones
            self._ask_paste_preset(text, fallback_name)

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
            if self._write_imported(path, name, values, first.folder):
                added.append((path, name))

        if not path.exists():
            add(path, name)
            self._import_presets(rest, added)
            return
        try:
            _n, stored = self._read_preset(path)
        except (OSError, ValueError):
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
            heading=_("“{name}” Already Exists").format(name=short(name)), body=body)
        if diff:
            self._show_changes(dialog, diff)
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
                new = free_name(
                    name, lambda n: config.preset_path(n).exists())
                add(config.preset_path(new), new)
            self._import_presets(rest, added)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _write_imported(self, path, name, values, folder=None):
        """folder: the shared one; without, a replaced preset keeps its own."""
        try:
            config.write_preset(path, name, values, config.existing_folder(folder)
                                if folder else config.KEEP)
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
            self._notify(_("Preset “{name}” imported").format(name=short(name)),
                         _("Apply"), lambda: self._ask_apply_preset(path))
        else:
            self._notify(_("Presets imported: {n}").format(n=len(added)))

    def _ask_rename_preset(self, path, name):
        entry = Adw.EntryRow(title=_("Name"), text=name, activates_default=True)
        dialog = self._name_dialog(
            _("Rename Preset"), _("Games that use this preset keep it."),
            [entry], _("Rename"),
            lambda: self._rename_preset(path, name, entry.get_text().strip()),
            entry, _("Enter a name for the preset."))
        entry.select_region(0, -1)
        return dialog

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
        self._slot_presets = {k: new_path if p == path else p
                              for k, p in self._slot_presets.items()}
        self.games_page.games = config.load_games()
        self._presets_changed()
        self._notify(_("Preset renamed to “{name}”").format(name=short(new_name)))

    def _games_using(self, files):
        """Names of the games that use one of these preset files."""
        return [g["name"] for g in getattr(self.games_page, "games", [])
                if g.get("preset") in files]

    def _games_note(self, files):
        """For a delete dialog: which games use one of these preset files,
        or ""."""
        games = self._games_using(files)
        if not games:
            return ""
        return "\n\n" + _("Used by: {games}. Without their preset, these games "
                           "get none applied.").format(games=join_names(games))

    def _delete_presets(self, paths, folder=None):
        """Put presets in the bin; the message offers Undo, and they stay
        in the bin (the Trash button) for trash.KEEP_DAYS days. folder:
        their folder, deleted with them if all of them went; Undo brings
        it back too."""
        items, names, files = [], [], set()
        for path in paths:
            name = self._preset_name(path)
            try:
                items.append(trash.put_preset(path))
            except OSError as e:
                self._error(_("Could not delete preset"), str(e))
                break
            names.append(name)
            files.add(pathlib.Path(path).name)
            if self.active_preset == pathlib.Path(path):
                self.active_preset, self.active_values = None, {}
            self._slot_presets = {k: p for k, p in self._slot_presets.items()
                                  if p != pathlib.Path(path)}
        if not items:
            return
        if folder is not None and len(items) == len(paths):
            try:
                config.delete_folder(folder)
                log.info("Folder %r deleted with %d presets", folder, len(items))
            except OSError as e:
                self._error(_("Could not delete folder"), str(e))
                folder = None
        else:
            folder = None
        self._presets_changed()
        self._trash_changed()
        games = self._games_using(files)
        if folder is not None:
            text = _("Folder “{folder}” deleted. Presets deleted: {n}").format(
                folder=short(folder), n=len(names))
        elif len(names) == 1:
            text = _("“{name}” deleted").format(name=short(names[0]))
        else:
            text = _("Presets deleted: {n}").format(n=len(names))
        if games:
            text += " · " + _("Used by: {games}").format(games=join_names(games))

        def undo():
            for item in items:
                try:
                    trash.restore_preset(item)
                except (trash.Error, OSError) as e:
                    self._error(_("Could not restore presets"), str(e))
            self._presets_changed()
            self._trash_changed()

        self._notify(text, _("Undo"), undo)
