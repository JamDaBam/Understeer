"""Application entry point."""
import pathlib
import sys

from . import DATA_DIR, config, i18n, log
from .ui.window import TunerWindow  # also selects the GTK version

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

# Our own symbolic icons. They must sit in an icon theme folder
# (icons/hicolor/scalable/apps); loose files are not recoloured by GTK.
ICON_DIR = DATA_DIR / "icons"
STYLE = pathlib.Path(__file__).resolve().parent / "ui" / "style.css"


class TunerApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.JamDaBam.Understeer",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        if ICON_DIR.is_dir():
            Gtk.IconTheme.get_for_display(display).add_search_path(str(ICON_DIR))
        css = Gtk.CssProvider()
        css.load_from_path(str(STYLE))
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        win = self.get_active_window() or TunerWindow(application=self)
        win.present()

    def rebuild_window(self, page=None):
        """Replace the window, e.g. after the language changed."""
        old = self.get_active_window()
        win = TunerWindow(application=self)
        if old is not None:
            win.set_default_size(old.get_width(), old.get_height())
            if old.is_maximized():
                win.maximize()
        if page:
            win._select_page(page)
        win.present()
        if old is not None:
            old.close()


def main():
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
