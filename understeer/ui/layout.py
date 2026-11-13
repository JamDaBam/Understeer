"""Building blocks of the pages, the same on all of them: a scrolling
column, section headings, grids of tiles and the dashed tiles that make
something new."""
import weakref

from gi.repository import Adw, GLib, GObject, Gtk

SIDES = ("top", "bottom", "start", "end")
# How wide the content of a page gets in a large window: more than a
# Full HD screen leaves free, so a maximized window there uses its width,
# but not the whole of a 4K screen, where lines would get hard to follow.
CONTENT_WIDTH = 1800


def margins(widget, size, sides=SIDES):
    """Give widget a margin of size on these sides; returns it."""
    for side in sides:
        getattr(widget, f"set_margin_{side}")(size)
    return widget


def content_clamp(child=None):
    """Centres child, at most CONTENT_WIDTH wide, the same on every page."""
    return Adw.Clamp(maximum_size=CONTENT_WIDTH, tightening_threshold=900,
                     child=child)


def scroll_page():
    """Scrollable page with a centred column; returns (page, column)."""
    column = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6), 12)
    clamp = content_clamp(column)
    page = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                              vexpand=True, child=clamp)
    return page, column


_ALL_COLUMNS = weakref.WeakSet()


class Columns:
    """The column width the tile grids of one page share, so their tiles
    line up and each grid has as many per row as the others.

    Gtk.FlowBox gives a row as many tiles as fit at the natural width of
    its widest tile, and that depends on its text: a grid with a long
    preset name got fewer, wider tiles than the one above, and asked for
    more height than it then used. Here every tile asks for the same
    width: the largest minimum width of all tiles of the page, which
    comes from the CSS (min-width) and from what can't shrink, such as
    the quick select of the steering angle.
    """

    def __init__(self):
        self.width = 0
        # Held here: a WeakSet loses grids nothing else in Python holds,
        # although GTK still shows them. Grids taken off the page are
        # dropped in _update().
        self._grids = []
        self._scheduled = False
        _ALL_COLUMNS.add(self)

    def add(self, grid):
        self._grids.append(grid)
        self.schedule()

    def schedule(self):
        """Measure again once the main loop is idle, e.g. after tiles were
        added or the layout changed."""
        if not self._scheduled:
            self._scheduled = True
            GLib.idle_add(self._update)

    def _update(self):
        self._scheduled = False
        kept = []
        for grid in self._grids:
            if grid.get_parent() is not None:
                grid.placed = True
            elif getattr(grid, "placed", False):
                continue                # was on the page, is gone now
            kept.append(grid)
        self._grids = kept
        places = [place for grid in kept if grid.get_parent()
                  for place in _children(grid)]
        width = max((tile.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
                     for place in places
                     if (tile := place.get_child()) is not None
                     and tile.get_visible()), default=0)
        if width != self.width:
            self.width = width
            for place in places:
                place.queue_resize()
        return GLib.SOURCE_REMOVE


def update_columns():
    """Measure every page's tiles again, e.g. after switching between the
    full and the compact layout, whose tiles have other minimum widths."""
    for columns in list(_ALL_COLUMNS):
        columns.schedule()


def columns():
    """A Columns for the tile grids of one page."""
    return Columns()


def _children(grid):
    child = grid.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.FlowBoxChild):
            yield child
        child = child.get_next_sibling()


class _ColumnLayout(Gtk.LayoutManager):
    """The place of a tile in a TileGrid: as wide as the shared columns
    naturally, not as its text would like; at least its minimum width."""

    def __init__(self, columns):
        super().__init__()
        self.columns = columns
        self._minimum = None

    def do_measure(self, widget, orientation, for_size):
        tile = widget.get_first_child()
        if tile is None or not tile.get_visible():
            return 0, 0, -1, -1
        minimum, natural, _mb, _nb = tile.measure(orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            if minimum != self._minimum:
                # A new tile, or the layout or theme changed: the shared
                # width is measured again once the CSS applies
                self._minimum = minimum
                self.columns.schedule()
            natural = max(minimum, self.columns.width)
        return minimum, natural, -1, -1

    def do_allocate(self, widget, width, height, baseline):
        tile = widget.get_first_child()
        if tile is not None:
            tile.allocate(width, height, baseline, None)


class TileGrid(Gtk.FlowBox):
    """A grid of tiles that wraps to the window width; see Columns."""
    __gtype_name__ = "UndersteerTileGrid"

    def __init__(self, columns=None, **props):
        super().__init__(**props)
        self.columns = columns or Columns()
        self.columns.add(self)

    def append(self, tile):
        place = Gtk.FlowBoxChild()
        place.set_layout_manager(_ColumnLayout(self.columns))
        place.set_child(tile)
        super().append(place)
        self.columns.schedule()


def tile_grid(min_per_line=1, max_per_line=5, columns=None):
    """Grid of tiles that wraps to the window width. Grids of one page
    share columns (see Columns), so their tiles line up."""
    # vexpand set to False: tiles stretch a line inside them (vexpand)
    # to keep their buttons at the bottom, which would otherwise make the
    # grid take the free height of a page that is shorter than the window,
    # with empty space below the first grid
    return TileGrid(columns, selection_mode=Gtk.SelectionMode.NONE,
                    homogeneous=True, min_children_per_line=min_per_line,
                    max_children_per_line=max_per_line, column_spacing=12,
                    row_spacing=12, valign=Gtk.Align.START, vexpand=False)


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


def tab_row(stack, tabs):
    """A row of tab buttons above stack, as above the settings of the
    Tuning view. tabs: [(key, title, page)]; the first is shown. Returns
    the row (more widgets can be appended at its end)."""
    row = Gtk.Box(spacing=2, css_classes=["tab-row"])
    buttons = {}
    for key, title, page in tabs:
        button = Gtk.ToggleButton(label=title, css_classes=["flat", "tab"])
        if buttons:
            button.set_group(next(iter(buttons.values())))
        button.connect("toggled", lambda b, k=key: b.get_active() and
                       stack.set_visible_child_name(k))
        buttons[key] = button
        row.append(button)
        stack.add_named(page, key)
    buttons[tabs[0][0]].set_active(True)
    stack.connect("notify::visible-child-name", lambda s, _p:
                  buttons[s.get_visible_child_name()].set_active(True))
    return row


def page_head(column, intro, buttons=()):
    """The line about a page, with its buttons at the end."""
    head = Gtk.Box(spacing=12)
    label = Gtk.Label(label=intro, xalign=0, wrap=True, hexpand=True)
    label.add_css_class("dim-label")
    head.append(label)
    for button in buttons:
        button.set_valign(Gtk.Align.START)
        head.append(button)
    column.append(head)


def level_bar(**props):
    """A bar for a value 0..1, in the accent colour all the way (GTK
    colours a full bar differently)."""
    bar = Gtk.LevelBar(min_value=0, max_value=1, **props)
    for offset in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH,
                   Gtk.LEVEL_BAR_OFFSET_FULL):
        bar.remove_offset_value(offset)
    return bar


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


class SwitchRow(Adw.ActionRow):
    """A row with a switch at its end that only the switch turns on and
    off, not a click elsewhere on the row (Adw.SwitchRow toggles on any
    click). Used like Adw.SwitchRow: active, get_active(), set_active()."""
    __gtype_name__ = "UndersteerSwitchRow"

    active = GObject.Property(type=bool, default=False)

    def __init__(self, **props):
        active = props.pop("active", False)
        super().__init__(**props)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.bind_property("active", self.switch, "active",
                           GObject.BindingFlags.BIDIRECTIONAL
                           | GObject.BindingFlags.SYNC_CREATE)
        self.add_suffix(self.switch)
        # The switch tells screen readers what it is for
        self.switch.update_relation([Gtk.AccessibleRelation.LABELLED_BY], [self])
        self.active = active

    def get_active(self):
        return self.active

    def set_active(self, active):
        self.active = bool(active)
