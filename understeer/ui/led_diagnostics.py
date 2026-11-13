"""LED diagnostics: switch the rev and flag LEDs one by one, show the RGB
colours and run a short, cancellable test sequence.

A diagnostic, not telemetry: nothing runs by itself. The LEDs come from the
window's capabilities (see leds.find()); the window calls hardware_changed()
when they change, e.g. when the wheelbase is unplugged or another wheel put
on, which stops a running test. When the dialog closes, the LEDs whose state
could be read are set back as they were.
"""
from gi.repository import Adw, GLib, Gtk

from .. import leds
from ..i18n import _
from . import layout

STEP_MS = 250           # one step of the test sequence


def color_names():
    return {"red": _("Red"), "green": _("Green"), "blue": _("Blue"),
            "yellow": _("Yellow"), "cyan": _("Cyan"),
            "magenta": _("Magenta"), "white": _("White")}


def group_title(location, kind):
    if kind == leds.LEFT_FLAG:
        return _("Left Flag LEDs")
    if kind == leds.RIGHT_FLAG:
        return _("Right Flag LEDs")
    return {leds.BASE: _("Wheelbase Rev LEDs"),
            leds.RIM: _("Steering Wheel Rev LEDs"),
            leds.BOTH: _("Rev LEDs")}[location]


def groups(found):
    """[((location, kind), [Led])] in the order of found."""
    out = {}
    for led in found:
        key = (led.location if led.kind == leds.REV else leds.RIM, led.kind)
        out.setdefault(key, []).append(led)
    return list(out.items())


def result_text(result):
    return {
        leds.UNAVAILABLE: _("The LED is not available any more. Is the "
                            "wheelbase still connected?"),
        leds.DENIED: _("No permission to switch the LEDs"),
    }.get(result, _("Could not switch the LED"))


class LedDiagnostics:
    """The dialog; window.led_diagnostics while it is open."""

    def __init__(self, window):
        self.window = window
        self.leds = []
        self.toggles = {}       # LED folder -> its toggle button
        self.before = {}        # LED folder -> State when first seen here
        self.color = leds.SWEEP_COLOR
        self.sequence = None
        self._timer = 0
        self._syncing = False

        self.dialog = window.fit_dialog(Adw.Dialog(title=_("LED Diagnostics")))
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.off_button = Gtk.Button(label=_("All Off"))
        self.off_button.set_tooltip_text(_("Switch every LED off"))
        self.off_button.connect("clicked", lambda b: self.all_off())
        header.pack_start(self.off_button)
        self.test_button = Gtk.Button(label=_("Run Test"))
        self.test_button.add_css_class("suggested-action")
        self.test_button.connect("clicked", lambda b: self._test_clicked())
        header.pack_end(self.test_button)
        view.add_top_bar(header)

        outer = layout.margins(
            Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6), 18)
        intro = Gtk.Label(
            label=_("Switch the LEDs on and off one by one, or run the short "
                    "test that lights them one after another. When this "
                    "dialog closes, the LEDs are set back as they were where "
                    "the driver tells their state."),
            xalign=0, wrap=True)
        intro.add_css_class("dim-label")
        outer.append(intro)
        self.problem = Gtk.Label(xalign=0, wrap=True)
        self.problem.add_css_class("warning")
        outer.append(self.problem)
        self.status = Gtk.Label(xalign=0, wrap=True)
        outer.append(self.status)
        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.append(self.column)
        clamp = Adw.Clamp(maximum_size=900, tightening_threshold=900,
                          child=outer)
        view.set_content(Gtk.ScrolledWindow(
            child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.dialog.set_child(view)
        self.dialog.connect("closed", lambda d: self._closed())

        window.led_diagnostics = self
        self.rebuild()
        self.dialog.present(window)

    # -- building ---------------------------------------------------------
    def rebuild(self):
        """The LEDs of the window's capabilities, a section per group."""
        while (child := self.column.get_first_child()) is not None:
            self.column.remove(child)
        self.toggles = {}
        self.leds = leds.find(self.window.capabilities)
        for folder, state in leds.snapshot(
                [l for l in self.leds if l.folder not in self.before]).items():
            self.before[folder] = state

        if not self.leds:
            problem = _("No LEDs found: no wheelbase connected, or the driver "
                        "offers none for this wheelbase and wheel.")
        elif not any(l.writable for l in self.leds):
            problem = _("No permission to switch the LEDs. The udev rule of "
                        "hid-fanatecff allows it for the members of its "
                        "group, usually “games”.")
        else:
            problem = ""
        self.problem.set_label(problem)
        self.problem.set_visible(bool(problem))

        for (location, kind), group in groups(self.leds):
            layout.section(self.column, group_title(location, kind))
            box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                              homogeneous=True, min_children_per_line=5,
                              max_children_per_line=15, column_spacing=4,
                              row_spacing=4, valign=Gtk.Align.START)
            for led in group:
                box.append(self._toggle(led))
            self.column.append(box)
        self._build_colors()
        self.sync()

    def _toggle(self, led):
        button = Gtk.ToggleButton(label=str(led.number),
                                  css_classes=["led-cell", "numeric"])
        button.set_tooltip_text(led.name)
        button.set_sensitive(led.writable)
        button.connect("toggled", lambda b, l=led: self._toggled(l, b))
        self.toggles[led.folder] = button
        return button

    def _build_colors(self):
        rgb = [l for l in self.leds if l.rgb]
        offered = leds.colors(rgb)
        if not offered:
            return
        if self.color not in offered:
            self.color = offered[-1]
        show = Gtk.Button(label=_("Light All in This Colour"))
        show.connect("clicked", lambda b: self.show_color())
        show.set_sensitive(any(l.writable for l in rgb))
        have = {ch for l in rgb for ch in l.channels}
        channels = [ch for ch in ("red", "green", "blue") if ch in have] \
            + sorted(have - {"red", "green", "blue"})
        names = color_names()
        layout.section(
            self.column, _("RGB Colour"),
            _("Colour channels: {channels}. LEDs switched on here use the "
              "chosen colour.").format(
                channels=", ".join(names.get(ch, ch) for ch in channels)),
            suffix=show)
        box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                          homogeneous=True, min_children_per_line=4,
                          max_children_per_line=7, column_spacing=4,
                          row_spacing=4, valign=Gtk.Align.START)
        first = None
        self.color_buttons = {}
        for name in offered:
            button = Gtk.ToggleButton(label=names[name],
                                      css_classes=["led-color", f"led-{name}"])
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.set_active(name == self.color)
            button.connect("toggled", lambda b, n=name: b.get_active()
                           and self._color_chosen(n))
            self.color_buttons[name] = button
            box.append(button)
        self.column.append(box)

    # -- state ------------------------------------------------------------
    def sync(self):
        """Toggles as the driver reports the LEDs; buttons as possible."""
        self._syncing = True
        try:
            for led in self.leds:
                state = leds.read_state(led)
                self.toggles[led.folder].set_active(leds.is_on(state))
        finally:
            self._syncing = False
        running = self.sequence is not None
        writable = any(l.writable for l in self.leds)
        self.test_button.set_label(_("Stop Test") if running else _("Run Test"))
        self.test_button.set_visible(bool(self.leds))
        self.test_button.set_sensitive(writable)
        for css in ("suggested-action", "destructive-action"):
            self.test_button.remove_css_class(css)
        self.test_button.add_css_class(
            "destructive-action" if running else "suggested-action")
        self.off_button.set_visible(bool(self.leds))
        self.off_button.set_sensitive(writable)
        self.column.set_sensitive(not running)

    def _say(self, text, error=False):
        self.status.set_label(text)
        self.status.set_visible(bool(text))
        (self.status.add_css_class if error
         else self.status.remove_css_class)("error")

    def _done(self, result, text=""):
        """After writing: tell how it went; look again if an LED went."""
        if result == leds.OK:
            self._say(text)
        else:
            self._say(result_text(result), error=True)
        if result == leds.UNAVAILABLE:
            # Calls hardware_changed() if the LEDs are gone indeed
            self.window._check_capabilities()
        if self.window.led_diagnostics is self:
            self.sync()

    def _toggled(self, led, button):
        if self._syncing:
            return
        state = leds.lit(led, self.color) if button.get_active() else leds.off(led)
        self._done(leds.write_state(led, state))

    def _color_chosen(self, name):
        """Lit RGB LEDs change to the colour chosen."""
        if self._syncing or name == self.color:
            return
        self.color = name
        lit = [l for l in self.leds if l.rgb and l.writable
               and leds.is_on(leds.read_state(l))]
        if lit:
            self._done(leds.write_all([(l, leds.lit(l, name)) for l in lit]))

    def show_color(self):
        rgb = [l for l in self.leds if l.rgb and l.writable]
        self._done(leds.write_all([(l, leds.lit(l, self.color)) for l in rgb]))

    def all_off(self):
        self._stop_sequence(restore=False)
        self._done(leds.all_off(self.leds), _("All LEDs switched off"))

    # -- test sequence ----------------------------------------------------
    def _test_clicked(self):
        if self.sequence is not None:
            self._stop_sequence()
            self._say(_("Test stopped"))
            self.sync()
        else:
            self.start_test()

    def start_test(self):
        if self.sequence is not None or not self.leds:
            return
        self.sequence = leds.Sequence(self.leds)
        if not self.sequence.running:
            self.sequence = None
            return
        self._say(_("Test running…"))
        self.sync()
        self._timer = GLib.timeout_add(STEP_MS, self._step)

    def _step(self):
        sequence = self.sequence
        if sequence is None:
            self._timer = 0
            return GLib.SOURCE_REMOVE
        if sequence.advance():
            return GLib.SOURCE_CONTINUE
        self._timer = 0
        self._stop_sequence()
        if sequence.result == leds.OK:
            self._done(leds.OK, _("Test finished"))
        else:
            self._done(sequence.result)
        return GLib.SOURCE_REMOVE

    def _stop_sequence(self, restore=True):
        """Stop a running test; set the LEDs back as before it."""
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        sequence, self.sequence = self.sequence, None
        if sequence is None:
            return
        sequence.cancel()
        if restore:
            sequence.finish()

    @property
    def running(self):
        return self.sequence is not None

    def hardware_changed(self):
        """The window found other capabilities: unplugged, plugged in again,
        another wheel. A running test stops; the LEDs are shown anew."""
        was_running = self.running
        self._stop_sequence()
        self.rebuild()
        if was_running:
            self._say(_("Test stopped: the LEDs changed. Was the wheelbase "
                        "unplugged or the wheel changed?"), error=True)
        elif self.leds:
            self._say("")       # LEDs back: an error from before is over

    def close(self):
        """Close at once, e.g. when the window goes."""
        self._closed()
        self.dialog.force_close()

    def _closed(self):
        if self.window.led_diagnostics is not self:
            return              # already done
        self.window.led_diagnostics = None
        self._stop_sequence()
        leds.restore(self.leds, self.before)
