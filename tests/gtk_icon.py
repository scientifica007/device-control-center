"""Verify the actual DEB icon/desktop identity; run with --package-root DIR.

Extract a built DEB with dpkg-deb -x first. No installation or hardware writes.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.package_root.resolve()
    # Gio validates that Exec can be resolved. Use the extracted launcher,
    # so this test cannot accidentally rely on an older installed package.
    os.environ["PATH"] = str(root / "usr/bin") + os.pathsep + os.environ.get("PATH", "")
    assert Path(shutil.which("device-control-center")) == root / "usr/bin/device-control-center"
    sys.path.insert(0, str(root / "usr/lib/device-control-center"))
    import dcc_app
    from gi.repository import Gdk, Gio, GLib, Gtk

    assert Path(dcc_app.__file__).is_relative_to(root)
    name = "device-control-center"
    desktop = root / f"usr/share/applications/{name}.desktop"
    info = Gio.DesktopAppInfo.new_from_filename(str(desktop))
    assert info is not None and info.should_show()
    assert info.get_icon().to_string() == name
    assert info.get_startup_wm_class() == name
    assert info.get_executable() == name
    assert GLib.get_prgname() == Gdk.get_program_class() == name
    assert len(list((root / "usr/share/applications").glob("*.desktop"))) == 1

    # Resolve against the extracted icon, never the old installed artwork.
    theme = Gtk.IconTheme.new()
    theme.set_search_path([str(root / "usr/share/icons"), "/usr/share/icons", "/usr/share/pixmaps"])
    theme.set_custom_theme("hicolor")
    expected = root / f"usr/share/icons/hicolor/scalable/apps/{name}.svg"
    for size in (16, 24, 32, 48, 128):
        icon = theme.lookup_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        assert Path(icon.get_filename()) == expected
        pixels = icon.load_icon()
        assert (pixels.get_width(), pixels.get_height()) == (size, size)
        print("PASS packaged SVG resolved and rendered:", size, flush=True)

    # Apply only to this process, not to the user's icon theme settings.
    Gtk.IconTheme.get_default().prepend_search_path(str(root / "usr/share/icons"))
    assert Path(Gtk.IconTheme.get_default().lookup_icon(name, 48, 0).get_filename()) == expected
    window = dcc_app.DccWindow()
    try:
        assert window.get_icon_name() == info.get_icon().to_string()
        window.show_all()
        until = time.monotonic() + 1
        while time.monotonic() < until:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(0.01)
        assert window.get_mapped()
        display_kind = type(window.get_display()).__name__
        print("PASS actual GTK window mapped with custom icon:", display_kind, flush=True)
        if "X11" in display_kind and shutil.which("xprop"):
            import gi
            gi.require_version("GdkX11", "3.0")
            from gi.repository import GdkX11
            xid = GdkX11.X11Window.get_xid(window.get_window())
            wm_class = subprocess.check_output(["xprop", "-id", str(xid), "WM_CLASS"], text=True)
            assert wm_class.count('"' + name + '"') == 2, wm_class
            print("PASS X11 WM_CLASS instance and class match desktop", flush=True)
    finally:
        window.destroy()
    print("PASS DEB desktop/icon/window integration; GNOME visual acceptance still required")


if __name__ == "__main__":
    main()
