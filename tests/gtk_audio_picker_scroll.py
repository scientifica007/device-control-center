"""GTK regression: audio device pickers must not hijack page scrolling.

Uses synthetic devices and simulated GDK scroll events. No audio changes.
"""
from unittest.mock import patch
from gtk_layout import DccWindow, Gtk, Gdk, core, pump, wait


def gdk_scroll(widget, direction):
    event = Gdk.Event.new(Gdk.EventType.SCROLL)
    event.direction = direction
    event.window = widget.get_window()
    return event


def main():
    fixture = {
        "bluetooth": [{"mac": "02:00:00:00:00:01", "name": "Synthetic headset",
                       "connected": True, "paired": True, "battery": 40}],
        "bluetooth_error": None,
        "audio": {
            "sinks": [{"id": 10, "name": "Speakers", "default": True},
                      {"id": 11, "name": "Headphones", "default": False}],
            "sources": [{"id": 20, "name": "Internal microphone", "default": True},
                        {"id": 21, "name": "External microphone", "default": False}],
            "out": {"percent": 48, "muted": False},
            "in": {"percent": 31, "muted": False}
        },
        "wifi": {"connected": True, "ssid": "Test", "signal": 56, "freq": "2452"},
        "timestamp": "fixture"
    }
    with patch.object(core, "snapshot", return_value=fixture), \
            patch.object(core, "set_default_audio") as audio_changes, \
            patch.object(core, "set_volume") as volume_changes:
        window = DccWindow()
        try:
            window.show_all()
            wait(lambda: window.data is not None and not window._busy)
            window.resize(440, 400)
            wait(lambda: tuple(window.get_size()) == (440, 400), timeout=5)
            pump()
            adj = window.scrolled.get_vadjustment()
            maximum = adj.get_upper() - adj.get_page_size()
            assert maximum > 100
            assert window.out_combo.get_sensitive()
            assert window.in_combo.get_sensitive()
            assert isinstance(window.out_combo, Gtk.MenuButton)
            assert isinstance(window.in_combo, Gtk.MenuButton)
            original_out = window.out_combo.get_active_id()
            original_in = window.in_combo.get_active_id()
            assert original_out == "10"
            assert original_in == "20"
            original_volume_out = window.out_scale.get_value()
            original_volume_in = window.in_scale.get_value()

            for combo in (window.out_combo, window.in_combo):
                adj.set_value(0)
                assert combo.emit("scroll-event", gdk_scroll(combo, Gdk.ScrollDirection.DOWN))
                assert adj.get_value() > 0, "Wheel must move outer page"
                lower = adj.get_value()
                assert combo.emit("scroll-event", gdk_scroll(combo, Gdk.ScrollDirection.UP))
                assert adj.get_value() < lower, "Wheel up must move outer page"
                assert combo.get_active_id() == (
                    original_out if combo is window.out_combo else original_in)

            # Direct callback verifies touchpad smooth delta routing as well.
            for combo in (window.out_combo, window.in_combo):
                adj.set_value(0)
                class Smooth:
                    direction = Gdk.ScrollDirection.SMOOTH
                    def get_scroll_deltas(self):
                        return True, 0.0, 0.75
                assert window._scroll_over_volume(combo, Smooth())
                assert adj.get_value() > 0

            assert window.out_combo.get_active_id() == original_out
            assert window.in_combo.get_active_id() == original_in
            assert window.out_scale.get_value() == original_volume_out
            assert window.in_scale.get_value() == original_volume_in
            audio_changes.assert_not_called()
            volume_changes.assert_not_called()
            # Explicitly clicking an alternative remains possible; only then
            # should the app be allowed to request a new default audio device.
            window.out_combo._choices.get_children()[1].clicked()
            wait(lambda: audio_changes.call_count == 1, timeout=5)
            assert audio_changes.call_args.args[0] == 11
        finally:
            window.destroy()
            pump()
    print("PASS scrolling over both audio pickers preserves devices and volume")


if __name__ == "__main__":
    main()
