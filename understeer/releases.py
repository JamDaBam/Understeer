"""The release notes in the metainfo file: for "What's New" in the app and
for the GitHub release.

The <releases> list of the metainfo file is the only place the changes
are written down. Translations are the same element again with xml:lang,
e.g. <li xml:lang="de">. Works without GTK.
"""
import html
import pathlib
import xml.etree.ElementTree as ET

from . import DATA_DIR

METAINFO = DATA_DIR / "io.github.JamDaBam.Understeer.metainfo.xml"
_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def plain_version(version):
    """"v1.8.1" -> "1.8.1"; a version from git like "v1.8.1-3-gabc" stays
    as it is apart from the "v", so it matches no release."""
    version = (version or "").strip()
    return version[1:] if version[:1] == "v" else version


def _release(version, path):
    try:
        root = ET.parse(path or METAINFO).getroot()
    except (OSError, ET.ParseError):
        return None
    for release in root.iterfind("releases/release"):
        if release.get("version") == plain_version(version):
            return release
    return None


def has_notes(version, path=None):
    release = _release(version, path)
    return release is not None and release.find("description") is not None


def _blocks(release, language):
    """[(tag, text or [items])] of the description in language.

    An element counts for a language if it has that xml:lang; elements
    without xml:lang are English and used when nothing is translated.
    """
    description = release.find("description")
    if description is None:
        return []
    wanted = None
    if language and language != "en" and any(
            e.get(_LANG) == language for e in description.iter()):
        wanted = language

    def keep(e):
        return e.get(_LANG) == wanted

    blocks = []
    for e in description:
        if e.tag in ("ul", "ol"):
            items = ["".join(li.itertext()).strip()
                     for li in e.findall("li") if keep(li)]
            if items:
                blocks.append((e.tag, items))
        elif e.tag == "p" and keep(e):
            blocks.append(("p", "".join(e.itertext()).strip()))
    return blocks


def blocks(version, language=None, path=None):
    """[(tag, text or [items])] of the notes of version, e.g.
    [("ul", ["…", "…"])]; [] without notes."""
    release = _release(version, path)
    return _blocks(release, language) if release is not None else []


def release_date(version, path=None):
    """"2026-11-11" or None."""
    release = _release(version, path)
    return release.get("date") if release is not None else None


def versions_since(last, current, path=None):
    """Versions with notes after last up to current, newest first: what is
    new for someone who used last. Just current if last is unknown."""
    try:
        root = ET.parse(path or METAINFO).getroot()
    except (OSError, ET.ParseError):
        return []
    old, new = _number(last) if last else None, _number(current)
    found = []
    for release in root.iterfind("releases/release"):
        version = release.get("version")
        if release.find("description") is None:
            continue
        number = _number(version)
        if version == plain_version(current) or (
                old is not None and new is not None and number is not None
                and old < number <= new):
            found.append((number or (), version))
    return [v for _n, v in sorted(found, reverse=True)]


def latest(path=None):
    """The newest version with notes, e.g. for a build from git."""
    try:
        root = ET.parse(path or METAINFO).getroot()
    except (OSError, ET.ParseError):
        return None
    for release in root.iterfind("releases/release"):
        if release.find("description") is not None:
            return release.get("version")
    return None


def notes(version, language=None, path=None):
    """Notes of version as AppStream markup for Adw.AboutDialog, or None."""
    release = _release(version, path)
    if release is None:
        return None
    parts = []
    for tag, content in _blocks(release, language):
        if tag == "p":
            parts.append(f"<p>{html.escape(content, quote=False)}</p>")
        else:
            items = "".join(f"<li>{html.escape(i, quote=False)}</li>"
                            for i in content)
            parts.append(f"<{tag}>{items}</{tag}>")
    return "".join(parts) or None


def markdown(version, path=None):
    """Notes of version in English as Markdown, for GitHub, or None."""
    release = _release(version, path)
    if release is None:
        return None
    parts = []
    for tag, content in _blocks(release, None):
        if tag == "p":
            parts.append(content)
        else:
            parts.append("\n".join(
                (f"{n}. " if tag == "ol" else "- ") + item
                for n, item in enumerate(content, 1)))
    return "\n\n".join(parts) or None


def _number(version):
    try:
        return tuple(int(n) for n in plain_version(version).split("."))
    except ValueError:
        return None


def should_announce(current, last, used_before, path=None):
    """Whether to say once that Understeer was updated to current.

    last: the version that ran before (None if never recorded);
    used_before: whether Understeer has been used on this computer, so
    updating from a version that didn't record itself still counts.
    Only versions with notes are announced, not ones built from git, and
    not going back to an older version.
    """
    current = plain_version(current)
    if not has_notes(current, path):
        return False
    if last is None:
        return used_before
    old, new = _number(last), _number(current)
    if old is not None and new is not None:
        return new > old
    return plain_version(last) != current


def was_used(config_dir):
    """Whether Understeer has saved anything in config_dir yet."""
    config_dir = pathlib.Path(config_dir)
    return any(config_dir.glob("*.json")) or any(
        (config_dir / "settings").glob("*.json"))
