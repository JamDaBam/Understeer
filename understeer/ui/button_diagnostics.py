"""Button diagnostics: every key and button of the Fanatec input devices
as a small numbered cell, lit while held down and green once it was pressed
in this test; the details of the button pressed last in a line above.

Read only. It shows the buttons of the window's InputMonitor (window.inputs)
and opens no input device itself; the window calls update() with its live
display and rebuild() when the devices change. Nothing is remapped. A tab
of the Diagnostics view (see diagnostics_page).
"""
from gi.repository import Gtk

from ..i18n import _
from ..inputs import sdl_order
from . import layout


def summary_text(buttons):
    """How far the test is: buttons pressed at least once of all."""
    tested = sum(1 for b in buttons if b.tested)
    return _("{tested} of {total} buttons tested").format(
        tested=tested, total=len(buttons))


def held_text(buttons, numbers):
    """The buttons held down now, by their number; several at once too."""
    held = [numbers[b.key] for b in buttons if b.pressed]
    if not held:
        return _("No button held down")
    return _("Held down: {buttons}").format(
        buttons=", ".join(_("Button {n}").format(n=n) for n in held))


def describe(button, numbers):
    """"Button 5 · SDL 4 · BTN_TOP2 · 292": the number shown (from 1), the
    one SDL and game config files use (from 0), kernel name and code."""
    n = numbers.get(button.key)
    return " · ".join((_("Button {n}").format(n=n if n else "?"),
                       f"SDL {n - 1}" if n else "SDL ?",
                       button.name, str(button.code)))


def last_text(button, numbers):
    """The button pressed last, described, and how often it was pressed."""
    if button is None:
        return _("Press a button to see its name and code")
    return _("Last pressed: {button} · pressed {n}×").format(
        button=describe(button, numbers), n=button.presses)


def numbered(buttons, devices):
    """The buttons in the order shown: per device in SDL's order (see
    sdl_order), numbered from 1 on each device as games show them.
    Returns ([(device, [Button])], {key: number})."""
    groups, numbers = [], {}
    for device in devices:
        mine = sorted((b for b in buttons.values() if b.device == device),
                      key=lambda b: sdl_order(b.code))
        if mine:
            groups.append((device, mine))
            numbers.update({b.key: i for i, b in enumerate(mine, 1)})
    return groups, numbers


class ButtonDiagnostics:
    """The page; window.button_diagnostics."""

    def __init__(self, window):
        self.window = window
        self.widgets = {}       # button key -> its cell
        self.shown = {}         # button key -> (pressed, presses) on screen
        self.numbers = {}       # button key -> number shown

        reset = Gtk.Button(label=_("Reset Button Test"))
        reset.set_tooltip_text(_("Count every button as untested again"))
        reset.connect("clicked", lambda b: self.reset())
        self.page, outer = layout.scroll_page()
        layout.page_head(
            outer, _("Press every button once. A button lights up while it is "
                     "held down and turns green once it was pressed. Hover "
                     "over a button for its name and code. Read only: no "
                     "button is remapped."), [reset])
        self.problem = Gtk.Label(xalign=0, wrap=True)
        self.problem.add_css_class("warning")
        outer.append(self.problem)
        self.summary = Gtk.Label(xalign=0, css_classes=["heading"])
        outer.append(self.summary)
        self.last = Gtk.Label(xalign=0, wrap=True, css_classes=["numeric"])
        outer.append(self.last)
        self.held = Gtk.Label(xalign=0, wrap=True,
                              css_classes=["dim-label", "numeric"])
        outer.append(self.held)
        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.append(self.column)
        window.button_diagnostics = self
        self.rebuild()

    def reset(self):
        self.window.inputs.reset_buttons()
        self.update()

    def rebuild(self):
        """A section per device with a small cell per button; after the
        devices were opened again or one was unplugged."""
        while (child := self.column.get_first_child()) is not None:
            self.column.remove(child)
        self.widgets, self.shown = {}, {}
        inputs = self.window.inputs
        problems = []
        if inputs.denied:
            problems.append(_("No read access to {devices}. Set up "
                              "permissions in the preferences.").format(
                devices=", ".join(inputs.denied)))
        elif not inputs.devices:
            problems.append(self.window._input_problem())
        elif not inputs.buttons:
            problems.append(_("The input devices report no buttons."))
        self.problem.set_label("\n".join(problems))
        self.problem.set_visible(bool(problems))

        groups, self.numbers = numbered(inputs.buttons, inputs.devices)
        for device, buttons in groups:
            layout.section(self.column, device)
            grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                               homogeneous=True, min_children_per_line=6,
                               max_children_per_line=24, column_spacing=4,
                               row_spacing=4, valign=Gtk.Align.START)
            for button in buttons:
                grid.append(self._cell(button))
            self.column.append(grid)
        self.update()

    def _cell(self, button):
        """A small cell with the number; the rest in its tooltip."""
        n = self.numbers[button.key]
        cell = Gtk.Label(label=str(n), css_classes=["button-cell", "numeric"])
        cell.set_tooltip_text(describe(button, self.numbers))
        self.widgets[button.key] = cell
        return cell

    def update(self):
        """Show the current state; called with the live display. Only the
        cells whose button changed are touched (wheels have 100 and more)."""
        buttons = self.window.inputs.buttons
        for key, cell in self.widgets.items():
            button = buttons.get(key)
            if button is None:
                continue
            state = (button.pressed, button.presses)
            if self.shown.get(key) == state:
                continue
            self.shown[key] = state
            for css, on in (("button-held", button.pressed),
                            ("button-tested", button.tested)):
                (cell.add_css_class if on else cell.remove_css_class)(css)
        shown = [buttons[k] for k in self.widgets if k in buttons]
        self.summary.set_label(summary_text(shown))
        self.summary.set_visible(bool(shown))
        self.held.set_label(held_text(shown, self.numbers))
        self.held.set_visible(bool(shown))
        last = buttons.get(self.window.inputs.last_button)
        self.last.set_label(last_text(last, self.numbers))
        self.last.set_visible(bool(shown))
