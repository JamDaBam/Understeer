"""Dates written the way the desktop writes them (its locale), the same
everywhere in the app."""
from gi.repository import GLib


def date_text(when):
    """when: a time.time() or a date such as "2026-11-11". A date that
    can't be read is returned as it is."""
    if isinstance(when, str):
        try:
            year, month, day = (int(part) for part in when.split("-"))
            moment = GLib.DateTime.new_local(year, month, day, 0, 0, 0)
        except (ValueError, TypeError):
            moment = None
    else:
        moment = GLib.DateTime.new_from_unix_local(int(when))
    return moment.format("%x") if moment is not None else str(when)
