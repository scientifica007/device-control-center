"""GTK/GDK startup regression smoke test in a virtual X11 display.

Run with: xvfb-run -a /usr/bin/python3 tests/gtk_smoke.py
This test imports the *actual application* and creates its window.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from dcc_app import DccWindow  # noqa: E402
from gi.repository import Gdk, Gtk  # noqa: E402


def main():
    assert Gtk.get_major_version() == 3, "GTK must be version 3"
    assert Gdk.Screen.get_default() is not None, "The smoke test needs a display"
    window = DccWindow()
    window.show_all()
    assert window.get_visible(), "Main window did not become visible"
    window.hide()
    print("PASS: GTK3/GDK3 imported, main window constructed, shown and hidden.")


if __name__ == "__main__":
    main()
