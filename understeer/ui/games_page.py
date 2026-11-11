"""The Games page: one tile per game with its cover and preset."""
import logging
import pathlib
import threading

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .. import config, games, steamgriddb, trash
from ..i18n import _
from . import cover_search
from .live import CoverView
from .selection import Selection

log = logging.getLogger("understeer.games")


def _steam_appid(game):
    kind, _sep, appid = game["game"].partition(":")
    return appid if kind == "steam" else None


class GamesPage:
    """Builds and updates the Games page; `widget` is the page itself and
    `options` the cover settings shown in the preferences.

    Running games are reported by the window through set_running().
    """

    def __init__(self, window):
        self.window = window
        self.games = config.load_games()
        self.running = set()
        self._downloading = set()   # ids of games whose cover is being fetched
        self._failed = set()        # ... or that had no cover online
        self._search = None         # [asked, found] of a search the user started
        roots = games.steam_roots()
        log.info("Steam installations: %s", ", ".join(map(str, roots)) or "none")
        for root in roots:
            log.info("Steam libraries in %s: %s", root,
                     ", ".join(map(str, games.library_folders(root))))
        log.info("%d games set up, cover downloads %s", len(self.games),
                 "on" if config.get_setting("download_covers", False) else "off")

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        column.add_css_class("games")
        for side in ("top", "bottom", "start", "end"):
            getattr(column, f"set_margin_{side}")(12)
        self.selection = Selection(
            _("Delete"), lambda: self.refresh(),
            lambda ids: self._remove_games(
                [g for g in self.games if g["game"] in set(ids)]))
        label = Gtk.Label(label=_("Games"), xalign=0, hexpand=True)
        label.add_css_class("section-title")
        title = Gtk.Box(spacing=12)
        title.append(label)
        # Spaced as on the Presets page, so the buttons stay in place
        buttons = Gtk.Box(spacing=6, valign=Gtk.Align.END)
        buttons.append(self.window._trash_button("game"))
        buttons.append(self.selection.button)
        title.append(buttons)
        desc = Gtk.Label(
            label=_("Give each game a preset. While Understeer is open, it "
                    "applies the preset as soon as the game starts."),
            xalign=0, wrap=True)
        desc.add_css_class("dim-label")
        desc.set_margin_start(6)
        desc.set_margin_bottom(6)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                homogeneous=True, min_children_per_line=2,
                                max_children_per_line=8, column_spacing=12,
                                row_spacing=12, valign=Gtk.Align.START)

        # Shown in the preferences, not on the page.
        options = self.options = Adw.PreferencesGroup(title=_("Covers"))
        self.download_row = Adw.SwitchRow(
            title=_("Download Missing Covers"),
            subtitle=_("Steam games from Steam, other games from SteamGridDB "
                       "if an API key is entered below. When off, Understeer "
                       "uses only the pictures already on this computer."))
        self.download_row.set_active(bool(config.get_setting("download_covers", False)))
        self.search_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                     valign=Gtk.Align.CENTER)
        self.search_btn.add_css_class("flat")
        self.search_btn.set_tooltip_text(_("Search missing covers now"))
        self.search_btn.set_sensitive(self.download_row.get_active())
        self.search_btn.connect("clicked", lambda b: self.search_covers())
        self.download_row.add_suffix(self.search_btn)
        self.download_row.connect("notify::active", self._on_download_toggled)
        options.add(self.download_row)

        self.key_entry = cover_search.KeyRow(self.window._notify,
                                             self._on_key_accepted)
        self.key_row = self.key_entry.row
        get_key = Gtk.Button(icon_name="web-browser-symbolic", valign=Gtk.Align.CENTER)
        get_key.add_css_class("flat")
        get_key.set_tooltip_text(_("Get a free API key on SteamGridDB"))
        get_key.connect("clicked", lambda b: cover_search.open_key_page(self.window))
        self.key_row.add_suffix(get_key)
        options.add(self.key_row)

        for w in (title, desc, self.flow):
            column.append(w)
        clamp = Adw.Clamp(maximum_size=1200, tightening_threshold=900)
        clamp.set_child(column)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vexpand=True)
        scroll.set_child(clamp)
        self.widget = self.selection.page(scroll)
        self.refresh()

    # -- state ---------------------------------------------------------------
    def set_running(self, running):
        """Ids of running games that are on this page."""
        if running != self.running:
            self.running = set(running)
            self.refresh()

    def _save(self):
        config.save_games(self.games)

    def _on_download_toggled(self, row, _param):
        active = row.get_active()
        config.set_setting("download_covers", active or None)
        self.search_btn.set_sensitive(active)
        log.info("Cover downloads %s", "on" if active else "off")
        if active:
            self.search_covers()

    def _on_key_accepted(self):
        self._failed.clear()                # may find more now
        if config.get_setting("download_covers", False):
            self.refresh()

    def key_changed(self):
        """The cover search saved a key."""
        self.key_entry.show_saved()

    # -- tiles ---------------------------------------------------------------
    def refresh(self):
        self.flow.remove_all()
        self.selection.available([g["game"] for g in self.games])
        add = self._add_tile()
        add.set_sensitive(not self.selection.active)
        self.flow.append(add)
        # In the order of the menus, a preset in a folder as "Folder – Name"
        presets = [(_("{folder} – {name}").format(folder=folder, name=name)
                    if folder else name, path)
                   for folder, group in config.list_presets_by_folder()
                   for name, path in group]
        for game in self.games:
            self.flow.append(self.selection.wrap(
                self._game_tile(game, presets), game["game"]))

    def _add_tile(self):
        button = Gtk.Button()
        button.add_css_class("card")
        button.add_css_class("tile")
        button.add_css_class("new-preset")
        button.connect("clicked", lambda b: self._ask_game())
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                        valign=Gtk.Align.CENTER)
        plus = Gtk.Image.new_from_icon_name("list-add-symbolic")
        plus.set_pixel_size(32)
        plus.add_css_class("tile-icon")
        plus.set_halign(Gtk.Align.CENTER)
        inner.append(plus)
        inner.append(Gtk.Label(label=_("Add Game…"), css_classes=["heading"]))
        button.set_child(inner)
        return button

    def _cover_file(self, game):
        """The picture to show: chosen by the user, else Steam's cover."""
        path = config.game_cover_path(game)
        appid = _steam_appid(game)
        if path is None and appid:
            path = games.find_cover(appid, config.COVER_DIR)
        return path

    def _cover(self, game):
        path = self._cover_file(game)
        if path is None and config.get_setting("download_covers", False) \
                and self._can_download(game):
            self._download(game)
        if path is not None:
            child = Gtk.Picture.new_for_filename(str(path))
            child.set_content_fit(Gtk.ContentFit.COVER)
            child.set_can_shrink(True)
        else:
            child = self._placeholder(game)
        cover = CoverView(child)                # portrait, like Steam's library
        cover.add_css_class("game-cover")
        return cover

    def _placeholder(self, game):
        """Empty cover that tells whether a cover is being searched."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("cover-placeholder")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        valign=Gtk.Align.CENTER, vexpand=True)
        if game["game"] in self._downloading:
            icon = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
            text = _("Searching for a cover…")
        else:
            icon = Gtk.Image.new_from_icon_name("applications-games-symbolic")
            icon.set_pixel_size(48)
            text = (_("No cover found online") if game["game"] in self._failed
                    else _("No cover"))
        icon.set_halign(Gtk.Align.CENTER)
        label = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER,
                          max_width_chars=10)
        label.add_css_class("cover-status")
        inner.append(icon)
        inner.append(label)
        box.append(inner)
        return box

    def _game_menu(self, game):
        """The ⋮ button on a cover: picture and remove actions."""
        group = Gio.SimpleActionGroup()

        def add(name, callback):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda a, p: callback())
            group.add_action(action)

        add("choose-cover", lambda: self._choose_cover(game))
        add("remove-cover", lambda: self._remove_cover(game))
        add("find-cover", lambda: self.search_covers([game]))
        add("steamgriddb", lambda: cover_search.CoverSearch(self, game))
        add("remove", lambda: self._remove_games([game]))

        menu = Gio.Menu()
        pictures = Gio.Menu()
        pictures.append(_("Choose Picture…"), "game.choose-cover")
        if config.game_cover_path(game) is not None:
            pictures.append(_("Use Steam Cover") if _steam_appid(game)
                            else _("Remove Picture"), "game.remove-cover")
        elif self._cover_file(game) is None and self._can_download(game):
            pictures.append(_("Download Cover"), "game.find-cover")
        pictures.append(_("Search SteamGridDB…"), "game.steamgriddb")
        menu.append_section(None, pictures)
        menu.append(_("Delete Game"), "game.remove")

        button = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                                halign=Gtk.Align.END, valign=Gtk.Align.START)
        button.add_css_class("osd")
        button.add_css_class("circular")
        button.set_margin_top(6)
        button.set_margin_end(6)
        button.set_tooltip_text(_("Picture and More"))
        button.insert_action_group("game", group)
        return button

    def _game_tile(self, game, presets):
        running = game["game"] in self.running
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tile.add_css_class("card")
        tile.add_css_class("tile")
        tile.add_css_class("game-tile")
        if running:
            tile.add_css_class("running")

        overlay = Gtk.Overlay()
        overlay.set_child(self._cover(game))
        if not self.selection.active:           # a check mark is there then
            overlay.add_overlay(self._game_menu(game))
        if running:
            badge = Gtk.Label(label=_("Running"), halign=Gtk.Align.START,
                              valign=Gtk.Align.START)
            badge.add_css_class("running-badge")
            overlay.add_overlay(badge)
        tile.append(overlay)

        name = Gtk.Label(label=game["name"], xalign=0, wrap=True, lines=2,
                         hexpand=True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(8)
        name.add_css_class("heading")
        # A pencil next to the name, as at presets and folders
        rename = Gtk.Button(icon_name="document-edit-symbolic",
                            valign=Gtk.Align.CENTER,
                            tooltip_text=_("Change the name shown for this game"))
        rename.add_css_class("flat")
        rename.connect("clicked", lambda b: self._ask_rename(game))
        head = Gtk.Box(spacing=4)
        head.append(name)
        if not self.selection.active:
            head.append(rename)
        tile.append(head)

        files = [path.name for _n, path in presets]
        choose = Gtk.DropDown.new_from_strings(
            [_("No Preset")] + [n for n, _p in presets])
        choose.set_tooltip_text(_("Preset for this game"))
        if game["preset"] in files:
            choose.set_selected(files.index(game["preset"]) + 1)
        choose.connect("notify::selected", lambda d, _p: self._set_preset(
            game, files[d.get_selected() - 1] if d.get_selected() else None))
        tile.append(choose)

        auto = Gtk.CheckButton(label=_("Auto-apply"), active=game["auto"])
        auto.set_tooltip_text(_("Apply the preset as soon as the game starts"))
        auto.connect("toggled", lambda c: self._set_auto(game, c.get_active()))
        tile.append(auto)
        return tile

    def _set_preset(self, game, preset):
        log.info("Preset of %s set to %s", game["name"], preset)
        game["preset"] = preset
        self._save()

    def _set_auto(self, game, auto):
        log.info("Auto-apply for %s %s", game["name"], "on" if auto else "off")
        game["auto"] = auto
        self._save()

    def _remove_games(self, removed):
        """Put games in the bin, with their preset and picture; the message
        offers Undo, and they stay in the bin (the Trash button)."""
        items = []
        for game in removed:
            try:
                items.append(trash.put_game(game))
            except OSError as e:
                self.window._error(_("Could not delete game"), str(e))
                break
            log.info("Removed game %s (%s)", game["name"], game["game"])
        gone = set(id(g) for g in removed[:len(items)])
        if not items:
            return
        self.games = [g for g in self.games if id(g) not in gone]
        self._save()
        GLib.idle_add(lambda: self.refresh() and False)
        self.window._trash_changed()
        text = (_("“{name}” deleted").format(name=removed[0]["name"])
                if len(items) == 1 else
                _("Games deleted: {n}").format(n=len(items)))

        def undo():
            for item in items:
                self.restore(item)

        self.window._notify(text, _("Undo"), undo)

    def restore(self, item):
        """Put a game back from the bin; returns whether it worked."""
        try:
            game = trash.restore_game(item, self.games)
        except (trash.Error, OSError) as e:
            self.window._error(_("Could not restore game"), str(e))
            return False
        self.games.append(game)
        self.games.sort(key=lambda g: g["name"].lower())
        self._save()
        self.refresh()
        self.window._trash_changed()
        return True

    def _ask_rename(self, game):
        entry = Adw.EntryRow(title=_("Name"), text=game["name"],
                             activates_default=True)
        self.window._name_dialog(
            _("Rename Game"), _("Only the name shown in Understeer changes; "
                                "the game is found as before."),
            [entry], _("Rename"), lambda: self._rename(game, entry.get_text()),
            entry, _("Enter a name for the game."))
        entry.select_region(0, -1)

    def _rename(self, game, name):
        name = " ".join(name.split())
        if not name or name == game["name"]:
            return
        log.info("Game %s renamed to %r", game["game"], name)
        game["name"] = name
        self.games.sort(key=lambda g: g["name"].lower())
        self._save()
        self.refresh()

    def _add(self, game_id, name, steamgriddb_id=None):
        if any(g["game"] == game_id for g in self.games):
            return
        log.info("Added game %s (%s)%s", name, game_id,
                 f", SteamGridDB {steamgriddb_id}" if steamgriddb_id else "")
        game = {"game": game_id, "name": name, "preset": None, "auto": True}
        if steamgriddb_id:
            game["steamgriddb"] = steamgriddb_id
        self.games.append(game)
        self.games.sort(key=lambda g: g["name"].lower())
        self._save()
        self.refresh()

    # -- own pictures --------------------------------------------------------
    def _choose_cover(self, game):
        images = Gtk.FileFilter(name=_("Pictures"))
        images.add_pixbuf_formats()
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog = Gtk.FileDialog(title=_("Picture for {game}").format(game=game["name"]),
                                filters=filters, default_filter=images)
        dialog.open(self.window, None,
                    lambda d, result: self._cover_chosen(game, d, result))

    def _cover_chosen(self, game, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return                              # cancelled
        path = file.get_path() if file else None
        if path is not None:
            self.set_cover(game, path)

    def set_cover(self, game, path, quiet=False):
        """Use the picture at path for game; returns whether it worked."""
        try:
            Gdk.Texture.new_from_filename(path)
        except GLib.Error as e:
            log.warning("Picture %s for %s can't be shown: %s", path,
                        game["name"], e.message)
            if not quiet:
                self.window._notify(_("This picture can't be opened"))
            return False
        try:
            config.set_game_cover(game, path)
        except OSError as e:
            log.warning("Could not copy picture %s: %s", path, e)
            if not quiet:
                self.window._notify(_("Could not save the picture"))
            return False
        log.info("Picture for %s set from %s", game["name"], path)
        self._save()
        self.refresh()
        return True

    def _remove_cover(self, game):
        log.info("Picture for %s removed", game["name"])
        config.remove_game_cover(game)
        self._save()
        self.refresh()

    # -- downloaded covers ---------------------------------------------------
    @staticmethod
    def _can_download(game):
        """Steam games always; others only through SteamGridDB."""
        return bool(_steam_appid(game) or config.get_setting(cover_search.KEY_SETTING))

    def search_covers(self, which=None):
        """Download missing covers and report the result.

        Started by the user (switch, refresh button or tile menu), so it
        always says what happens, also when there is nothing to search.
        """
        which = self.games if which is None else which
        missing = [g for g in which if self._cover_file(g) is None]
        possible = [g for g in missing if self._can_download(g)]
        if not missing:
            self.window._notify(_("All games already have a cover"))
            return
        if not possible:
            self.window._notify(_("Covers of games not on Steam come from "
                                  "SteamGridDB. Enter an API key under "
                                  "Covers to search them."))
            return
        ids = {g["game"] for g in possible}
        self._failed.difference_update(ids)
        self._search = {"asked": ids, "found": 0, "missed": [],
                        "skipped": len(missing) - len(possible), "key_error": False}
        log.info("Searching covers of %s", ", ".join(sorted(ids)))
        self.window._notify(_("Searching covers for {n} games…").format(n=len(ids))
                            if len(ids) > 1 else _("Searching for a cover…"))
        for game in possible:
            self._download(game)
        self.refresh()                          # shows the spinners

    def _download(self, game):
        """Fetch a missing cover: Steam's own, else SteamGridDB's."""
        game_id = game["game"]
        if game_id in self._downloading or game_id in self._failed:
            return
        self._downloading.add(game_id)
        appid = _steam_appid(game)
        key = config.get_setting(cover_search.KEY_SETTING)

        def work():
            if appid:
                path = games.download_cover(appid, config.COVER_DIR)
                if path is not None:
                    return "steam", path, None
            if not key:
                return None, None, None
            try:
                url = steamgriddb.find_cover(game["name"], key, steam_appid=appid,
                                             game_id=game.get("steamgriddb"))
                if url is None:
                    return None, None, None
                suffix = pathlib.PurePosixPath(url).suffix.lower() or ".img"
                tmp = config.COVER_DIR / f"steamgriddb-{abs(hash(game_id))}{suffix}"
                return "steamgriddb", steamgriddb.download(url, tmp), None
            except steamgriddb.Error as e:
                return None, None, e

        def done(result):
            source, path, error = result
            self._downloading.discard(game_id)
            if error is not None:
                log.warning("SteamGridDB cover for %s: %s", game["name"], error)
            if source == "steamgriddb":
                if game in self.games and self._cover_file(game) is None:
                    self.set_cover(game, str(path), quiet=True)
                try:
                    path.unlink()
                except OSError:
                    pass
            found = source is not None and self._cover_file(game) is not None
            if found:
                log.info("Downloaded cover for %s from %s", game["name"], source)
            else:
                log.info("No cover online for %s", game["name"])
                self._failed.add(game_id)
            self._report_search(game_id, found, error)
            self.refresh()
            return False

        threading.Thread(target=lambda: GLib.idle_add(done, work()),
                         daemon=True).start()

    def _report_search(self, game_id, found, error=None):
        search = self._search
        if search is None or game_id not in search["asked"]:
            return
        if found:
            search["found"] += 1
        else:
            search["missed"].append(game_id)
        if error is not None and error.kind == "key":
            search["key_error"] = True
        if search["asked"] & self._downloading:
            return                              # still waiting for others
        self._search = None
        found_n, total = search["found"], len(search["asked"])
        missed = [g for g in self.games if g["game"] in search["missed"]]
        button = action = None
        if search["key_error"]:
            self.window._notify(cover_search.error_text(steamgriddb.Error("key", "")))
            return
        if found_n == total:
            parts = [_("Found all {n} covers").format(n=total) if total > 1
                     else _("Cover found")]
        else:
            parts = [_("No cover found") if total == 1 else
                     _("No covers found") if found_n == 0 else
                     _("Found {found} of {n} covers").format(found=found_n, n=total)]
        if search["skipped"]:
            parts.append(_("Games not on Steam need a SteamGridDB API key."))
        if missed and config.get_setting(cover_search.KEY_SETTING):
            # Names that didn't match exactly: let the user pick them
            parts.append(_("Choose the others on SteamGridDB?") if found_n
                         else _("Choose on SteamGridDB?"))
            button = _("Choose")
            action = lambda: self.choose_covers(missed)   # noqa: E731
        text = " ".join(p if p[-1] in ".?" else p + "." for p in parts)
        self.window._notify(text, button, action)

    def choose_covers(self, chosen):
        """Pick covers on SteamGridDB for these games, one after another."""
        chosen = [g for g in chosen if g in self.games]
        if chosen:
            log.info("Choosing covers for %s", ", ".join(g["name"] for g in chosen))
            cover_search.CoverSearch(self, chosen[0], chosen[1:])

    # -- adding games --------------------------------------------------------
    def _ask_game(self):
        added = {g["game"] for g in self.games}
        installed = [(appid, name) for appid, name in games.installed_games()
                     if games.steam_id(appid) not in added]

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_size_request(420, -1)
        search = Gtk.SearchEntry(placeholder_text=_("Search games"))
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        for appid, name in installed:
            row = Adw.ActionRow(title=name, subtitle=f"Steam · {appid}",
                                activatable=True)
            row.game = (games.steam_id(appid), name)
            listbox.append(row)
        if not installed:
            listbox.append(Adw.ActionRow(
                title=_("No installed Steam games found"),
                subtitle=_("Add other games by their program.")))
        search.connect("search-changed", lambda e: listbox.invalidate_filter())

        def matches(row):
            text = search.get_text().strip().lower()
            return not hasattr(row, "game") or text in row.game[1].lower()
        listbox.set_filter_func(matches)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    min_content_height=320)
        scroll.set_child(listbox)
        box.append(search)
        box.append(scroll)

        dialog = Adw.AlertDialog(heading=_("Add Game"))
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("other", _("Other Program…"))

        def on_row(lb, row):
            if hasattr(row, "game"):
                self._add(*row.game)
                dialog.close()

        listbox.connect("row-activated", on_row)
        dialog.connect("response", lambda d, r: r == "other" and self._ask_program())
        dialog.present(self.window)

    def _name_suggestions(self, name_row):
        """Names from SteamGridDB under the name field, while typing.

        Choosing one remembers the SteamGridDB game, so its cover is always
        found, also after renaming. Returns the box shown under the field;
        its `chosen` is the chosen game's id or None.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      visible=False)
        box.chosen = None
        box.closed = False
        label = Gtk.Label(label=_("Suggestions from SteamGridDB"), xalign=0)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        box.append(label)
        box.append(listbox)
        linked = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        linked.set_tooltip_text(_("Linked with SteamGridDB: its cover is found "
                                  "automatically"))
        linked.set_visible(False)
        name_row.add_suffix(linked)
        state = {"timer": 0, "search": 0, "setting": False}
        key = config.get_setting(cover_search.KEY_SETTING)
        if not key:
            return box

        def choose(row):
            state["setting"] = True
            name_row.set_text(row.found["name"])
            state["setting"] = False
            box.chosen = row.found["id"]
            linked.set_visible(True)
            box.set_visible(False)

        def search():
            state["timer"] = 0
            term = name_row.get_text().strip()
            state["search"] += 1
            search_id = state["search"]
            if len(term) < 2:
                box.set_visible(False)
                return False

            def done(found, error):
                if box.closed or search_id != state["search"]:
                    return
                if error is not None:
                    log.info("SteamGridDB suggestions: %s", error)
                    return
                listbox.remove_all()
                for g in found[:5]:
                    row = Adw.ActionRow(title=g["name"], activatable=True,
                                        subtitle=str(g["year"]) if g["year"] else "")
                    row.set_use_markup(False)
                    row.found = g
                    listbox.append(row)
                box.set_visible(bool(found))

            cover_search.in_thread(lambda: steamgriddb.search(term, key), done)
            return False

        def changed(_row):
            if state["setting"]:
                return
            box.chosen = None                   # typed by hand again
            linked.set_visible(False)
            if state["timer"]:
                GLib.source_remove(state["timer"])
            state["timer"] = GLib.timeout_add(500, search)

        name_row.connect("changed", changed)
        listbox.connect("row-activated", lambda lb, row: choose(row))
        return box

    def _ask_program(self):
        """Add a game not started by Steam, recognised by its program."""
        fields = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        fields.add_css_class("boxed-list")
        name = Adw.EntryRow(title=_("Name"))
        program = Adw.EntryRow(title=_("Program"))
        pick = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER)
        pick.add_css_class("flat")
        pick.set_tooltip_text(_("Choose Program File…"))
        program.add_suffix(pick)
        fields.append(name)
        fields.append(program)
        suggestions = self._name_suggestions(name)

        hint = Gtk.Label(label=_("Is the game running? Then pick it here:"),
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        hint.set_margin_top(10)
        search = Gtk.SearchEntry(placeholder_text=_("Search programs"),
                                 hexpand=True)
        reload_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        reload_btn.set_tooltip_text(_("Update List"))
        search_box = Gtk.Box(spacing=6)
        search_box.append(search)
        search_box.append(reload_btn)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    min_content_height=200)
        scroll.set_child(listbox)

        def fill_list():
            listbox.remove_all()
            for prog, cmdline in games.running_programs():
                row = Adw.ActionRow(title=prog, subtitle=cmdline, activatable=True,
                                    title_lines=1, subtitle_lines=1)
                row.set_use_markup(False)
                row.set_tooltip_text(cmdline)
                row.program = prog
                listbox.append(row)

        def matches(row):
            text = search.get_text().strip().lower()
            return text in row.program.lower() or text in row.get_subtitle().lower()

        listbox.set_filter_func(matches)
        search.connect("search-changed", lambda e: listbox.invalidate_filter())
        reload_btn.connect("clicked", lambda b: fill_list())
        fill_list()

        def use(file_name):
            program.set_text(file_name)
            if not name.get_text().strip():
                name.set_text(pathlib.Path(file_name).stem
                              if file_name.lower().endswith(".exe") else file_name)

        listbox.connect("row-activated", lambda lb, row: use(row.program))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_size_request(420, -1)
        for w in (fields, suggestions, hint, search_box, scroll):
            box.append(w)
        dialog = Adw.AlertDialog(
            heading=_("Add Other Program"),
            body=_("For games not started by Steam. Understeer recognises them "
                   "by their program file. For Windows games (Wine, Proton, "
                   "Lutris, Heroic) that is the game's .exe, not the launcher."))
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add"))
        dialog.set_default_response("add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("add", False)
        program.connect("changed", lambda e: dialog.set_response_enabled(
            "add", bool(games.program_key(e.get_text()))))

        def choose_file(_button):
            def done(d, result):
                try:
                    file = d.open_finish(result)
                except GLib.Error:
                    return
                if file is not None and file.get_basename():
                    use(file.get_basename())
            # The dialog is no window of its own; the file chooser belongs
            # to the main window.
            Gtk.FileDialog(title=_("Program File")).open(self.window, None, done)

        pick.connect("clicked", choose_file)

        def on_response(d, response):
            key = games.program_key(program.get_text())
            suggestions.closed = True
            if response == "add" and key:
                self._add(f"process:{key}", name.get_text().strip() or key,
                          suggestions.chosen)

        dialog.connect("response", on_response)
        dialog.present(self.window)
