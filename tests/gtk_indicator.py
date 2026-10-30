"""GTK control of the *extension*, using mocks and no device writes."""
from unittest.mock import patch
from gtk_layout import DccWindow, core, wait, pump
import dcc_app
import dcc_indicator


def fixture():
    return {"timestamp": "synthetic",
            "bluetooth": [], "bluetooth_error": None,
            "audio": {"sinks": [], "sources": [], "out": {"percent": 58},
                      "in": {"percent": 46}, "bluetooth_profiles": []},
            "wifi": {"connected": False}}


def main():
    states = [dcc_indicator.IndicatorState(True, True, True),
              dcc_indicator.IndicatorState(True, False, False)]
    calls = []
    def read_state():
        calls.append("read")
        return states.pop(0) if states else dcc_indicator.IndicatorState(True, False, False)
    def set_enabled(value):
        calls.append(("set", value))
        return dcc_indicator.IndicatorState(True, value, value)
    with patch.object(core, "snapshot", return_value=fixture()), \
            patch.object(core, "bluetooth_action") as bt, \
            patch.object(core, "set_default_audio") as choose, \
            patch.object(core, "set_volume") as volume, \
            patch.object(dcc_indicator, "read_indicator_state", side_effect=read_state), \
            patch.object(dcc_indicator, "set_indicator_enabled", side_effect=set_enabled):
        w = DccWindow()
        try:
            w.show_all()
            wait(lambda: w._indicator_state is not None and not w._indicator_busy)
            assert w._indicator_state.enabled
            assert "يعمل" in w.indicator_status.get_text()
            assert w.indicator_toggle.get_label() == "إخفاء المؤشر"
            w.indicator_toggle.clicked()
            wait(lambda: w._indicator_state is not None and not w._indicator_busy
                 and not w._indicator_state.enabled)
            assert ("set", False) in calls
            assert "Bluetooth يعمل" in w.indicator_status.get_text()
            assert w.indicator_toggle.get_label() == "إظهار المؤشر"
            w.indicator_toggle.clicked()
            wait(lambda: w._indicator_state is not None and not w._indicator_busy
                 and w._indicator_state.enabled)
            assert ("set", True) in calls
            assert w.indicator_toggle.get_label() == "إخفاء المؤشر"
            w.indicator_refresh.clicked()
            wait(lambda: not w._indicator_busy)
            assert not w._indicator_state.enabled
            assert w.indicator_toggle.get_label() == "إظهار المؤشر"
            bt.assert_not_called()
            choose.assert_not_called()
            volume.assert_not_called()
        finally:
            w.destroy()
            pump()
    print("PASS GNOME extension panel toggle and refresh; Bluetooth and audio untouched")


if __name__ == "__main__":
    main()
