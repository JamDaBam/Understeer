"""Application entry point."""
import sys

from . import DATA_DIR
from .config import migrate_config
from .ui.window import TunerWindow  # also selects the GTK version

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

ICON_DIR = DATA_DIR / "icons"


class TunerApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.JamDaBam.Understeer",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.get_active_window()
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        if ICON_DIR.is_dir():
            theme.add_search_path(str(ICON_DIR))
        if not win:
            win = TunerWindow(application=self)
        win.present()


def main():
    migrate_config()
    return TunerApplication().run(sys.argv)
