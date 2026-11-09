"""Themes, accent colours and corner radius, as an extra stylesheet.

Works without GTK: css() returns the stylesheet text, the UI loads it.
Libadwaita 1.6 (GTK 4.16) and newer take colours as CSS variables
(--accent-bg-color); older versions as named colours (@accent_bg_color).
"""
from .i18n import N_

# key: (name, description, forced colour scheme or None, colours or None)
# Colours: window, view (lists), card, header bar, popover, text, accent.
THEMES = {
    "standard": (N_("Standard"), N_("Light or dark"), None, None),
    "carbon": (N_("Carbon"), N_("Dark, orange"), "dark", dict(
        window="#141518", view="#18191c", card="#1d1f24", headerbar="#1b1c20",
        popover="#202227", fg="#ececee", accent="#ed5b00")),
    "midnight": (N_("Midnight"), N_("Dark blue, cyan"), "dark", dict(
        window="#0f1626", view="#121b2e", card="#17213a", headerbar="#131c31",
        popover="#1a2540", fg="#e6ebf5", accent="#1a8fb5")),
    "pitlane": (N_("Pit Lane"), N_("Light, red"), "light", dict(
        window="#f3f1ec", view="#fbfaf7", card="#fbfaf7", headerbar="#ebe7df",
        popover="#fbfaf7", fg="#2b2622", accent="#c01c28")),
}
DEFAULT_THEME = "standard"

# The accent colours of GNOME; None follows the theme or the system.
ACCENTS = {
    "blue": (N_("Blue"), "#3584e4"),
    "teal": (N_("Teal"), "#2190a4"),
    "green": (N_("Green"), "#3a944a"),
    "yellow": (N_("Yellow"), "#c88800"),
    "orange": (N_("Orange"), "#ed5b00"),
    "red": (N_("Red"), "#e62d42"),
    "pink": (N_("Pink"), "#d56199"),
    "purple": (N_("Purple"), "#9141ac"),
    "slate": (N_("Slate"), "#6f8396"),
}

SCHEMES = ("system", "light", "dark")
RADIUS_MIN, RADIUS_MAX = 0, 16
DEFAULT_RADIUS = 3


def theme_key(value):
    return value if value in THEMES else DEFAULT_THEME


def color_scheme(theme, scheme):
    """"light", "dark" or "system": a theme may decide it."""
    forced = THEMES[theme_key(theme)][2]
    return forced or (scheme if scheme in SCHEMES else "system")


def radius(value):
    try:
        return max(RADIUS_MIN, min(RADIUS_MAX, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_RADIUS


def _colors(theme, accent):
    """{adwaita colour name: value} to set."""
    colors = {}
    own = THEMES[theme_key(theme)][3]
    if own:
        colors.update({
            "window_bg_color": own["window"], "window_fg_color": own["fg"],
            "view_bg_color": own["view"], "view_fg_color": own["fg"],
            "card_bg_color": own["card"], "card_fg_color": own["fg"],
            "headerbar_bg_color": own["headerbar"], "headerbar_fg_color": own["fg"],
            "headerbar_backdrop_color": own["window"],
            "sidebar_bg_color": own["headerbar"], "sidebar_fg_color": own["fg"],
            "popover_bg_color": own["popover"], "popover_fg_color": own["fg"],
            "dialog_bg_color": own["window"], "dialog_fg_color": own["fg"],
            "accent_bg_color": own["accent"],
        })
    if accent in ACCENTS:
        colors["accent_bg_color"] = ACCENTS[accent][1]
    return colors


def css(theme=DEFAULT_THEME, accent=None, corner=DEFAULT_RADIUS, variables=True):
    """The stylesheet for these choices.

    variables: whether GTK understands CSS variables (4.16 and newer).
    """
    lines = []
    colors = _colors(theme, accent)
    if colors:
        if variables:
            # Libadwaita's own widgets read the variables and derive the
            # accent text colour from the background.
            lines.append(":root {")
            lines += [f"  --{name.replace('_', '-')}: {value};"
                      for name, value in colors.items()]
            lines.append("}")
        # Named colours, used by style.css (and by Libadwaita before 1.6).
        if "accent_bg_color" in colors:
            # Readable as text: a bit towards the text colour.
            colors.setdefault("accent_color", f"mix({colors['accent_bg_color']}, "
                                              "@window_fg_color, 0.25)")
        lines += [f"@define-color {name} {value};" for name, value in colors.items()]

    r = radius(corner)
    lines.append(f"""
/* Corners: {r} px */
.tile, .card, list.boxed-list, popover > contents, button.advanced-toggle,
.game-cover, .cover-placeholder, .theme-choice, .theme-preview {{
  border-radius: {r}px;
}}
list.boxed-list > row:first-child {{
  border-top-left-radius: {r}px; border-top-right-radius: {r}px;
}}
list.boxed-list > row:last-child {{
  border-bottom-left-radius: {r}px; border-bottom-right-radius: {r}px;
}}
list.boxed-list > row.expander:last-child list.nested,
list.boxed-list > row.expander:last-child list.nested > row:last-child {{
  border-bottom-left-radius: {r}px; border-bottom-right-radius: {r}px;
}}
button:not(.circular), entry, spinbutton, dropdown > button,
.tab-row button.tab, levelbar > trough, levelbar > trough > block,
scale > trough, scale > trough > highlight {{
  border-radius: {r}px;
}}
.tab-row button.tab {{
  border-bottom-left-radius: 0; border-bottom-right-radius: 0;
}}
.linked:not(.vertical) > :not(:first-child),
.linked:not(.vertical) > :not(:first-child) > button {{
  border-top-left-radius: 0; border-bottom-left-radius: 0;
}}
.linked:not(.vertical) > :not(:last-child),
.linked:not(.vertical) > :not(:last-child) > button {{
  border-top-right-radius: 0; border-bottom-right-radius: 0;
}}
.linked.vertical > :not(:first-child) {{
  border-top-left-radius: 0; border-top-right-radius: 0;
}}
.linked.vertical > :not(:last-child) {{
  border-bottom-left-radius: 0; border-bottom-right-radius: 0;
}}""")
    lines.append(_choices_css())
    return "\n".join(lines) + "\n"


def _choices_css():
    """Colours of the accent swatches and theme previews in the preferences."""
    rules = [f".accent-swatch.accent-{key} {{ background: {color}; }}"
             for key, (_name, color) in ACCENTS.items()]
    rules.append(".accent-swatch.accent-auto { background: conic-gradient("
                 + ", ".join(c for _n, c in ACCENTS.values()) + ", #3584e4); }")
    for key, (_name, _desc, _forced, colors) in THEMES.items():
        if colors is None:          # follows light/dark: half and half
            rules.append(f".theme-preview.theme-{key} {{ background: linear-gradient("
                         "to right, #fafafb 50%, #242428 50%); }")
            rules.append(f".theme-preview.theme-{key} .theme-mini {{ "
                         "background: alpha(#888, 0.25); }")
            rules.append(f".theme-preview.theme-{key} .theme-mini-accent {{ "
                         "background: #3584e4; }")
            continue
        rules.append(f".theme-preview.theme-{key} {{ background: {colors['window']}; }}")
        rules.append(f".theme-preview.theme-{key} .theme-mini {{ "
                     f"background: {colors['card']}; }}")
        rules.append(f".theme-preview.theme-{key} .theme-mini-accent {{ "
                     f"background: {colors['accent']}; }}")
    return "\n".join(rules)
