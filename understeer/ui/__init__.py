"""GTK 4 / Libadwaita user interface."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")


def toolkit_versions():
    """{"GTK": "4.18.6", "Libadwaita": "1.7.6"}: the libraries in use."""
    from gi.repository import Adw, Gtk

    def version(lib):
        return f"{lib.get_major_version()}.{lib.get_minor_version()}." \
               f"{lib.get_micro_version()}"
    return {"GTK": version(Gtk), "Libadwaita": version(Adw)}
