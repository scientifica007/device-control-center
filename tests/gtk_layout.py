"""Real GTK interaction regression: resize, scroll, activate final action.

No hardware writes. Runs on a desktop or under Xvfb. --live also checks
manual/periodic hardware reads; no identifiers are printed or saved.
"""
import copy
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from dcc_app import DccWindow, Gtk, GLib, Gdk, core, isolate


def pump(seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


def wait(predicate):
    end = time.monotonic() + 35
    while not predicate():
        assert time.monotonic() < end, "GTK update timed out"
        pump(0.05)


def main():
    live = "--live" in sys.argv
    fixture = {"bluetooth": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "جهاز طويل Condor " * 12,
                             "battery": 53, "connected": True, "paired": True}],
               "bluetooth_error": None, "audio": {"sinks": [], "sources": []},
               "wifi": {"connected": True, "ssid": "اسم شبكة طويل Test " * 15}, "timestamp": "fixture"}
    snapshot = core.snapshot if live else lambda: copy.deepcopy(fixture)
    with patch.object(core, "snapshot", wraps=snapshot) as reads, \
            patch.object(core, "set_volume") as writes, \
            patch.object(core, "set_default_audio") as select:
        w = DccWindow()
        try:
            w.show_all()
            wait(lambda: w.data is not None)
            assert w.get_mapped()
            assert w.get_direction() == Gtk.TextDirection.RTL
            for width, height in [(780, 620), (520, 470), (440, 400), (900, 650)]:
                w.resize(width, height)
                pump(0.4)
                assert w.get_size().width <= width, (width, w.get_size())
                w.scrolled.emit("scroll-child", Gtk.ScrollType.END, False)
                pump(0.4)
                adj = w.scrolled.get_vadjustment()
                assert abs(adj.get_value() - (adj.get_upper() - adj.get_page_size())) < 2
                # Hit-test the button in window coordinates, then really activate it.
                x, y = w.export_button.translate_coordinates(w, 0, 0)
                allocation = w.export_button.get_allocation()
                _, status_y = w.status.translate_coordinates(w, 0, 0)
                assert y + allocation.height <= status_y
                assert x >= 0 and x + allocation.width <= w.get_allocated_width()
                seen = []

                def cancel_chooser():
                    for dialog in Gtk.Window.list_toplevels():
                        if isinstance(dialog, Gtk.FileChooserDialog) and dialog.get_mapped():
                            seen.append(True)
                            dialog.response(Gtk.ResponseType.CANCEL)
                            return False
                    return True

                timer = GLib.timeout_add(50, cancel_chooser)
                w.export_button.grab_focus()
                assert w.activate_focus(), "Final action could not be activated"
                wait(lambda: bool(seen))
                print("PASS resize, scroll to end, final button opened chooser:", width, height, flush=True)
            before = reads.call_count
            w.refresh_button.clicked()
            wait(lambda: not w._busy)
            assert reads.call_count > before
            before = reads.call_count
            w.refresh()
            w.refresh()
            wait(lambda: not w._busy)
            assert reads.call_count == before + 1, "Overlapping snapshots"
            if live:
                prior = w.data["timestamp"]
                before = reads.call_count
                wait(lambda: reads.call_count > before and not w._busy)
                assert w.data["timestamp"] != prior
                print("PASS fresh manual and periodic system queries; devices:",
                      [(d.get("name"), d.get("battery"), d.get("battery_source"))
                       for d in w.data["bluetooth"]], flush=True)
                image = Gdk.pixbuf_get_from_window(w.get_window(), 0, 0,
                                                  w.get_allocated_width(), w.get_allocated_height())
                image.savev("/tmp/dcc-after.png", "png", [], [])
            else:
                w._action_running = True
                w._volume_timer["out"] = 999
                assert w._commit_volume("out", 42) is True
                assert w._volume_timer["out"] == 999
                w._volume_timer["out"] = None
                w._action_running = False
                prior = copy.deepcopy(w.data)
                w._updated({**copy.deepcopy(fixture), "bluetooth": [], "bluetooth_error": "temporary failure"}, None)
                assert w.data["bluetooth"][0]["stale"]
                assert w.data["bluetooth"][0]["battery"] == prior["bluetooth"][0]["battery"]
                w._updated({**copy.deepcopy(fixture), "bluetooth": []}, None)
                assert w.data["bluetooth"] == [], "Successful empty query must remove old devices"
            writes.assert_not_called()
            select.assert_not_called()
            assert isolate("AA:BB:CC:DD:EE:FF") == "\u2068AA:BB:CC:DD:EE:FF\u2069"
        finally:
            w.destroy()
            pump()
    print("PASS layout, RTL, read isolation and clean close")


if __name__ == "__main__":
    main()
