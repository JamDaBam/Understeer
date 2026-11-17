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
# PYTHONSAFEPATH: don't look for modules in the current folder first, so an
# "understeer" folder there (e.g. a git checkout) can't take our place.
PYTHONSAFEPATH=1 PYTHONPATH="$HERE/../share/understeer${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m understeer "$@"
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

echo "=== Step 5: Getting the packaging tools ==="
# Pinned build tools (README, "Build tools"): the continuous URLs are moving
# upstream references, the SHA-256 values pin the exact files that were
# checked. A different file stops the build until a maintainer reviews it
# and updates the value here; nothing is ever accepted or replaced by
# itself. The tools run on an x86-64 build machine.
TOOL="appimagetool-x86_64.AppImage"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
TOOL_SHA256="e8c1169c4af66c3184d7ee110713d4be04befe671fa358e2c1591ee840553b03"
RUNTIME="runtime-x86_64"
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
RUNTIME_SHA256="617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46"

# Whether file $1 is a regular, non-empty file (no link) with SHA-256 $2
verified() {
    [ -f "$1" ] && [ ! -L "$1" ] && [ -s "$1" ] &&
        [ "$(sha256sum < "$1" | cut -d' ' -f1)" = "$2" ]
}

# Make sure file $1 is there and has SHA-256 $3, downloading it from $2
# only if it is missing. A download goes to a new temporary file and only
# takes the name $1 once it is checked; an existing file is never replaced.
get_verified() {
    local file="$1" url="$2" sha="$3" part
    if [ -L "$file" ] || { [ -e "$file" ] && [ ! -f "$file" ]; }; then
        echo "ERROR: $file is not a regular file. Move it away and build again." >&2
        exit 1
    fi
    if [ ! -e "$file" ]; then
        part="$(mktemp "$file.download.XXXXXX")"
        if ! wget -q -O "$part" "$url"; then
            echo "ERROR: could not download $url (the incomplete file is $part)." >&2
            exit 1
        fi
        if ! verified "$part" "$sha"; then
            echo "ERROR: $url is not the expected file (SHA-256 $sha), it is" \
                 "not used: $part. If upstream changed it on purpose, review the" \
                 "new file and update the SHA-256 in build.sh." >&2
            exit 1
        fi
        mv -n -- "$part" "$file" || true   # checked again just below
    fi
    if ! verified "$file" "$sha"; then
        echo "ERROR: $file is not the expected file (SHA-256 $sha). It is not" \
             "used or replaced; move it away to download it again." >&2
        exit 1
    fi
}

get_verified "$RUNTIME" "$RUNTIME_URL" "$RUNTIME_SHA256"
get_verified "$TOOL" "$TOOL_URL" "$TOOL_SHA256"
chmod +x "$TOOL"

echo "=== Step 6: Compiling final AppImage package ==="
export APPIMAGE_EXTRACT_AND_RUN=1
export ARCH=x86_64
# Version shown by AppImage managers, e.g. v1.0.3 -> 1.0.3
VERSION="${VERSION:-$(git describe --tags --always 2>/dev/null || echo dev)}"
export VERSION="${VERSION#v}"

# Update information lets tools like Gear Lever find new releases on GitHub.
# It also produces a .zsync file that must be uploaded next to the AppImage.
# Only an official stable release gets it: those tools follow the latest
# stable release and take any other file as an update, so a beta or a
# build from git would be "updated" back to an older stable version.
UPDATE_INFO="gh-releases-zsync|JamDaBam|Understeer|latest|Understeer-*x86_64.AppImage.zsync"
OUTPUT="Understeer-$VERSION-$ARCH.AppImage"
BUILD_KIND="$(python3 tools/release_tag.py --build-kind "$VERSION")"

# Shown in the log file
echo "$VERSION" > AppDir/usr/share/understeer/VERSION

# Both tools are checked again right before the tool runs; the runtime is
# given to it, so it downloads none of its own.
verified "$RUNTIME" "$RUNTIME_SHA256" && verified "$TOOL" "$TOOL_SHA256" ||
    { echo "ERROR: a build tool changed after it was checked." >&2; exit 1; }
if [ "$BUILD_KIND" = "stable" ]; then
    "./$TOOL" --runtime-file "$RUNTIME" -u "$UPDATE_INFO" AppDir "$OUTPUT"
else
    echo "No update information in a $BUILD_KIND build"
    "./$TOOL" --runtime-file "$RUNTIME" AppDir "$OUTPUT"
fi

echo "=== Success: AppImage compilation sequence completed ==="
