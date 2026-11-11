"""Long names in widgets: labels end in "…" once they run out of room and
ask for little, so a long name doesn't make every tile as wide. Text with
names is cut by short() and join_names() (from understeer.names)."""
from gi.repository import Gtk, Pango

from ..names import join_names, short

__all__ = ["cap_row", "full_name_tip", "join_names", "label_factory", "name_label",
           "short"]

LIST_CHARS = 50     # widest entry of a drop-down list, in characters


def name_label(label, lines=1, chars=10):
    """Let label end in "…" after lines lines. chars is how wide it asks
    to be: little, so a long name doesn't widen the tiles next to it; it
    still fills the room it gets."""
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_max_width_chars(chars)
    if lines > 1:
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)   # also names without spaces
        label.set_lines(lines)
    return label


def full_name_tip(label, name, chars):
    """Show name whole when pointing at label, if it may be cut there."""
    if len(name) > chars:
        label.set_tooltip_text(name)
    return label


def cap_row(row, title_lines=2, subtitle_lines=2):
    """An Adw.ActionRow (or ExpanderRow) with a name: at most two lines."""
    row.set_title_lines(title_lines)
    row.set_subtitle_lines(subtitle_lines)
    return row


def label_factory(texts=None, chars=LIST_CHARS, in_list=False):
    """Rows of a Gtk.DropDown of strings as labels ending in "…" when long.

    texts: what to show instead, one per row, e.g. only a preset's name on
    the button and "Folder – Name" in the list. in_list: for the list that
    opens, whose entries are as wide as their text, up to chars."""
    factory = Gtk.SignalListItemFactory()

    def setup(_f, item):
        item.set_child(name_label(Gtk.Label(xalign=0), chars=chars))

    def bind(_f, item):
        text = item.get_item().get_string() if texts is None \
            else texts[item.get_position()]
        label = item.get_child()
        label.set_label(text)
        # Else the list takes only the width of its button and cuts every
        # entry
        if in_list:
            label.set_width_chars(min(len(text), chars))

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    return factory
