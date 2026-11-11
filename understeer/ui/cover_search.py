"""Choosing a game's cover from SteamGridDB: game first, then the picture."""
import logging
import pathlib
import threading

from gi.repository import Adw, Gdk, GLib, Gtk

from .. import config, steamgriddb
from ..i18n import _
from . import tasks
from .layout import margins, tile_grid
from .live import CoverView
from .names import cap_row, short

log = logging.getLogger("understeer.steamgriddb")

KEY_SETTING = "steamgriddb_key"
MAX_COVERS = 36         # more is hard to choose from
THUMB_THREADS = 4


def saved_key():
    """The user's SteamGridDB API key, "" if none is saved."""
    return config.get_text(KEY_SETTING) or ""


def in_thread(work, done):
    """Run a SteamGridDB request in a thread, see tasks.in_thread()."""
    tasks.in_thread(work, done, steamgriddb.Error)


def error_text(error):
    kind = getattr(error, "kind", "network")   # an unexpected error: like network
    if kind == "key":
        return _("SteamGridDB did not accept the API key.")
    if kind == "save":
        return _("Could not save the picture")
    return _("SteamGridDB could not be reached. Check your internet connection.")


def check_key(key, done):
    """Try key with a small search; done(error or None) on the main loop."""
    in_thread(lambda: steamgriddb.search("Portal", key),
              lambda _result, error: done(error))


def open_key_page(parent):
    Gtk.UriLauncher.new(steamgriddb.KEY_URL).launch(parent, None, None)


class KeyRow:
    """Entry row for the SteamGridDB API key, without an apply button.

    What is entered counts at once: it is saved shortly after typing
    stops, on Enter, and when flush() is called (e.g. as its dialog
    closes). Then SteamGridDB is asked whether it takes the key; a refused
    key stays saved, but the row turns red and notify() says why.
    """
    DELAY_MS = 800

    def __init__(self, notify, accepted=None):
        """notify(text) shows a message; accepted() runs after SteamGridDB
        took a newly saved key."""
        self.notify = notify
        self.accepted = accepted
        self.row = Adw.PasswordEntryRow(title=_("SteamGridDB API Key"))
        self._timer = 0
        self._check_id = 0          # ignores answers about an older key
        self._loading = False
        self.show_saved()
        self.row.connect("changed", self._changed)
        self.row.connect("entry-activated", lambda r: self.flush())

    def show_saved(self):
        """Show the saved key, e.g. after the other key row saved one."""
        self._stop_timer()
        self._loading = True
        self.row.set_text(saved_key())
        self._loading = False
        self.row.remove_css_class("error")

    def _stop_timer(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _changed(self, _row):
        if self._loading:
            return
        self.row.remove_css_class("error")
        self._stop_timer()
        self._timer = GLib.timeout_add(self.DELAY_MS, self._timeout)

    def _timeout(self):
        self._timer = 0
        self.flush()
        return False

    def flush(self):
        """Save the entered key now, if it differs from the saved one."""
        self._stop_timer()
        key = self.row.get_text().strip()
        if key == saved_key():
            return
        self._check_id += 1
        if not key:
            config.set_setting(KEY_SETTING, None)
            log.info("SteamGridDB API key removed")
            self.notify(_("SteamGridDB API key removed"))
            return
        config.set_setting(KEY_SETTING, key)
        log.info("SteamGridDB API key saved")
        check_id = self._check_id

        def done(error):
            if check_id != self._check_id:
                return                  # changed again in the meantime
            if error is None:
                self.notify(_("SteamGridDB API key saved"))
                if self.accepted:
                    self.accepted()
                return
            log.warning("SteamGridDB API key check failed: %s", error)
            if getattr(error, "kind", None) == "key":
                self.row.add_css_class("error")
            self.notify(error_text(error))
        check_key(key, done)


class CoverSearch:
    """Dialog to search a game on SteamGridDB and pick one of its covers.

    With more games (`more`), it goes through them one after another; each
    can be skipped.
    """

    def __init__(self, games_page, game, more=()):
        self.page = games_page
        self.queue = [game, *more]
        self.index = 0
        self.game = game
        self._search_id = 0         # ignores answers of older searches
        self._closed = False
        self.key_row = None
        self.dialog = Adw.Dialog(title=_("Cover from SteamGridDB"))
        self.dialog.connect("closed", self._on_close)
        self.nav = Adw.NavigationView()
        self.dialog.set_child(self.nav)
        # replace, not add: added pages stay in the view for good, and the
        # next page with the same tag (e.g. after a refused key) clashes.
        if saved_key():
            self.nav.replace([self._search_page()])
        else:
            self.nav.replace([self._key_page()])
        games_page.window.fit_dialog(self.dialog)
        self.dialog.present(games_page.window)

    def _on_close(self, _dialog):
        self._closed = True
        if self.key_row is not None:
            self.key_row.flush()        # a key entered just before closing

    def _progress(self):
        """Subtitle: the game, and where we are when going through several."""
        if len(self.queue) < 2:
            return self.game["name"]
        return _("{game} · {i} of {n}").format(game=short(self.game["name"]),
                                               i=self.index + 1, n=len(self.queue))

    def _page(self, title, content, tag=None, subtitle=None):
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        if subtitle:
            header.set_title_widget(Adw.WindowTitle(title=title, subtitle=subtitle))
        if len(self.queue) > 1:
            skip = Gtk.Button(label=_("Skip"))
            skip.set_tooltip_text(_("Leave this game without a cover"))
            skip.connect("clicked", lambda b: self._next())
            header.pack_end(skip)
        view.add_top_bar(header)
        view.set_content(content)
        return Adw.NavigationPage(title=title, child=view, tag=tag or title)

    def _next(self):
        """Go on with the next game, or close after the last."""
        self.index += 1
        if self.index >= len(self.queue):
            self.dialog.close()
            return
        self.game = self.queue[self.index]
        self.nav.replace([self._search_page()])

    # -- API key -------------------------------------------------------------
    def _key_page(self):
        status = Adw.StatusPage(
            icon_name="dialog-password-symbolic", title=_("API Key Needed"),
            description=_("SteamGridDB answers only with a personal API key. "
                          "It is free: sign in on SteamGridDB, open your "
                          "preferences and copy the key from the API "
                          "section."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        get = Gtk.Button(label=_("Get API Key"), halign=Gtk.Align.CENTER)
        get.add_css_class("pill")
        get.connect("clicked", lambda b: open_key_page(self.page.window))
        problem = Gtk.Label(wrap=True, visible=False)
        problem.add_css_class("error")

        def notify(text):
            if self._closed:
                self.page.window._notify(text)
            else:
                problem.set_label(text)
                problem.set_visible(True)

        def accepted():
            self.page.key_changed()
            if not self._closed:
                self.key_row = None
                self.nav.replace([self._search_page()])

        self.key_row = KeyRow(notify, accepted)
        self.key_row.row.connect("changed", lambda r: problem.set_visible(False))
        group = Adw.PreferencesGroup()
        group.add(self.key_row.row)
        box.append(get)
        box.append(group)
        box.append(problem)
        clamp = Adw.Clamp(maximum_size=420, child=box)
        status.set_child(clamp)
        return self._page("SteamGridDB", status, "key")

    # -- games ---------------------------------------------------------------
    def _search_page(self):
        box = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12), 12)
        entry = Gtk.SearchEntry(placeholder_text=_("Game name"), search_delay=500)
        entry.set_text(self.game["name"])
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                   valign=Gtk.Align.START)
        self.results.add_css_class("boxed-list")
        self.results.connect("row-activated",
                             lambda lb, row: self.nav.push(self._covers_page(row.found)))
        self.search_stack = Gtk.Stack(vexpand=True)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.results)
        self.search_stack.add_named(scroll, "results")
        self.search_stack.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER,
                                                valign=Gtk.Align.CENTER,
                                                width_request=32,
                                                height_request=32), "busy")
        self.search_status = Adw.StatusPage(icon_name="system-search-symbolic")
        self.search_status.add_css_class("compact")
        self.search_stack.add_named(self.search_status, "status")
        box.append(entry)
        box.append(self.search_stack)
        entry.connect("search-changed", lambda e: self._search(e.get_text()))
        entry.connect("activate", lambda e: self._search(e.get_text()))
        GLib.idle_add(lambda: self._search(entry.get_text()) and False)
        return self._page(_("Choose the Game"), box, f"search-{self.index}",
                          self._progress())

    def _show_status(self, stack, status, title, description=""):
        status.set_title(title)
        status.set_description(description)
        stack.set_visible_child_name("status")

    def _search(self, term):
        self._search_id += 1
        search_id = self._search_id
        if not term.strip():
            self._show_status(self.search_stack, self.search_status,
                              _("Enter the name of the game"))
            return
        self.search_stack.set_visible_child_name("busy")
        key = saved_key()

        def done(found, error):
            if self._closed or search_id != self._search_id:
                return
            if error is not None:
                log.warning("SteamGridDB search failed: %s", error)
                self._show_status(self.search_stack, self.search_status,
                                  _("Search failed"), error_text(error))
                if getattr(error, "kind", None) == "key":
                    self.nav.replace([self._key_page()])
                return
            self.results.remove_all()
            for g in found:
                row = cap_row(Adw.ActionRow(
                    title=g["name"], activatable=True,
                    subtitle=str(g["year"]) if g["year"] else ""))
                row.set_use_markup(False)
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                row.found = g
                self.results.append(row)
            if found:
                self.search_stack.set_visible_child_name("results")
            else:
                self._show_status(self.search_stack, self.search_status,
                                  _("No games found"),
                                  _("Try a shorter or different name."))

        in_thread(lambda: steamgriddb.search(term, key), done)

    # -- covers --------------------------------------------------------------
    def _covers_page(self, found):
        stack = Gtk.Stack()
        stack.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER,
                                    valign=Gtk.Align.CENTER, width_request=32,
                                    height_request=32), "busy")
        status = Adw.StatusPage(icon_name="image-missing-symbolic")
        stack.add_named(status, "status")
        flow = margins(tile_grid(2, 4), 12)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(flow)
        stack.add_named(scroll, "covers")
        stack.set_visible_child_name("busy")
        title = found["name"] + (f" ({found['year']})" if found["year"] else "")
        page = self._page(title, stack, f"covers-{found['id']}", self._progress())
        key = saved_key()

        def done(covers, error):
            if self._closed:
                return
            if error is not None:
                log.warning("Loading SteamGridDB covers failed: %s", error)
                self._show_status(stack, status, _("Covers could not be loaded"),
                                  error_text(error))
                return
            if not covers:
                self._show_status(stack, status, _("No covers"),
                                  _("SteamGridDB has no portrait covers for "
                                    "this game yet."))
                return
            limit = threading.Semaphore(THUMB_THREADS)
            for cover in covers[:MAX_COVERS]:
                flow.append(self._cover_button(cover, stack, limit, found))
            stack.set_visible_child_name("covers")

        in_thread(lambda: steamgriddb.covers(found["id"], key), done)
        return page

    def _cover_button(self, cover, stack, limit, found):
        picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        view = CoverView(picture, natural_width=150)
        view.add_css_class("game-cover")
        button = Gtk.Button(child=view)
        button.add_css_class("flat")
        if cover["author"]:
            button.set_tooltip_text(_("By {author}").format(author=cover["author"]))
        button.connect("clicked", lambda b: self._choose(cover, stack, found))

        def load():
            with limit:
                return steamgriddb.fetch(cover["thumb"])

        def shown(data, error):
            if self._closed or error is not None:
                return
            try:
                picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
            except GLib.Error:
                button.set_visible(False)       # not a picture GTK can show

        in_thread(load, shown)
        return button

    def _choose(self, cover, stack, found):
        stack.set_visible_child_name("busy")
        suffix = pathlib.PurePosixPath(cover["url"]).suffix.lower() or ".img"
        tmp = config.COVER_DIR / f"steamgriddb-download{suffix}"

        game = self.game

        def done(path, error):
            if self._closed:
                return
            if error is not None:
                log.warning("Downloading %s failed: %s", cover["url"], error)
                stack.set_visible_child_name("covers")
                self.page.window._notify(error_text(error))
                return
            game["steamgriddb"] = found["id"]     # finds it again by itself
            ok = self.page.set_cover(game, str(path))
            try:
                path.unlink()
            except OSError:
                pass
            if ok:
                log.info("SteamGridDB cover %s chosen for %s", cover["url"],
                         game["name"])
                self.page.window._notify(_("Cover from SteamGridDB set"))
                self._next()
            else:
                stack.set_visible_child_name("covers")

        in_thread(lambda: steamgriddb.download(cover["url"], tmp), done)
