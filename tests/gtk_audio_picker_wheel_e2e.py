"""End-to-end X11/Xvfb wheel injection over the *actual* audio picker.

Unlike emitting scroll-event ourselves, xdotool uses XTEST to deliver a real
pointer wheel event through the widget's input window and GTK event routing.
No Bluetooth or audio commands are executed: both are patched.
"""
import subprocess
from unittest.mock import patch
from gtk_layout import DccWindow, Gtk, core, pump, wait


def wheel_at(picker, window, direction="5"):
    # Gdk.Window's origin is in root coordinates under X11 / Xvfb.
    # PyGObject returns (success, x, y) for get_origin on Ubuntu 24.04.
    origin_x, origin_y = window.get_window().get_origin()[-2:]
    x, y = picker.translate_coordinates(
        window, picker.get_allocated_width() // 2,
        picker.get_allocated_height() // 2)
    # --sync may hang in Xvfb if the pointer is already at the target.\n    # The following click still travels through real XTEST/Gdk routing.\n    subprocess.run(["xdotool", "mousemove",
                    str(origin_x + x), str(origin_y + y),
                    "click", "--clearmodifiers", direction],
                   check=True, timeout=5)
    pump(0.4)


def main():
    fixture = {
        "bluetooth": [{"mac": "02:00:00:00:00:01", "name": "Test headphone",
                       "connected": True, "paired": True, "battery": 60}],
        "bluetooth_error": None,
        "audio": {
            "sinks": [{"id": 10, "name": "Speaker", "default": True},
                      {"id": 11, "name": "Headphones", "default": False}],
            "sources": [{"id": 20, "name": "Internal", "default": True},
                        {"id": 21, "name": "External", "default": False}],
            "out": {"percent": 48, "muted": False},
            "in": {"percent": 31, "muted": False},
        },
        "wifi": {"connected": True, "ssid": "Test", "signal": 56, "freq": "2452"},
        "timestamp": "fixture",
    }
    with patch.object(core, "snapshot", return_value=fixture), \
            patch.object(core, "set_default_audio") as audio_writes, \
            patch.object(core, "set_volume") as volume_writes:
        window = DccWindow()
        try:
            window.move(60, 60)
            window.resize(780, 470)
            window.show_all()
            wait(lambda: window.data is not None and not window._busy)
            pump(0.3)
            adj = window.scrolled.get_vadjustment()
            assert adj.get_upper() - adj.get_page_size() > 150

            for picker in (window.out_combo, window.in_combo):
                assert isinstance(picker, Gtk.MenuButton)
                # Make the closed picker visible in the scrolled viewport,
                # leaving enough room to continue scrolling downward.
                # Use the target's *current window coordinates*, not the
                # coordinates relative to GtkViewport. After the first wheel
                # test the viewport is already scrolled, so its child position
                # cannot be interpreted as an unscrolled document offset.
                maximum = adj.get_upper() - adj.get_page_size()
                _, scroller_y = window.scrolled.translate_coordinates(window, 0, 0)
                desired_y = scroller_y + window.scrolled.get_allocated_height() * 0.5
                _, current_y = picker.translate_coordinates(
                    window, 0, picker.get_allocated_height() // 2)
                target = adj.get_value() + current_y - desired_y
                adj.set_value(max(0, min(maximum - 35, target)))
                pump(0.3)
                pos = picker.translate_coordinates(
                    window, picker.get_allocated_width() // 2,
                    picker.get_allocated_height() // 2)
                assert pos is not None
                # Ensure we're aiming at the visible part of the main scroller,
                # not below its viewport or onto the fixed footer.
                _, scroller_y = window.scrolled.translate_coordinates(window, 0, 0)
                assert scroller_y < pos[1] < scroller_y + window.scrolled.get_allocated_height()
                before = adj.get_value()
                selected = picker.get_active_id()
                wheel_at(picker, window)
                after = adj.get_value()
                assert after > before + 1, (
                    "Wheel did not scroll page over audio picker", selected, before, after)
                assert picker.get_active_id() == selected, (
                    "Wheel unexpectedly changed audio device")
                assert window.out_scale.get_value() == 48
                assert window.in_scale.get_value() == 31

            audio_writes.assert_not_called()
            volume_writes.assert_not_called()
        finally:
            window.destroy()
            pump()
    print("PASS: real XTEST mouse wheel over both closed pickers scrolls the page")


if __name__ == "__main__":
    main()
