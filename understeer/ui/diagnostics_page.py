"""The Diagnostics view: axes, buttons and LEDs in tabs, like the settings
of the Tuning view.

The pages are window.axis_diagnostics (with the health check),
window.button_diagnostics and window.led_diagnostics. Axes and buttons only read; the LED page writes,
so it is active only while its tab is shown (see LedDiagnostics.enter()).
"""
from gi.repository import Adw, Gtk

from ..i18n import N_, _
from . import layout
from .axis_diagnostics import AxisDiagnostics
from .button_diagnostics import ButtonDiagnostics
from .led_diagnostics import LedDiagnostics

VIEW = "diagnostics"
TABS = [
    ("axes", N_("Axes")),
    ("buttons", N_("Buttons")),
    ("leds", N_("LEDs")),
]


class DiagnosticsPage:
    """window.diagnostics_page; its widget is .view."""

    def __init__(self, window, tab=None):
        self.window = window
        pages = {"axes": AxisDiagnostics(window).page,
                 "buttons": ButtonDiagnostics(window).page,
                 "leds": LedDiagnostics(window).page}
        self.tabs = Gtk.Stack(vexpand=True)
        self.tabs.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        row = layout.tab_row(self.tabs, [(key, _(title), pages[key])
                                         for key, title in TABS])
        if tab in pages:
            self.tabs.set_visible_child_name(tab)
        self.tabs.connect("notify::visible-child-name",
                          lambda *a: self.shown_changed())
        top = layout.margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL),
                             12, ("top", "start", "end"))
        top.append(row)
        self.view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.view.append(Adw.Clamp(maximum_size=1200, tightening_threshold=900,
                                   child=top))
        self.view.append(self.tabs)

    @property
    def tab(self):
        return self.tabs.get_visible_child_name()

    def shown(self, tab):
        """Whether tab is on screen: this view and tab shown, the window too."""
        w = self.window
        return (not w._in_background and not w._shut_down
                and w.views.get_visible_child_name() == VIEW
                and self.tab == tab)

    def shown_changed(self):
        """After switching view or tab, hiding or showing the window."""
        led = self.window.led_diagnostics
        if self.shown("leds"):
            led.enter()
        else:
            led.leave()
        if self.shown("axes"):
            self.window.axis_diagnostics.update()
        else:
            self.window.axis_diagnostics.leave()
        if self.shown("buttons"):
            self.window.button_diagnostics.update()

    def leave(self):
        """Before the window hides, quits or is built anew."""
        self.window.led_diagnostics.leave()
        self.window.axis_diagnostics.leave()
