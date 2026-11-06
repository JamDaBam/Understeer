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
mkdir -p AppDir/usr/share/understeer/locale AppDir/usr/share/understeer/icons

echo "=== Step 3: Copying project assets into the layout ==="
# App execution script
cp understeer.py AppDir/usr/bin/
chmod +x AppDir/usr/bin/understeer.py

# Translations and custom symbolic icons (found by the app under usr/share/understeer)
cp locale/*.po AppDir/usr/share/understeer/locale/
cp icons/*.svg AppDir/usr/share/understeer/icons/

# App metadata files
cp $APP_ID.desktop AppDir/
cp $APP_ID.desktop AppDir/usr/share/applications/
cp $APP_ID.metainfo.xml AppDir/usr/share/metainfo/

# App icon (AppImage looks in the root and in the hicolor spec folder)
cp $APP_ID.svg AppDir/
cp $APP_ID.svg AppDir/usr/share/icons/hicolor/scalable/apps/

echo "=== Step 4: Creating the core AppRun launch script ==="
cat << 'EOF' > AppDir/AppRun
#!/bin/sh
HERE="$(dirname "$(readlink -f "${0}")")"
exec python3 "$HERE/usr/bin/understeer.py" "$@"
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
UPDATE_INFO="gh-releases-zsync|JamDaBam|FanatecLinux|latest|Understeer-*x86_64.AppImage.zsync"

"./$TOOL" -u "$UPDATE_INFO" AppDir "Understeer-$VERSION-$ARCH.AppImage"

echo "=== Success: AppImage compilation sequence completed ==="
