#!/bin/bash
set -e

echo "=== Step 1: Cleaning previous build artifacts ==="
rm -rf AppDir

echo "=== Step 2: Creating the AppDir folder structure ==="
APP_ID=io.github.JamDaBam.Understeer
mkdir -p AppDir/usr/bin
mkdir -p AppDir/usr/share/applications
mkdir -p AppDir/usr/share/metainfo
mkdir -p AppDir/usr/share/icons/hicolor/scalable/apps
mkdir -p AppDir/usr/share/icons/hicolor/256x256/apps
mkdir -p AppDir/usr/share/understeer/locale AppDir/usr/share/understeer/icons

echo "=== Step 3: Copying project assets into the layout ==="
# Python package; translations and custom symbolic icons sit next to it,
# just like in the source checkout
cp -r understeer AppDir/usr/share/understeer/
find AppDir -name __pycache__ -prune -exec rm -rf {} +
cp locale/*.po AppDir/usr/share/understeer/locale/
cp -r icons/hicolor AppDir/usr/share/understeer/icons/

# Launcher
cat << 'EOF' > AppDir/usr/bin/understeer
#!/bin/sh
HERE="$(dirname "$(readlink -f "${0}")")"
PYTHONPATH="$HERE/../share/understeer${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m understeer "$@"
EOF
chmod +x AppDir/usr/bin/understeer

# App metadata files
cp $APP_ID.desktop AppDir/
cp $APP_ID.desktop AppDir/usr/share/applications/
# appimagetool only looks for the older .appdata.xml file name
cp $APP_ID.metainfo.xml AppDir/usr/share/metainfo/$APP_ID.appdata.xml
# The app reads its release notes from it, next to the package as in git
cp $APP_ID.metainfo.xml AppDir/usr/share/understeer/
# ... and shows this picture as its tray icon
cp $APP_ID.png AppDir/usr/share/understeer/

# App icon: PNG in the root becomes .DirIcon (must be PNG), SVG for desktops
cp $APP_ID.png AppDir/
cp $APP_ID.png AppDir/usr/share/icons/hicolor/256x256/apps/
cp $APP_ID.svg AppDir/usr/share/icons/hicolor/scalable/apps/

echo "=== Step 4: Creating the core AppRun launch script ==="
cat << 'EOF' > AppDir/AppRun
#!/bin/sh
HERE="$(dirname "$(readlink -f "${0}")")"
exec "$HERE/usr/bin/understeer" "$@"
EOF
chmod +x AppDir/AppRun

echo "=== Step 5: Downloading packaging utilities ==="
# The tool runs on the build machine; ARCH below picks the target runtime.
TOOL="appimagetool-$(uname -m).AppImage"
wget -N "https://github.com/AppImage/appimagetool/releases/download/continuous/$TOOL"
chmod +x "$TOOL"

echo "=== Step 6: Compiling final AppImage package ==="
export APPIMAGE_EXTRACT_AND_RUN=1
export ARCH=x86_64
# Version shown by AppImage managers, e.g. v1.0.3 -> 1.0.3
VERSION="${VERSION:-$(git describe --tags --always 2>/dev/null || echo dev)}"
export VERSION="${VERSION#v}"

# Update information lets tools like Gear Lever find new releases on GitHub.
# It also produces a .zsync file that must be uploaded next to the AppImage.
UPDATE_INFO="gh-releases-zsync|JamDaBam|Understeer|latest|Understeer-*x86_64.AppImage.zsync"

# Shown in the log file
echo "$VERSION" > AppDir/usr/share/understeer/VERSION

"./$TOOL" -u "$UPDATE_INFO" AppDir "Understeer-$VERSION-$ARCH.AppImage"

echo "=== Success: AppImage compilation sequence completed ==="
