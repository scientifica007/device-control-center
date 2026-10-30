#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist"
if [ "$#" -gt 0 ]; then OUT="$1"; fi
PKG_DIR="$(mktemp -d)"
trap 'rm -rf "$PKG_DIR"' EXIT
mkdir -p "$PKG_DIR/DEBIAN" "$PKG_DIR/usr/bin" "$PKG_DIR/usr/lib/device-control-center" "$PKG_DIR/usr/share/applications" "$PKG_DIR/usr/share/icons/hicolor/scalable/apps" "$PKG_DIR/usr/share/doc/device-control-center"
cat > "$PKG_DIR/DEBIAN/control" <<'CONTROL'
Package: device-control-center
Version: 0.1.13
Section: utils
Priority: optional
Architecture: all
Maintainer: Device Control Center <maintainers@localhost.invalid>
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, librsvg2-common, bluez, upower, wireplumber, network-manager
Description: Arabic desktop hub for Bluetooth battery, audio and Wi-Fi
 Shows Bluetooth devices and their reported battery levels,
 controls default audio input and output, and presents Wi-Fi status.
 Designed for Ubuntu 24.04 LTS. All readings stay local.
CONTROL
install -m 0644 "$ROOT/app/dcc_core.py" "$PKG_DIR/usr/lib/device-control-center/dcc_core.py"
install -m 0644 "$ROOT/app/dcc_app.py" "$PKG_DIR/usr/lib/device-control-center/dcc_app.py"
install -m 0644 "$ROOT/app/dcc_diagnostics.py" "$PKG_DIR/usr/lib/device-control-center/dcc_diagnostics.py"
cat > "$PKG_DIR/usr/bin/device-control-center" <<'LAUNCH'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/device-control-center/dcc_app.py "$@"
LAUNCH
chmod 0755 "$PKG_DIR/usr/bin/device-control-center"
install -m 0644 "$ROOT/packaging/device-control-center.desktop" "$PKG_DIR/usr/share/applications/device-control-center.desktop"
install -m 0644 "$ROOT/packaging/device-control-center.svg" "$PKG_DIR/usr/share/icons/hicolor/scalable/apps/device-control-center.svg"
install -m 0644 "$ROOT/README.md" "$PKG_DIR/usr/share/doc/device-control-center/README.md"
install -m 0644 "$ROOT/LICENSE" "$PKG_DIR/usr/share/doc/device-control-center/copyright"
find "$PKG_DIR" -type d -exec chmod 755 {} \;
mkdir -p "$OUT"
VERSION="$(sed -n 's/^Version: //p' "$PKG_DIR/DEBIAN/control")"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid package version: $VERSION" >&2
    exit 1
fi
ARTIFACT="$OUT/device-control-center_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$PKG_DIR" "$ARTIFACT"
echo "Artifact: $ARTIFACT"
