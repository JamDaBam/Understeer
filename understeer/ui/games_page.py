"""The Games page: one tile per game with its cover and preset."""
import logging
import threading

from gi.repository import Adw, GLib, Gtk

from .. import config, games
from ..i18n import _
from .live import CoverView

log = logging.getLogger("understeer.games")


class GamesPage:
    """Builds and updates the Games page; `widget` is the page itself.

    Running games are reported by the window through set_running().
    """

    def __init__(self, window):
        self.window = window
        self.games = config.load_games()
        self.running = set()
        roots = games.steam_roots()
        log.info("Steam installations: %s", ", ".join(map(str, roots)) or "none")
        for root in roots:
            log.info("Steam libraries in %s: %s", root,
                     ", ".join(map(str, games.library_folders(root))))
        log.info("%d games set up, cover downloads %s", len(self.games),
                 "on" if config.get_setting("download_covers", False) else "off")
        self._downloading = set()   # steam app ids being fetched
        self._failed = set()        # ... or that had no cover online

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        column.add_css_class("games")
        for side in ("top", "bottom", "start", "end"):
            getattr(column, f"set_margin_{side}")(12)
        title = Gtk.Label(label=_("Games"), xalign=0)
        title.add_css_class("section-title")
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

        options = Adw.PreferencesGroup()
        options.set_margin_top(18)
        download = Adw.SwitchRow(
            title=_("Download Missing Covers"),
            subtitle=_("Loads covers from Steam's servers when Steam has none "
                       "stored. When off, Understeer uses only the pictures "
                       "Steam already keeps on this computer."))
        download.set_active(bool(config.get_setting("download_covers", False)))
        download.connect("notify::active", self._on_download_toggled)
        options.add(download)

        for w in (title, desc, self.flow, options):
            column.append(w)
        clamp = Adw.Clamp(maximum_size=1200, tightening_threshold=900)
        clamp.set_child(column)
        self.widget = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         vexpand=True)
        self.widget.set_child(clamp)
        self.refresh()

    # -- state ---------------------------------------------------------------
    def set_running(self, running):
        """Ids of running games, see games.running_games()."""
        shown = {g["game"] for g in self.games}
        if (running & shown) != (self.running & shown):
            self.running = running
            self.refresh()
        else:
            self.running = running

    def _save(self):
        config.save_games(self.games)

    def _on_download_toggled(self, row, _param):
        config.set_setting("download_covers", row.get_active() or None)
        self._failed.clear()
        self.refresh()

    # -- tiles ---------------------------------------------------------------
    def refresh(self):
        self.flow.remove_all()
        self.flow.append(self._add_tile())
        presets = config.list_presets()
        for game in self.games:
            self.flow.append(self._game_tile(game, presets))

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

    def _cover(self, game):
        appid = game["game"].split(":", 1)[1] if game["game"].startswith("steam:") else None
        path = games.find_cover(appid, config.COVER_DIR) if appid else None
        if path is None and appid and config.get_setting("download_covers", False):
            self._download(appid)
        if path is not None:
            child = Gtk.Picture.new_for_filename(str(path))
            child.set_content_fit(Gtk.ContentFit.COVER)
            child.set_can_shrink(True)
        else:
            child = Gtk.Image.new_from_icon_name("applications-games-symbolic")
            child.set_pixel_size(48)
            child.add_css_class("cover-placeholder")
        cover = CoverView(child)                # portrait, like Steam's library
        cover.add_css_class("game-cover")
        return cover

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
        remove = Gtk.Button(icon_name="user-trash-symbolic",
                            halign=Gtk.Align.END, valign=Gtk.Align.START)
        remove.add_css_class("osd")
        remove.add_css_class("circular")
        remove.set_margin_top(6)
        remove.set_margin_end(6)
        remove.set_tooltip_text(_("Remove Game"))
        remove.connect("clicked", lambda b: self._remove(game))
        overlay.add_overlay(remove)
        if running:
            badge = Gtk.Label(label=_("Running"), halign=Gtk.Align.START,
                              valign=Gtk.Align.START)
            badge.add_css_class("running-badge")
            overlay.add_overlay(badge)
        tile.append(overlay)

        name = Gtk.Label(label=game["name"], xalign=0, wrap=True, lines=2)
        name.set_ellipsize(3)                   # Pango.EllipsizeMode.END
        name.set_max_width_chars(8)
        name.add_css_class("heading")
        tile.append(name)

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

    def _remove(self, game):
        log.info("Removed game %s (%s)", game["name"], game["game"])
        self.games = [g for g in self.games if g is not game]
        self._save()
        GLib.idle_add(lambda: self.refresh() and False)

    def _add(self, game_id, name):
        if any(g["game"] == game_id for g in self.games):
            return
        log.info("Added game %s (%s)", name, game_id)
        self.games.append({"game": game_id, "name": name, "preset": None,
                           "auto": True})
        self.games.sort(key=lambda g: g["name"].lower())
        self._save()
        self.refresh()

    # -- covers --------------------------------------------------------------
    def _download(self, appid):
        if appid in self._downloading or appid in self._failed:
            return
        self._downloading.add(appid)

        def work():
            path = games.download_cover(appid, config.COVER_DIR)
            GLib.idle_add(done, path)

        def done(path):
            self._downloading.discard(appid)
            if path is None:
                log.info("No cover online for Steam app %s", appid)
                self._failed.add(appid)
            else:
                log.info("Downloaded cover for Steam app %s", appid)
                self.refresh()
            return False

        threading.Thread(target=work, daemon=True).start()

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
                subtitle=_("Add other games by their program name.")))
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

        dialog = Adw.MessageDialog(transient_for=self.window,
                                   heading=_("Add Game"))
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("other", _("Other Program…"))

        def on_row(lb, row):
            if hasattr(row, "game"):
                self._add(*row.game)
                dialog.close()

        listbox.connect("row-activated", on_row)
        dialog.connect("response", lambda d, r: r == "other" and self._ask_program())
        dialog.present()

    def _ask_program(self):
        name = Gtk.Entry(placeholder_text=_("Name, e.g. BeamNG.drive"))
        program = Gtk.Entry(placeholder_text=_("Program, e.g. BeamNG.drive.x64"),
                            activates_default=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(name)
        box.append(program)
        dialog = Adw.MessageDialog(
            transient_for=self.window, heading=_("Add Other Program"),
            body=_("For games not started by Steam. Enter the name of the "
                   "program file; “.exe” is optional."))
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add"))
        dialog.set_default_response("add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            key = games.program_key(program.get_text())
            if response == "add" and key:
                self._add(f"process:{key}", name.get_text().strip() or key)

        dialog.connect("response", on_response)
        dialog.present()
