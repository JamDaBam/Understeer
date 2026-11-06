"""Widgets for the live input display."""
from gi.repository import Gdk, Graphene, Gtk

# Top-centre stripe like on a real steering wheel; shows the angle even
# where the three spokes look the same (e.g. at 120°).
MARKER_COLOR = Gdk.RGBA()
MARKER_COLOR.parse("#ff7800")


class WheelView(Gtk.Widget):
    """A steering wheel icon turned by the current steering angle.

    Icon and widget size come from the stylesheet (.wheel-view), so the
    compact layout can make it smaller.
    """

    def __init__(self, icon_name):
        super().__init__()
        self.set_layout_manager(Gtk.BinLayout())
        self.add_css_class("wheel-view")
        self.angle = 0.0
        self.image = Gtk.Image.new_from_icon_name(icon_name)
        self.image.set_parent(self)

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

    def do_dispose(self):
        if self.image is not None:
            self.image.unparent()
            self.image = None
