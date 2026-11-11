"""Understeer: tune Fanatec wheel bases through the hid-fanatecff driver.

Start it with  python3 -m understeer
"""
import pathlib

# locale/ and icons/ sit next to the package, both in the source checkout
# and in the AppImage (usr/share/understeer/).
DATA_DIR = pathlib.Path(__file__).resolve().parent.parent

APP_ID = "io.github.JamDaBam.Understeer"
APP_ICON = DATA_DIR / f"{APP_ID}.png"   # as a picture: tray, notifications
USER_AGENT = "Understeer"               # asking Steam, SteamGridDB, GitHub
