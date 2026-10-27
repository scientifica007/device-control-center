"""Exercise the actual GTK event loop; hardware queries are read-only.

Run on a desktop or with xvfb-run -a /usr/bin/python3 tests/gtk_smoke.py.
"""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from dcc_app import DccWindow, core
from gi.repository import Gdk, GLib, Gtk


def main():
    assert Gtk.get_major_version() == 3
    assert Gdk.Screen.get_default() is not None
    window = DccWindow()
    window.show_all()
    result = {"loaded": False}

    def inspect():
        if window.data is None:
            return True
        result["loaded"] = True
        assert window.get_mapped()
        assert window.get_direction() == Gtk.TextDirection.RTL
        window.destroy()
        return False

    def expired():
        window.destroy()
        return False

    poll = GLib.timeout_add(100, inspect)
    deadline = GLib.timeout_add_seconds(30, expired)
    Gtk.main()
    if result["loaded"]:
        GLib.source_remove(deadline)
    else:
        GLib.source_remove(poll)
    assert result["loaded"], "Initial hardware snapshot did not reach the UI"

    # Closing during the slider debounce must cancel the hardware write.
    window = DccWindow()
    window.data = {}
    with patch.object(core, "set_volume") as write:
        window.schedule_volume(window.out_scale, "out")
        window.destroy()
        assert all(timer is None for timer in window._volume_timer.values())
        assert window.refresh() is False
        assert window._updated(None, "late worker error") is False
        assert window._action_done("late action", "error", True) is False
        loop = GLib.MainLoop()
        GLib.timeout_add(500, lambda: (loop.quit(), False)[1])
        loop.run()
        write.assert_not_called()
    print("PASS: GTK3 window mapped, real snapshot rendered, RTL and safe close verified.")


if __name__ == "__main__":
    main()
