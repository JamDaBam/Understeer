"""Building blocks of the pages, the same on all of them: a scrolling
column, section headings, grids of tiles and the dashed tiles that make
something new."""
from gi.repository import Adw, Gtk

SIDES = ("top", "bottom", "start", "end")


def margins(widget, size, sides=SIDES):
    """Give widget a margin of size on these sides; returns it."""
    for side in sides:
        getattr(widget, f"set_margin_{side}")(size)
    return widget


def scroll_page():
    """Scrollable page with a centred column; returns (page, column)."""
    column = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6), 12)
    clamp = Adw.Clamp(maximum_size=1200, tightening_threshold=900, child=column)
    page = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                              vexpand=True, child=clamp)
    return page, column


def tile_grid(min_per_line=1, max_per_line=5):
    """Grid of tiles that wraps to the window width."""
    return Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                       homogeneous=True, min_children_per_line=min_per_line,
                       max_children_per_line=max_per_line, column_spacing=12,
                       row_spacing=12, valign=Gtk.Align.START)


def section(column, title, description=None, suffix=None):
    """A section heading in column, with a line about it and a widget at
    its end (e.g. buttons)."""
    head = Gtk.Box(spacing=12)
    label = Gtk.Label(label=title, xalign=0, hexpand=True)
    label.add_css_class("section-title")
    head.append(label)
    if suffix is not None:
        suffix.set_valign(Gtk.Align.END)
        head.append(suffix)
    column.append(head)
    if description:
        desc = Gtk.Label(label=description, xalign=0, wrap=True)
        desc.add_css_class("dim-label")
        desc.set_margin_start(6)
        desc.set_margin_bottom(6)
        column.append(desc)


def dashed_tile(icon, title, hint=None, action=None, clicked=None):
    """A dashed tile that makes something new: action is the name of the
    action it starts, or clicked() is called."""
    button = Gtk.Button(action_name=action,
                        css_classes=["card", "tile", "new-preset"])
    if clicked:
        button.connect("clicked", lambda b: clicked())
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                    valign=Gtk.Align.CENTER)
    image = Gtk.Image.new_from_icon_name(icon)
    image.set_pixel_size(32)
    image.add_css_class("tile-icon")
    image.set_halign(Gtk.Align.CENTER)
    inner.append(image)
    inner.append(Gtk.Label(label=title, css_classes=["heading"]))
    if hint:
        label = Gtk.Label(label=hint, wrap=True, max_width_chars=30,
                          justify=Gtk.Justification.CENTER)
        label.add_css_class("dim-label")
        inner.append(label)
    button.set_child(inner)
    return button
