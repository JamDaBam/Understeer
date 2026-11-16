#!/usr/bin/env python3
"""Check the tag of a release and say how to publish it, for the release
build, before anything is built:
    python3 tools/release_tag.py v2.4.0-beta.1 >> "$GITHUB_OUTPUT"

Only v1.2.3 (a stable release) and v1.2.3-beta.4 (a test version) are
released; any other tag fails, so nothing is built or published for it.
Prints the lines
    version=2.4.0-beta.1
    kind=beta
    flags=--prerelease --latest=false
where flags are the extra options of "gh release create": a test version
is a prerelease and never becomes the latest release, so the update check
and AppImage tools keep offering the stable one. A stable release keeps
the defaults. The flags are fixed words of this file, never from the tag.
"""
import re
import sys

TAG = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)(-beta\.([0-9]+))?", re.ASCII)

FLAGS = {"stable": (), "beta": ("--prerelease", "--latest=false")}


def release(tag):
    """(version, kind) of a tag, e.g. ("2.4.0-beta.1", "beta"); raises
    ValueError for any tag that is not released."""
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        raise ValueError(f"{tag!r} is no release tag like v1.2.3 or "
                         "v1.2.3-beta.4")
    return tag[1:], "beta" if "-" in tag else "stable"


def outputs(tag):
    """The lines for $GITHUB_OUTPUT."""
    version, kind = release(tag)
    return [f"version={version}", f"kind={kind}",
            "flags=" + " ".join(FLAGS[kind])]


def main(args):
    if len(args) != 1:
        print("usage: release_tag.py <tag>", file=sys.stderr)
        return 2
    try:
        lines = outputs(args[0])
    except ValueError as e:
        print(f"Not released: {e}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
