import re

import pytest

from understeer import themes


def test_unknown_values_fall_back():
    assert themes.theme_key("nope") == "standard"
    assert themes.radius("x") == themes.DEFAULT_RADIUS == 3
    assert themes.radius(99) == themes.RADIUS_MAX and themes.radius(-4) == 0


@pytest.mark.parametrize("theme, scheme, expected", [
    ("standard", "dark", "dark"),
    ("standard", "bogus", "system"),
    ("carbon", "light", "dark"),          # a dark theme stays dark
    ("pitlane", "dark", "light"),
])
def test_color_scheme(theme, scheme, expected):
    assert themes.color_scheme(theme, scheme) == expected


def colours(text):
    """The part that sets colours, before the corners and swatches."""
    return text.split("/* Corners")[0]


def test_standard_theme_sets_no_colours():
    text = themes.css("standard", None, 3)
    assert colours(text).strip() == ""          # Libadwaita's own colours
    assert "border-radius: 3px;" in text


def test_theme_colours_as_variables_or_named_colours():
    new = themes.css("carbon", None, 4, variables=True)
    assert "--window-bg-color: #141518;" in new and "--accent-bg-color: #ed5b00;" in new
    old = themes.css("carbon", None, 4, variables=False)
    assert "@define-color window_bg_color #141518;" in old
    assert "@define-color accent_color mix(#ed5b00, @window_fg_color, 0.25);" in old
    assert "--" not in colours(old)
    assert "@define-color accent_bg_color #ed5b00;" in new     # for style.css


def test_own_accent_replaces_the_themes():
    text = colours(themes.css("carbon", "teal", 3))
    assert "--accent-bg-color: #2190a4;" in text and "#ed5b00" not in text
    assert "--accent-bg-color: #3a944a;" in themes.css("standard", "green", 3)


def test_every_theme_and_accent_is_complete():
    for key, (name, desc, forced, colors) in themes.THEMES.items():
        assert name and desc and forced in (None, "light", "dark")
        if colors:
            assert set(colors) == {"window", "view", "card", "headerbar",
                                   "popover", "fg", "accent"}
    for name, color in themes.ACCENTS.values():
        assert re.fullmatch(r"#[0-9a-f]{6}", color)


def test_linked_buttons_keep_square_inner_corners():
    text = themes.css(corner=8)
    assert "border-radius: 8px;" in text
    assert ".linked:not(.vertical) > :not(:first-child)" in text


def test_swatches_and_previews_have_colours():
    text = themes.css()
    for key in themes.ACCENTS:
        assert f".accent-swatch.accent-{key} " in text
    for key in themes.THEMES:
        assert f".theme-preview.theme-{key} " in text
