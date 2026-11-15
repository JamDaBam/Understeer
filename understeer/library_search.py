"""Searching and filtering the presets of the community catalog, without
GTK: all on the catalog already loaded, nothing goes to the network."""
import unicodedata
from collections import namedtuple

from . import compatibility

# text: the search; game, wheelbase, tag: a value of options() or None for
# all; only_mine: only presets made on the model of the connected wheelbase
Filters = namedtuple("Filters", "text game wheelbase tag only_mine",
                     defaults=("", None, None, None, False))
NONE = Filters()
Options = namedtuple("Options", "games wheelbases tags")


def fold(text):
    """text to compare without case, also for other scripts: "Straße"
    finds "STRASSE"."""
    return unicodedata.normalize("NFKC", text or "").casefold()


def _choices(texts):
    """The texts once each (equal apart from case counts once, as first
    spelled), sorted without case."""
    seen = {}
    for text in texts:
        text = (text or "").strip()
        if text and fold(text) not in seen:
            seen[fold(text)] = text
    return sorted(seen.values(), key=lambda t: (fold(t), t))


def options(entries):
    """What the game, wheelbase and tag filters can choose from."""
    return Options(_choices(e.meta.game for e in entries),
                   _choices(e.meta.wheelbase for e in entries),
                   _choices(t for e in entries for t in e.meta.tags))


def keep(filters, available):
    """filters without choices that are no longer in available (Options),
    e.g. a game gone after the catalog was loaded again."""
    def still(value, choices):
        return value if value is not None and fold(value) in \
            {fold(c) for c in choices} else None
    return filters._replace(game=still(filters.game, available.games),
                            wheelbase=still(filters.wheelbase, available.wheelbases),
                            tag=still(filters.tag, available.tags))


def haystack(entry):
    """All the text of an entry the search looks in, folded once."""
    meta = entry.meta
    return fold("\n".join(t for t in (entry.name, meta.author, meta.game,
                                      meta.wheelbase, meta.description,
                                      *meta.tags) if t))


def matches(entry, filters, wheelbase=None, text=None):
    """Whether entry passes filters; wheelbase: the connected one
    (compatibility.Device) for only_mine; text: haystack(entry), if
    already made."""
    if filters.text.strip():
        text = haystack(entry) if text is None else text
        if not all(word in text for word in fold(filters.text).split()):
            return False
    if filters.game is not None and fold(entry.meta.game) != fold(filters.game):
        return False
    if filters.wheelbase is not None and \
            fold(entry.meta.wheelbase) != fold(filters.wheelbase):
        return False
    if filters.tag is not None and fold(filters.tag) not in \
            {fold(t) for t in entry.meta.tags}:
        return False
    if filters.only_mine and compatibility.classify(
            entry.meta, wheelbase).level != compatibility.SAME:
        return False
    return True


def apply(entries, filters, wheelbase=None):
    """The entries passing filters, in their order."""
    return [e for e in entries if matches(e, filters, wheelbase)]


def active(filters):
    """Whether filters leave anything out."""
    return bool(filters.text.strip()) or filters._replace(text="") != NONE
