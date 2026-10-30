"""GTK opt-in workflow with synthetic snapshots, no device writes."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from gtk_layout import DccWindow, core, pump, wait
import dcc_app
from dcc_diagnostics import SessionRecorder


def fixture(percent):
    return {
        "timestamp": "synthetic", "bluetooth_error": None,
        "bluetooth": [{"mac": "02:00:00:00:00:01", "name": "Do not log name",
                       "connected": True, "battery": percent, "battery_source": "BlueZ"}],
        "audio": {"sinks": [], "sources": [],
                  "out": {"percent": 70, "muted": False},
                  "in": {"percent": 31, "muted": False},
                  "bluetooth_profiles": [{"device": "Do not log name", "mode": "A2DP",
                                           "profile": "a2dp-sink", "codec": "SBC"}]},
        "wifi": {"connected": True, "ssid": "private wifi", "signal": 52, "freq": "2412"}}


def main():
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp) / "dcc" / "diagnostics"
        requested_durations = []
        def recorder_factory(duration_seconds=None):
            requested_durations.append(duration_seconds)
            # Scale the finite session for quick, deterministic Xvfb testing.
            duration = 0.25 if duration_seconds is not None else None
            return SessionRecorder(folder, checkpoint_seconds=0, duration_seconds=duration)
        with patch.object(core, "snapshot", return_value=fixture(100)), \
                patch.object(core, "set_volume") as volume, \
                patch.object(core, "set_default_audio") as select, \
                patch.object(dcc_app, "SessionRecorder", side_effect=recorder_factory):
            window = DccWindow()
            try:
                window.show_all()
                wait(lambda: window.data is not None and not window._busy)
                assert window.recorder is None
                assert not folder.exists(), "Disabled recorder must not create state directories"
                window.record_button.clicked()
                wait(lambda: window.recorder.status == "recording")
                assert "التسجيل" in window.record_button.get_label()
                window._updated(fixture(100), None)
                window._updated(fixture(90), None)
                window.record_button.clicked()
                wait(lambda: window.recorder.status == "stopped")
                assert window.recorder.path.is_file()
                content = window.recorder.path.read_text()
                assert "private wifi" not in content
                assert "Do not log name" not in content
                rows = [json.loads(row) for row in content.splitlines()]
                assert rows[0]["type"] == "session_start"
                assert rows[-1]["type"] == "session_end"
                assert any(row.get("field") == "battery" and
                           row["current"]["value"] == 90 for row in rows)
                assert requested_durations == [None]
                wait(lambda: window.record_minutes.get_sensitive())
                assert window._format_duration(65) == "01:05"
                assert window._format_duration(3661) == "01:01:01"
                # A 1-minute choice must reach the backend as 60 seconds.
                window.record_minutes.set_value(1)
                window.record_button.clicked()
                wait(lambda: window.recorder.status == "stopped", timeout=5)
                assert requested_durations == [None, 60], requested_durations
                wait(lambda: window.record_minutes.get_sensitive())
                assert "انتهت المدة" in window.record_state.get_text(), window.record_state.get_text()
                assert window.record_minutes.get_sensitive()
                assert window.record_button.get_sensitive()
                assert window.recorder.stop_reason == "duration_elapsed"
                assert json.loads(window.recorder.path.read_text().splitlines()[-1])["reason"] == "duration_elapsed"
                volume.assert_not_called()
                select.assert_not_called()
            finally:
                window.destroy()
                pump()
    print("PASS opt-in GTK diagnostic recording and privacy; no device writes")


if __name__ == "__main__":
    main()
