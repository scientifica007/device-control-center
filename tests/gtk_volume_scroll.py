"""GTK regression: wheel/touchpad scrolling over a scale moves the page only.

Runs with Xvfb; all snapshots are synthetic. No audio or Bluetooth writes.
"""
from types import SimpleNamespace
from unittest.mock import patch
from gtk_layout import DccWindow, Gtk, Gdk, core, pump, wait


def event(direction, dy=None):
    args = {"direction": direction}
    if dy is not None:
        args["get_scroll_deltas"] = lambda: (True, 0.0, dy)
    return SimpleNamespace(**args)


def main():
    simulated = {
        "bluetooth": [{"mac": "02:00:00:00:00:01", "name": "Test headset",
                       "battery": 66, "connected": True, "paired": True}],
        "bluetooth_error": None,
        "audio": {
            "sinks": [{"id": 12, "name": "Test output", "default": True}],
            "sources": [{"id": 13, "name": "Test input", "default": True}],
            "out": {"percent": 48, "muted": False},
            "in": {"percent": 31, "muted": False}
        },
        "wifi": {"connected": True, "ssid": "Test", "signal": 56, "freq": "2452"},
        "timestamp": "synthetic"
    }
    with patch.object(core, "snapshot", return_value=simulated), \
            patch.object(core, "set_volume") as volume_writes:
        window = DccWindow()
        try:
            window.show_all()
            wait(lambda: window.data is not None and not window._busy)
            window.resize(440, 400)
            wait(lambda: tuple(window.get_size()) == (440, 400), timeout=5)
            pump()
            adj = window.scrolled.get_vadjustment()
            assert adj.get_upper() - adj.get_page_size() > 100, "Page must scroll"
            assert window.out_scale.get_sensitive()
            assert window.in_scale.get_sensitive()
            original_out = window.out_scale.get_value()
            original_in = window.in_scale.get_value()
            assert original_out == 48
            assert original_in == 31
            upper = adj.get_upper() - adj.get_page_size()

            adj.set_value(0)
            assert window._scroll_over_volume(
                window.out_scale, event(Gdk.ScrollDirection.DOWN))
            assert adj.get_value() > 0
            from_wheel = adj.get_value()

            assert window._scroll_over_volume(
                window.in_scale, event(Gdk.ScrollDirection.SMOOTH, dy=0.5))
            assert adj.get_value() > from_wheel

            before_up = adj.get_value()
            assert window._scroll_over_volume(
                window.out_scale, event(Gdk.ScrollDirection.UP))
            assert adj.get_value() < before_up

            adj.set_value(upper)
            assert window._scroll_over_volume(
                window.in_scale, event(Gdk.ScrollDirection.DOWN))
            assert adj.get_value() <= upper

            adj.set_value(0)
            assert window._scroll_over_volume(
                window.out_scale, event(Gdk.ScrollDirection.UP))
            assert adj.get_value() == 0

            assert window._scroll_over_volume(
                window.in_scale, event(Gdk.ScrollDirection.SMOOTH, dy=-0.25))
            assert adj.get_value() == 0

            assert window._scroll_over_volume(
                window.in_scale, event(Gdk.ScrollDirection.LEFT))
            assert adj.get_value() == 0

            assert window.out_scale.get_value() == original_out
            assert window.in_scale.get_value() == original_in
            assert window._volume_timer == {"out": None, "in": None}
            volume_writes.assert_not_called()
        finally:
            window.destroy()
            pump()
    print("PASS scrolling on both volume controls moves page only; no volume writes")


if __name__ == "__main__":
    main()
