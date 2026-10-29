"""Exercise rendered current/last-known states without touching devices."""
import copy
from unittest.mock import patch
from gtk_layout import DccWindow, Gtk, core, wait, pump


def snapshot(value, moment):
    return {"bluetooth": [{"mac": "02:00:00:00:00:01", "name": "Synthetic headset",
                           "connected": True, "paired": True, "battery": value,
                           "battery_query_at": moment,
                           "battery_checked_at": moment if value is not None else None,
                           "battery_source": "BlueZ" if value is not None else None}],
            "bluetooth_error": None, "bluetooth_query_at": moment,
            "audio": {"sinks": [], "sources": []}, "wifi": {}, "timestamp": moment}


def descendants(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from descendants(child)


def main():
    t1, t2, t3 = [f"2026-10-29T12:00:0{i}+00:00" for i in (1, 2, 3)]
    with patch.object(core, "snapshot", return_value=snapshot(60, t1)):
        window = DccWindow()
        try:
            window.show_all()
            wait(lambda: window.data is not None and not window._busy)
            assert window.get_mapped()
            initial_labels = [w.get_text() for w in descendants(window.bt_box)
                              if isinstance(w, Gtk.Label)]
            assert any("قوة إشارة Bluetooth (RSSI): غير متاحة حاليًا" in s
                       for s in initial_labels)
            window._render_wifi({"connected": True, "ssid": "Test Network",
                                 "signal": 56, "freq": "2452 MHz"})
            assert "قوة إشارة Wi-Fi: 56%" in window.wifi_info.get_text()
            assert "قوة إشارة Bluetooth" not in window.wifi_info.get_text()
            window._render_wifi({"connected": True, "ssid": "Test Network",
                                 "signal": None, "freq": None})
            assert "قوة إشارة Wi-Fi: غير متاحة" in window.wifi_info.get_text()
            assert "None%" not in window.wifi_info.get_text()
            window._updated(snapshot(None, t2), None)
            pump()
            labels = [w.get_text() for w in descendants(window.bt_box) if isinstance(w, Gtk.Label)]
            assert any("البطارية الحالية: غير متاحة" in s for s in labels)
            assert any("آخر قيمة معروفة (قديمة)" in s and "60%" in s for s in labels)
            assert not any(isinstance(w, Gtk.ProgressBar) for w in descendants(window.bt_box))
            assert not any("آخر قراءة ناجحة" in s for s in labels)
            assert window.data["bluetooth"][0]["battery_last_known"]["observed_at"] == t1
            failed = snapshot(None, t3)
            failed.update(bluetooth=[], bluetooth_error="synthetic failure")
            window._updated(copy.deepcopy(failed), None)
            assert window.data["bluetooth"][0]["battery"] is None
            assert window.data["bluetooth"][0]["battery_last_known"]["observed_at"] == t1
            window._updated(snapshot(50, t3), None)
            pump()
            progress = [w for w in descendants(window.bt_box) if isinstance(w, Gtk.ProgressBar)]
            assert len(progress) == 1 and progress[0].get_fraction() == 0.5
            assert window.data["bluetooth"][0]["battery_last_known"]["percent"] == 50
            fallback = snapshot(42, t3)
            fallback["bluetooth"][0].update(error="BlueZ query failed", battery_source="UPower")
            window._updated(fallback, None)
            assert window.data["bluetooth"][0]["stale"]
            assert window.data["bluetooth"][0]["battery"] == 42
            assert window.data["bluetooth"][0]["battery_source"] == "UPower"
            window._updated(None, "synthetic worker failure")
            assert window.data["bluetooth"][0]["battery"] is None
            assert window.data["bluetooth"][0]["battery_last_known"]["percent"] == 42
        finally:
            window.destroy()
    print("PASS rendered disappearance, stale history, error and return; no hardware changes")


if __name__ == "__main__":
    main()
