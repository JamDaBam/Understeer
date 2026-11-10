#!/usr/bin/env python3
"""Print the notes of a release from the metainfo file, as Markdown.

Used by the release build for the GitHub release:
    python3 tools/release_notes.py v1.9.0
Fails if the metainfo file has no notes for that version, so a release
can't be built before they are written.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from understeer import releases  # noqa: E402  (no GTK needed)


def main(args):
    if len(args) != 1:
        print("usage: release_notes.py <version>", file=sys.stderr)
        return 2
    text = releases.markdown(args[0])
    if not text:
        print(f"No notes for {releases.plain_version(args[0])} in "
              f"{releases.METAINFO.name}: add a <release> to its <releases>.",
              file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
