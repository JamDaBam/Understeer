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


def one_line(text, limit):
    """text as one line of printable characters, at most limit long, e.g.
    a name typed or pasted with line breaks or tabs in it."""
    text = "".join(c for c in " ".join(str(text).split()) if c.isprintable())
    return text[:limit].strip()


def menu_label(name):
    """A name in a menu: short, with its "_" kept (menus read "_" as a
    keyboard shortcut, "__" as "_")."""
    return short(name).replace("_", "__")


def free_name(name, taken):
    """name, or "name (2)", "name (3)", … if taken(name) is true."""
    if not taken(name):
        return name
    n = 2
    while taken(f"{name} ({n})"):
        n += 1
    return f"{name} ({n})"


def join_names(items, limit=TEXT_CHARS):
    """Several names for one line, e.g. the games that use a preset."""
    return ", ".join(short(n, limit) for n in items)
