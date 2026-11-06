"""Widgets for the live input display."""
from gi.repository import Adw, Gdk, Graphene, Gtk

# Top-centre stripe like on a real steering wheel; shows the angle even
# where the three spokes look the same (e.g. at 120°).
MARKER_COLOR = Gdk.RGBA()
MARKER_COLOR.parse("#ff7800")


class WheelView(Gtk.Box):
    """A steering wheel icon turned by the current steering angle.

    Icon and widget size come from the stylesheet (.wheel-view), so the
    compact layout can make it smaller. Built on Gtk.Box so GTK manages the
    child; Python widgets can't reliably free children themselves.
    """

    def __init__(self, icon_name):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.add_css_class("wheel-view")
        self.angle = 0.0
        self.image = Gtk.Image.new_from_icon_name(icon_name)
        self.image.set_hexpand(True)
        self.image.set_vexpand(True)
        self.append(self.image)

    def set_angle(self, degrees):
        if degrees != self.angle:
            self.angle = degrees
            self.queue_draw()

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(w / 2, h / 2))
        snapshot.rotate(self.angle)
        snapshot.translate(Graphene.Point().init(-w / 2, -h / 2))
        self.snapshot_child(self.image, snapshot)
        # The icon fills 100 of 128 px (see style.css); its rim starts
        # 1/16 below the top and is 2/16 thick.
        icon = min(w, h) * 100 / 128
        marker = Graphene.Rect().init(w / 2 - icon * 0.05,
                                      (h - icon) / 2 + icon / 16,
                                      icon * 0.1, icon * 2 / 16)
        snapshot.append_color(MARKER_COLOR, marker)
        snapshot.restore()


class _RatioLayout(Gtk.LayoutManager):
    """Gives the child the full width and asks for width * ratio as height."""

    def __init__(self, ratio, natural_width):
        super().__init__()
        self.ratio = ratio
        self.natural_width = natural_width

    def do_get_request_mode(self, widget):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, widget, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, self.natural_width, -1, -1
        width = for_size if for_size >= 0 else self.natural_width
        height = int(width * self.ratio)
        return height, height, -1, -1

    def do_allocate(self, widget, width, height, baseline):
        child = widget.get_first_child()
        if child is not None:
            child.allocate(width, height, baseline, None)


class CoverView(Adw.Bin):
    """Shows its child at the full width it gets, in portrait format.

    Gtk.AspectFrame sizes itself after its child, so a cover picture would
    stay small. Adw.Bin frees its child itself, which Python widgets can't
    do reliably.
    """

    def __init__(self, child, ratio=1.5, natural_width=100):
        super().__init__()
        self.set_layout_manager(_RatioLayout(ratio, natural_width))
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_child(child)
