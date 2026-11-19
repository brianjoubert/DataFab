#!/usr/bin/env bash
# Build dist/datafab_<version>_amd64.deb from the PyInstaller folder dist/datafab.
set -euo pipefail
VERSION="${1:?usage: build_deb.sh <version>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="build/deb/datafab_${VERSION}_amd64"

rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" "$ROOT/opt" "$ROOT/usr/bin" "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/256x256/apps" "$ROOT/usr/lib/udev/rules.d" \
         "$ROOT/usr/share/doc/datafab"

cp -a dist/datafab "$ROOT/opt/datafab"
ln -s /opt/datafab/datafab "$ROOT/usr/bin/datafab"
install -m 644 "$HERE/datafab.desktop" "$ROOT/usr/share/applications/datafab.desktop"
install -m 644 build/icons/datafab.png "$ROOT/usr/share/icons/hicolor/256x256/apps/datafab.png"
install -m 644 "$HERE/60-datafab-keithley.rules" "$ROOT/usr/lib/udev/rules.d/60-datafab-keithley.rules"
install -m 644 README.md "$ROOT/usr/share/doc/datafab/README.md"

SIZE_KB=$(du -sk --exclude=DEBIAN "$ROOT" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: datafab
Version: ${VERSION}
Section: science
Priority: optional
Architecture: amd64
Maintainer: DataFab <noreply@github.com>
Installed-Size: ${SIZE_KB}
Depends: libc6 (>= 2.34), libgl1, libegl1, libfontconfig1, libfreetype6, libxkbcommon0, libxkbcommon-x11-0, libdbus-1-3, libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libxcb-xkb1, libusb-1.0-0
Homepage: https://github.com/brianjoubert/DataFab
Description: Wafer database generator and resistance measurement
 DataFab reads a GDS layout, labels the dies and builds a JSON measurement
 database, then shows an interactive wafer map where resistances can be typed
 in or measured live with a Keithley 2450 source-meter (delta method).
CONTROL

cat > "$ROOT/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then udevadm control --reload-rules || true; udevadm trigger --subsystem-match=usb || true; fi
if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database -q /usr/share/applications || true; fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true; fi
exit 0
POST
cp "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/postrm"
chmod 755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/postrm"

mkdir -p dist
dpkg-deb --build --root-owner-group "$ROOT" "dist/datafab_${VERSION}_amd64.deb"
echo "Built dist/datafab_${VERSION}_amd64.deb"
