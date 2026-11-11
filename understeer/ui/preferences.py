"""The preferences window, with export, import and the log file."""
import logging
import time

from gi.repository import Adw, Gio, GLib, Gtk

from .. import background, backup, config, i18n
from .. import log as session_log
from ..i18n import _
from .look_prefs import LookGroups
from .report_dialog import ReportDialog

LAYOUTS = ["full", "compact"]      # tile sizes, see SettingTile.set_compact

log = logging.getLogger("understeer.window")


def _groups(widget):
    """The preferences groups inside widget (not those inside them)."""
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Adw.PreferencesGroup):
            yield child
        else:
            yield from _groups(child)
        child = child.get_next_sibling()


class PreferencesMixin:
    """Part of TunerWindow: the preferences window and what its Data page
    does."""

    def _build_preferences(self):
        """The preferences dialog, made once and shown again when reopened.

        After the language changed only the content of its pages is made
        again, so open preferences stay open, on the same page. The pages
        themselves stay: replacing the shown page made Libadwaita crash now
        and then.
        """
        prefs = getattr(self, "prefs", None)
        if prefs is None:
            self.prefs = prefs = Adw.PreferencesDialog(
                content_width=720, content_height=680, search_enabled=False)
            # A key typed just before closing counts without Enter.
            prefs.connect("closed", lambda d: self.games_page.key_entry.flush())
            self._prefs_pages = {}
        rebuilt = bool(self._prefs_pages)
        prefs.set_title(_("Preferences"))

        # -- General --------------------------------------------------------
        general = self._prefs_page("general", _("General"), "preferences-system-symbolic")
        look = LookGroups()
        appearance = Adw.PreferencesGroup(title=_("Appearance"))
        appearance.add(look.scheme_row)
        layout_row = Adw.ComboRow(title=_("Layout"),
                                  subtitle=_("Size of the tiles"))
        layout_row.set_model(Gtk.StringList.new([_("Full"), _("Compact")]))
        layout_row.set_selected(LAYOUTS.index(self.layout))
        layout_row.connect("notify::selected", lambda r, _p:
                           self._set_layout(LAYOUTS[r.get_selected()]))
        appearance.add(layout_row)

        languages = [None] + i18n.available_languages()
        current = config.get_setting("language")
        language_row = Adw.ComboRow(title=_("Language"))
        language_row.set_model(Gtk.StringList.new(
            [_("System language")] + [i18n.language_name(c) for c in languages[1:]]))
        language_row.set_selected(languages.index(current)
                                  if current in languages else 0)
        language_row.connect("notify::selected", lambda r, _p:
                             self._set_language(languages[r.get_selected()]))
        appearance.add(language_row)
        general.add(appearance)
        general.add(look.theme_group)
        general.add(look.shape_group)

        # -- Wheel base -----------------------------------------------------
        wheel = self._prefs_page("wheelbase", _("Wheel Base"), "understeer-steering-symbolic")
        hardware = Adw.PreferencesGroup(title=_("Hardware"))
        self.base_row = Adw.ActionRow(title=_("Wheel Base"), subtitle_selectable=True)
        self.rim_row = Adw.ActionRow(title=_("Steering Wheel"), subtitle_selectable=True)
        hardware.add(self.base_row)
        hardware.add(self.rim_row)
        wheel.add(hardware)
        self._show_wheel_info()

        self.slot_names_group = Adw.PreferencesGroup(
            title=_("Slot Names"),
            description=_("Name the slots like on the display of your wheel "
                          "base, e.g. “A” or “Set 1”. The names are saved for "
                          "this wheel base model."))
        self.slot_name_rows = {}
        self._show_slot_name_rows()
        wheel.add(self.slot_names_group)

        system = Adw.PreferencesGroup(title=_("System"))
        self.perm_row = Adw.ActionRow(title=_("Write Permissions"), subtitle="")
        self.perm_btn = Gtk.Button(label=_("Set Up"))
        self.perm_btn.set_valign(Gtk.Align.CENTER)
        self.perm_btn.connect("clicked", lambda b: self._fix_permissions())
        self.perm_row.add_suffix(self.perm_btn)
        system.add(self.perm_row)
        reset = Adw.ActionRow(
            title=_("Reset All Tuning Slots"),
            subtitle=_("Clears all tuning slots stored on the wheel base."))
        reset_btn = Gtk.Button(label=_("Reset…"))
        reset_btn.add_css_class("destructive-action")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.connect("clicked", lambda b: self._confirm_reset())
        reset.add_suffix(reset_btn)
        system.add(reset)
        wheel.add(system)

        rumble = Adw.PreferencesGroup(
            title=_("Pedal Rumble Test"),
            description=_("Sends a single rumble command to ClubSport Pedals "
                          "V3 or CSL Elite/LC pedals."))
        self.rumble_adj = {}
        for key, title in [("a", _("Throttle Motor")),
                           ("b", _("Brake Motor")),
                           ("dur", _("Duration (×10 ms)"))]:
            r = Adw.SpinRow.new_with_range(0, 255, 1)
            r.set_title(title)
            r.set_value(20 if key == "dur" else 128)
            self.rumble_adj[key] = r
            rumble.add(r)
        fire = Adw.ActionRow(title=_("Send Rumble"))
        fire_btn = Gtk.Button(label=_("Send"))
        fire_btn.set_valign(Gtk.Align.CENTER)
        fire_btn.connect("clicked", lambda b: self._send_rumble())
        fire.add_suffix(fire_btn)
        rumble.add(fire)
        self.rumble_group = rumble
        wheel.add(rumble)

        # -- Games ----------------------------------------------------------
        games_prefs = self._prefs_page("games", _("Games"), "applications-games-symbolic")
        games_prefs.add(self.games_page.options)
        games_prefs.add(self._background_group())

        # -- Data -----------------------------------------------------------
        data = self._prefs_page("data", _("Data"), "folder-symbolic")
        transfer = Adw.PreferencesGroup(
            title=_("Transfer"),
            description=_("Presets, games with their pictures and all "
                          "settings in one file, e.g. to move them to "
                          "another computer."))
        for title, subtitle, label, callback in (
                (_("Export"), _("Saves everything to a ZIP file."),
                 _("Export…"), self._ask_export),
                (_("Import"), _("Adds the presets and games from an exported "
                                "file. Ones with the same name are replaced."),
                 _("Import…"), self._ask_import)):
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda b, cb=callback: cb())
            row.add_suffix(button)
            transfer.add(row)
        data.add(transfer)

        problems = Adw.PreferencesGroup(title=_("Reporting Problems"))
        report_row = Adw.ActionRow(
            title=_("Report a Problem"),
            subtitle=_("Opens a filled-in issue on GitHub with the details "
                       "needed to help you."))
        report_btn = Gtk.Button(label=_("Report…"), valign=Gtk.Align.CENTER)
        report_btn.connect("clicked", lambda b: ReportDialog(self))
        report_row.add_suffix(report_btn)
        problems.add(report_row)
        log_row = Adw.ActionRow(
            title=_("Log File"),
            subtitle=_("Records what happened in this session. Attach it "
                       "when you report a problem."))
        log_row.set_tooltip_text(str(session_log.LOG_FILE))
        for label, callback in ((_("Open"), self._open_log),
                                (_("Show Folder"), self._open_log_folder)):
            button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda b, cb=callback: cb())
            log_row.add_suffix(button)
        problems.add(log_row)
        data.add(problems)
        if rebuilt:
            prefs.set_focus(None)   # not on whatever took the old focus' place

    def _background_group(self):
        """Running in the background and starting at login."""
        group = Adw.PreferencesGroup(title=_("Background"))
        run = Adw.SwitchRow(
            title=_("Run in the Background"),
            subtitle=_("Presets of games are also applied with the window "
                       "closed. An icon in the system tray shows that "
                       "Understeer runs; quit it there or in the main menu."))
        run.set_active(bool(config.get_setting("background")))
        login = Adw.SwitchRow(
            title=_("Start at Login"),
            subtitle=_("Starts in the background when you log in."))
        login.set_active(background.autostart_enabled())
        login.set_sensitive(run.get_active())
        notify = Adw.SwitchRow(
            title=_("Notify When a Game Starts"),
            subtitle=_("With the window closed, a notification tells whether "
                       "the preset of the game was applied."))
        notify.set_active(bool(config.get_setting("game_notifications", True)))
        notify.set_sensitive(run.get_active())

        def run_changed(row, _param):
            on = row.get_active()
            config.set_setting("background", on or None)
            log.info("Running in the background %s", "on" if on else "off")
            self.get_application().update_tray()
            login.set_sensitive(on)
            notify.set_sensitive(on)
            if not on:
                login.set_active(False)

        def notify_changed(row, _param):
            on = row.get_active()
            config.set_setting("game_notifications", None if on else False)
            log.info("Notifications when a game starts %s", "on" if on else "off")

        def login_changed(row, _param):
            on = row.get_active()
            if on == background.autostart_enabled():
                return
            try:
                background.set_autostart(on)
                log.info("Start at login %s (%s)", "on" if on else "off",
                         background.autostart_file())
            except OSError as e:
                self._error(_("Could not change the start at login"), str(e))
                row.set_active(not on)      # back; the check above stops here

        run.connect("notify::active", run_changed)
        login.connect("notify::active", login_changed)
        notify.connect("notify::active", notify_changed)
        group.add(run)
        group.add(login)
        group.add(notify)
        return group

    def _prefs_page(self, name, title, icon_name):
        """The preferences page name, emptied, or a new one."""
        page = self._prefs_pages.get(name)
        if page is None:
            page = Adw.PreferencesPage(name=name, icon_name=icon_name)
            self.prefs.add(page)
            self._prefs_pages[name] = page
        else:
            for group in list(_groups(page)):
                page.remove(group)
        page.set_title(title)
        return page

    def _show_preferences(self, page=None):
        if page:
            self.prefs.set_visible_page_name(page)
        self.prefs.present(self)
        self.prefs.set_focus(None)      # don't start typing in a slot name

    def _prefs_open(self):
        """Whether the preferences are shown. A closed Adw.Dialog still
        counts as visible, but it no longer sits in the window."""
        return self.prefs.get_parent() is not None

    @staticmethod
    def _zip_filters():
        zips = Gtk.FileFilter(name=_("Understeer Export (ZIP)"))
        zips.add_pattern("*.zip")
        zips.add_mime_type("application/zip")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(zips)
        return filters, zips

    def _ask_export(self):
        filters, zips = self._zip_filters()
        dialog = Gtk.FileDialog(
            title=_("Export"), filters=filters, default_filter=zips,
            initial_name=time.strftime("Understeer-%Y-%m-%d.zip"))
        dialog.save(self, None, self._export_chosen)

    def _export_chosen(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return                              # cancelled
        path = file.get_path() if file else None
        if path is None:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            counts = backup.export(path)
        except OSError as e:
            self._error(_("Could not export"), str(e))
            return
        self._notify(_("Exported (presets: {presets}, games: {games})").format(
            **counts), _("Show Folder"),
            lambda: Gtk.FileLauncher.new(Gio.File.new_for_path(path))
            .open_containing_folder(self, None, None))

    def _ask_import(self):
        filters, zips = self._zip_filters()
        dialog = Gtk.FileDialog(title=_("Import"), filters=filters,
                                default_filter=zips)
        dialog.open(self, None, self._import_chosen)

    def _import_chosen(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return                              # cancelled
        path = file.get_path() if file else None
        if path is None:
            return
        try:
            found = backup.read(path)
        except (backup.Error, OSError) as e:
            log.warning("Import of %s refused: %s", path, e)
            self._error(_("Could not import"),
                        _("This is not a file exported by Understeer, or it "
                          "is damaged."))
            return
        counts = backup.summary(found)
        dialog = Adw.AlertDialog(
            heading=_("Import from This File?"),
            body=_("Presets: {presets} · Games: {games} · Pictures: "
                   "{covers}\n\nPresets and games with the same name are replaced, "
                   "everything else stays. Settings like the language and "
                   "slot names are taken from the file.\n\nThe current "
                   "state is saved first, as before-import.zip next to "
                   "the log file."
                   ).format(**counts))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("import", _("Import"))
        dialog.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: r == "import" and
                       self._import(found))
        dialog.present(self)

    def _import(self, found):
        safety = session_log.LOG_DIR / "before-import.zip"
        try:
            backup.export(safety)
            backup.import_(found)
        except OSError as e:
            self._error(_("Could not import"), str(e))
            return
        # Show what was imported: language, layout, presets, games, names.
        i18n.set_language(config.get_setting("language"))
        layout = config.get_setting("layout")
        self.layout = layout if layout in LAYOUTS else "full"
        self._apply_layout()
        self.axis_roles = config.load_axis_roles()

        def rebuild():
            self.relocalize()
            counts = backup.summary(found)
            self._notify(_("Imported (presets: {presets}, games: {games})").format(
                **counts))
        GLib.idle_add(lambda: rebuild() and False)

    def _open_log(self):
        """Open the session log in the default text editor."""
        if session_log.LOG_FILE.exists():
            Gtk.FileLauncher.new(Gio.File.new_for_path(
                str(session_log.LOG_FILE))).launch(self, None, None)

    def _open_log_folder(self):
        Gtk.FileLauncher.new(Gio.File.new_for_path(
            str(session_log.LOG_FILE))).open_containing_folder(self, None, None)
