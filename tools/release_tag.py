#!/usr/bin/env python3
"""Check the tag of a release and its notes in the metainfo file and say
how to publish it, for the release build, before anything is built:
    python3 tools/release_tag.py v2.4.0-beta.1 >> "$GITHUB_OUTPUT"

Only v1.2.3 (a stable release) and v1.2.3-beta.4 (a test version) are
released; any other tag fails, so nothing is built or published for it.
The metainfo file must have exactly one <release> of that version, with
the real day it is released (YYYY-MM-DD), of type "development" for a
test version and not for a stable one, and with notes. While a test
version is prepared its date may be missing; releasing it then fails.
Prints the lines
    version=2.4.0-beta.1
    kind=beta
    flags=--prerelease --latest=false
where flags are the extra options of "gh release create": a test version
is a prerelease and never becomes the latest release, so the update check
and AppImage tools keep offering the stable one. A stable release keeps
the defaults. The flags are fixed words of this file, never from the tag.
"""
import datetime
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from understeer import releases  # noqa: E402  (no GTK needed)

TAG = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)(-beta\.([0-9]+))?", re.ASCII)

FLAGS = {"stable": (), "beta": ("--prerelease", "--latest=false")}
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)


def release(tag):
    """(version, kind) of a tag, e.g. ("2.4.0-beta.1", "beta"); raises
    ValueError for any tag that is not released."""
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        raise ValueError(f"{tag!r} is no release tag like v1.2.3 or "
                         "v1.2.3-beta.4")
    return tag[1:], "beta" if "-" in tag else "stable"


def check_metainfo(tag, path=None):
    """Raise ValueError unless the metainfo file (path, by default the
    one of this checkout) is ready to release tag."""
    version, kind = release(tag)
    path = pathlib.Path(path or releases.METAINFO)
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        raise ValueError(f"{path.name} can't be read: {e}") from None
    found = [r for r in root.findall("releases/release")
             if r.get("version") == version]
    where = f'<release version="{version}"> in {path.name}'
    if not found:
        raise ValueError(f"there is no {where}")
    if len(found) > 1:
        raise ValueError(f"there are {len(found)} times {where}")
    entry = found[0]
    date = entry.get("date")
    if date is None:
        raise ValueError(f"{where} has no date yet: add the day it is "
                         "released, e.g. date=\"2026-11-20\"")
    try:
        if not DATE.fullmatch(date):
            raise ValueError
        datetime.date.fromisoformat(date)
    except ValueError:
        raise ValueError(f"{where} has the date {date!r}, which is no real "
                         "day like 2026-11-20") from None
    kind_of_entry = entry.get("type")
    if kind == "beta" and kind_of_entry != "development":
        raise ValueError(f'{where} is a test version, so it needs '
                         f'type="development"')
    if kind == "stable" and kind_of_entry not in (None, "stable"):
        raise ValueError(f'{where} is a stable release, not '
                         f'type="{kind_of_entry}"')
    if not releases.markdown(version, path):
        raise ValueError(f"{where} has no notes")


def outputs(tag):
    """The lines for $GITHUB_OUTPUT."""
    version, kind = release(tag)
    return [f"version={version}", f"kind={kind}",
            "flags=" + " ".join(FLAGS[kind])]


def main(args):
    path = None
    if len(args) == 3 and args[0] == "--metainfo":      # for the tests
        path, args = args[1], args[2:]
    if len(args) != 1:
        print("usage: release_tag.py [--metainfo FILE] <tag>", file=sys.stderr)
        return 2
    try:
        check_metainfo(args[0], path)
        lines = outputs(args[0])
    except ValueError as e:
        print(f"Not released: {e}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
