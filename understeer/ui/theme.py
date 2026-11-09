"""Applies colour scheme, theme, accent colour and corner radius."""
import logging

from gi.repository import Adw, Gdk, Gtk

from .. import config, themes

log = logging.getLogger("understeer.theme")

SCHEMES = {"system": Adw.ColorScheme.DEFAULT,
           "light": Adw.ColorScheme.FORCE_LIGHT,
           "dark": Adw.ColorScheme.FORCE_DARK}
_provider = None


def current():
    """(theme, colour scheme, accent, radius) as chosen by the user."""
    return (themes.theme_key(config.get_setting("theme")),
            config.get_setting("color_scheme") or "system",
            config.get_setting("accent"),
            themes.radius(config.get_setting("corner_radius",
                                              themes.DEFAULT_RADIUS)))


def apply():
    """Show the chosen look; again after each change."""
    global _provider
    theme, scheme, accent, radius = current()
    Adw.StyleManager.get_default().set_color_scheme(
        SCHEMES[themes.color_scheme(theme, scheme)])
    display = Gdk.Display.get_default()
    if display is None:
        return
    if _provider is None:
        _provider = Gtk.CssProvider()
        # Above the app's own style.css, which it adjusts.
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    variables = (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 16)
    text = themes.css(theme, accent, radius, variables)
    if hasattr(_provider, "load_from_string"):      # GTK 4.12
        _provider.load_from_string(text)
    else:
        _provider.load_from_data(text, -1)
    log.info("Look: theme %s, colour scheme %s, accent %s, corners %d px",
             theme, scheme, accent or "auto", radius)
