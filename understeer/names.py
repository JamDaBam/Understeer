"""Long names cut short for menus, messages and headings, without GTK.

Names of presets, folders, games and slots can be as long as anyone types
them; shown whole, a long one made menus and messages wider than the
screen."""

TEXT_CHARS = 40     # a name inside a menu entry, message or heading


def short(name, limit=TEXT_CHARS):
    """name, cut to limit characters with "…" at the end."""
    name = str(name)
    if len(name) <= limit:
        return name
    return name[:limit - 1].rstrip() + "…"


def join_names(items, limit=TEXT_CHARS):
    """Several names for one line, e.g. the games that use a preset."""
    return ", ".join(short(n, limit) for n in items)
