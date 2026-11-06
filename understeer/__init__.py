"""Understeer: tune Fanatec wheel bases through the hid-fanatecff driver.

Start it with  python3 -m understeer
"""
import pathlib

# locale/ and icons/ sit next to the package, both in the source checkout
# and in the AppImage (usr/share/understeer/).
DATA_DIR = pathlib.Path(__file__).resolve().parent.parent
