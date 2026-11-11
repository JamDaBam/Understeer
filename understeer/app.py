"""Application entry point."""
import pathlib
import sys

from . import DATA_DIR, background, config, games, i18n, log, releases, updates
from .ui.window import TunerWindow  # also selects the GTK version
from .i18n import _
from .ui import theme
from .ui.tray import Tray

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

# Our own symbolic icons. They must sit in an icon theme folder
# (icons/hicolor/scalable/apps); loose files are not recoloured by GTK.
ICON_DIR = DATA_DIR / "icons"
STYLE = pathlib.Path(__file__).resolve().parent / "ui" / "style.css"
APP_ID = background.APP_ID
APP_ICON = DATA_DIR / "io.github.JamDaBam.Understeer.png"    # for the tray


class TunerApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        # Which games run; kept here so a rebuilt window doesn't apply
        # their presets again.
        self.game_watch = games.StartWatch()
        # Before this start saves anything: was Understeer used here before?
        # Then a first start of a new version counts as an update.
        self.used_before = releases.was_used(config.CONFIG_DIR)
        self.start_hidden = False   # --background: no window, only the tray
        self.tray = Tray(APP_ICON, self._tray_texts, self._tray_open,
                         self.quit_all)
        self.add_main_option(
            "background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Start without a window, in the background (used at login)", None)

    def do_handle_local_options(self, options):
        if options.contains("background"):
            self.start_hidden = True
            try:
                self.register(None)
            except GLib.Error:
                return -1
            if self.get_is_remote():
                return 0        # already running: leave it as it is
        return -1               # go on as usual

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        if ICON_DIR.is_dir():
            Gtk.IconTheme.get_for_display(display).add_search_path(str(ICON_DIR))
        css = Gtk.CssProvider()
        css.load_from_path(str(STYLE))
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        theme.apply()           # colour scheme, theme, accent and corners
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *a: self.quit_all())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        background.refresh_autostart()      # e.g. the AppImage moved
        self.update_tray()

    def do_activate(self):
        windows = self.get_windows()
        if windows:
            windows[0].show_again()
            return
        win = TunerWindow(application=self)
        if self.start_hidden and self.runs_in_background():
            self.start_hidden = False
            log.log.info("Started in the background")
            win.go_to_background(announce=False)
        else:
            win.present()

    # -- background ----------------------------------------------------------
    @staticmethod
    def runs_in_background():
        return bool(config.get_setting("background"))

    def update_tray(self):
        """The tray icon is shown exactly while running in the background
        is switched on, so the user always sees that Understeer runs."""
        if self.runs_in_background():
            self.tray.start()
        else:
            self.tray.stop()

    def _tray_texts(self):
        return {"title": "Understeer",
                "tooltip": _("Applies the presets of your games"),
                "open": _("Open Understeer"), "quit": _("Quit")}

    def _tray_open(self, token):
        windows = self.get_windows()
        if windows:
            windows[0].show_again(token)
        else:
            self.activate()

    def quit_all(self, restart=None):
        """Quit for real, also when running in the background; first
        offers to save unsaved changes of the active preset. restart: an
        AppImage to start once this one has ended, after an update."""
        def quit_now():
            if restart:
                log.log.info("Restarting %s", restart)
                updates.restart(restart)
            self._quit_now()

        windows = [w for w in self.get_windows() if hasattr(w, "confirm_quit")]
        if windows:
            windows[0].confirm_quit(quit_now)
        else:
            quit_now()

    def _quit_now(self):
        for win in self.get_windows():
            if hasattr(win, "shut_down"):
                win.shut_down()
        self.tray.stop()
        self.quit()


def main():
    # Started as "python3 -m understeer", GLib would call the program
    # "__main__.py", and the desktop shows that in notifications.
    GLib.set_prgname(APP_ID)
    GLib.set_application_name("Understeer")
    log.setup()
    config.migrate_config()
    i18n.set_language(config.get_setting("language"))
    log.log.info("GTK %s.%s.%s, Libadwaita %s.%s.%s, language %s, layout %s",
                 Gtk.get_major_version(), Gtk.get_minor_version(),
                 Gtk.get_micro_version(), Adw.get_major_version(),
                 Adw.get_minor_version(), Adw.get_micro_version(),
                 config.get_setting("language") or "system",
                 config.get_setting("layout") or "full")
    return TunerApplication().run(sys.argv)
