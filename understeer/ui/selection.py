"""Choosing several tiles to delete them at once, the same on the Presets
and the Games page: Select switches it on, the tiles get a check mark and
a click picks them, a bar below has Select All and Delete; Done or Esc
switches it off."""
from gi.repository import Gtk

from ..i18n import _


class Selection:
    def __init__(self, delete_label, rebuild, delete):
        """rebuild(): build the tiles again (with or without check marks);
        delete(keys): delete the chosen ones."""
        self.active = False
        self.keys = set()           # the chosen ones
        self._available = []       # what can be chosen now, in order
        self._rebuild = rebuild
        self._delete = delete
        self._marks = {}            # key: (check button, tile)

        self.button = Gtk.Button(label=_("Select"))
        self.button.add_css_class("flat")
        self.button.set_tooltip_text(_("Choose several to delete them at once"))
        self.button.connect("clicked", lambda b: self.stop() if self.active
                            else self.start())

        self.bar = Gtk.ActionBar(revealed=False)
        self.all_button = Gtk.Button(label=_("Select All"))
        self.all_button.connect("clicked", lambda b: self._select_all())
        self.count = Gtk.Label()
        self.delete_button = Gtk.Button(label=delete_label)
        self.delete_button.add_css_class("destructive-action")
        self.delete_button.connect("clicked", lambda b: self._delete_chosen())
        self.bar.pack_start(self.all_button)
        self.bar.set_center_widget(self.count)
        self.bar.pack_end(self.delete_button)
        self._update()

    def page(self, content):
        """content with the bar below it; Esc in it ends choosing."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_vexpand(True)
        box.append(content)
        box.append(self.bar)
        keys = Gtk.ShortcutController()
        keys.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("Escape"),
            Gtk.CallbackAction.new(lambda *a: self.active and (self.stop() or True))))
        box.add_controller(keys)
        return box

    # -- switching on and off --------------------------------------------------
    def start(self):
        if self.active:
            return
        self.active = True
        self.keys.clear()
        self._rebuild()
        self._update()

    def stop(self):
        if not self.active:
            return
        self.active = False
        self.keys.clear()
        self._rebuild()
        self._update()

    def available(self, keys):
        """While the tiles are built: what can be chosen; chosen ones that
        are gone are dropped."""
        self._available = list(keys)
        self.keys &= set(self._available)
        self._marks = {}
        self._update()

    # -- tiles ------------------------------------------------------------------
    def wrap(self, tile, key):
        """tile with a check mark while choosing; a click on it picks it."""
        if not self.active:
            return tile
        overlay = Gtk.Overlay(child=tile)
        catcher = Gtk.Button(halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        catcher.add_css_class("select-catcher")
        catcher.update_property([Gtk.AccessibleProperty.LABEL], [_("Select")])
        # Top right, where the ⋮ menu is otherwise: the tile shows none then
        check = Gtk.CheckButton(halign=Gtk.Align.END, valign=Gtk.Align.START,
                                margin_top=10, margin_end=10, can_target=False,
                                can_focus=False)
        check.add_css_class("selection-mode")
        overlay.add_overlay(catcher)
        overlay.add_overlay(check)
        self._marks[key] = (check, tile)
        catcher.connect("clicked", lambda b: self.toggle(key))
        self._show(key)
        return overlay

    def toggle(self, key):
        if key in self.keys:
            self.keys.discard(key)
        else:
            self.keys.add(key)
        self._show(key)
        self._update()

    def _show(self, key):
        check, tile = self._marks.get(key, (None, None))
        if check is None:
            return
        check.set_active(key in self.keys)
        if key in self.keys:
            tile.add_css_class("selected")
        else:
            tile.remove_css_class("selected")

    def _select_all(self):
        everything = set(self._available)
        self.keys = set() if self.keys >= everything else everything
        for key in self._marks:
            self._show(key)
        self._update()

    def _delete_chosen(self):
        chosen = [k for k in self._available if k in self.keys]
        if not chosen:
            return
        self.active = False
        self.keys.clear()
        self._delete(chosen)        # rebuilds without check marks
        self._update()

    def _update(self):
        self.button.set_label(_("Done") if self.active else _("Select"))
        if self.active:
            self.button.remove_css_class("flat")
            self.button.add_css_class("suggested-action")
        else:
            self.button.remove_css_class("suggested-action")
            self.button.add_css_class("flat")
        self.button.set_sensitive(self.active or bool(self._available))
        self.bar.set_revealed(self.active)
        n = len(self.keys)
        self.count.set_label(_("Selected: {n}").format(n=n))
        self.delete_button.set_sensitive(n > 0)
        everything = bool(self._available) and self.keys >= set(self._available)
        self.all_button.set_label(_("Select None") if everything else _("Select All"))
