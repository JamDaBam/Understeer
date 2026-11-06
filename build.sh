#!/bin/bash
set -e

echo "=== Step 1: Cleaning previous build artifacts ==="
rm -rf AppDir

echo "=== Step 2: Creating the AppDir folder structure ==="
mkdir -p AppDir/usr/bin
mkdir -p AppDir/usr/share/applications
mkdir -p AppDir/usr/share/metainfo
mkdir -p AppDir/usr/share/icons/hicolor/256x256/apps

echo "=== Step 3: Copying project assets into the layout ==="
# App execution script
cp FFB_Tuner.py AppDir/usr/bin/
chmod +x AppDir/usr/bin/FFB_Tuner.py

# Translations (looked up at usr/share/simpower/locale by the app)
mkdir -p AppDir/usr/share/simpower/locale
cp locale/*.po AppDir/usr/share/simpower/locale/

# App metadata files
cp io.github.benryboi.FanatecLinux.desktop AppDir/
cp io.github.benryboi.FanatecLinux.desktop AppDir/usr/share/applications/
cp io.github.benryboi.FanatecLinux.appdata.xml AppDir/usr/share/metainfo/

# Visual assets (AppImage looks in the root and in the hicolor spec folder)
cp io.github.benryboi.FanatecLinux.png AppDir/
cp io.github.benryboi.FanatecLinux.png AppDir/usr/share/icons/hicolor/256x256/apps/

echo "=== Step 4: Creating the core AppRun launch script ==="
cat << 'EOF' > AppDir/AppRun
#!/bin/sh
HERE="$(dirname "$(readlink -f "${0}")")"
exec python3 "$HERE/usr/bin/FFB_Tuner.py" "$@"
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
UPDATE_INFO="gh-releases-zsync|JamDaBam|FanatecLinux|latest|SimPower-*x86_64.AppImage.zsync"

"./$TOOL" -u "$UPDATE_INFO" AppDir "SimPower-$VERSION-$ARCH.AppImage"

echo "=== Success: AppImage compilation sequence completed ==="
