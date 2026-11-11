"""Choosing files and showing them in the file manager, the same way
everywhere."""
from gi.repository import Gio, GLib, Gtk


def file_dialog(title, kind, patterns=(), mime_types=(), pictures=False, **props):
    """A Gtk.FileDialog that offers one kind of file. kind: what it is
    called, e.g. "Presets (JSON or text)"; pictures: every picture format
    GTK can show. props go to the dialog, e.g. initial_name."""
    wanted = Gtk.FileFilter(name=kind)
    for pattern in patterns:
        wanted.add_pattern(pattern)
    for mime_type in mime_types:
        wanted.add_mime_type(mime_type)
    if pictures:
        wanted.add_pixbuf_formats()
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(wanted)
    return Gtk.FileDialog(title=title, filters=filters, default_filter=wanted,
                          **props)


def chosen_path(finish, result):
    """The path chosen in a file dialog, None if it was cancelled. finish:
    the dialog's open_finish or save_finish."""
    try:
        file = finish(result)
    except GLib.Error:
        return None
    return file.get_path() if file else None


def show_in_folder(parent, path):
    """Open the folder of the file at path in the file manager."""
    Gtk.FileLauncher.new(Gio.File.new_for_path(str(path))).open_containing_folder(
        parent, None, None)
