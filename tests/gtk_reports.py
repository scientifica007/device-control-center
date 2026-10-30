"""Real GTK report-folder UI with synthetic device data and no hardware writes."""
import json
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

from gtk_layout import DccWindow, Gtk, GLib, core, pump, wait
import dcc_app
import dcc_diagnostics


def fixture():
    return {
        "timestamp": "synthetic",
        "bluetooth": [{"mac": "02:00:00:00:00:01", "name": "Private headset",
                       "connected": True, "battery": 90, "battery_source": "BlueZ"}],
        "bluetooth_error": None,
        "audio": {"sinks": [], "sources": [],
                  "out": {"percent": 58, "muted": False},
                  "in": {"percent": 46, "muted": False}},
        "wifi": {"connected": True, "ssid": "Private Wifi", "signal": 55,
                 "freq": "2447 MHz"},
    }


def main():
    with tempfile.TemporaryDirectory() as temp:
        reports = Path(temp) / "Documents" / "Device-Control-Center" / "Reports"
        with patch.object(dcc_diagnostics, "reports_directory", return_value=reports), \
                patch.object(dcc_app, "launch_local_folder") as launch, \
                patch.object(core, "snapshot", return_value=fixture()), \
                patch.object(core, "set_volume") as volume, \
                patch.object(core, "set_default_audio") as choose:
            win = DccWindow()
            try:
                win.show_all()
                wait(lambda: win.data is not None and not win._busy)
                assert not reports.exists(), "No directory created before user action"
                win.reports_button.clicked()
                launch.assert_called_once_with(reports)
                assert reports.is_dir()
                assert stat.S_IMODE(reports.stat().st_mode) == 0o700

                selected = []

                def save_report_in_default():
                    for dialog in Gtk.Window.list_toplevels():
                        if isinstance(dialog, Gtk.FileChooserDialog) and dialog.get_mapped():
                            assert Path(dialog.get_current_folder()) == reports
                            filename = Path(dialog.get_filename())
                            assert filename.parent == reports
                            assert filename.name.startswith("device-control-report-")
                            selected.append(filename)
                            dialog.response(Gtk.ResponseType.OK)
                            return False
                    return True

                GLib.timeout_add(40, save_report_in_default)
                win.export_button.clicked()
                assert len(selected) == 1
                saved = selected[0]
                assert saved.is_file()
                assert stat.S_IMODE(saved.stat().st_mode) == 0o600
                assert json.loads(saved.read_text())["wifi"]["ssid"] == "Private Wifi"

                win.record_button.clicked()
                wait(lambda: win.recorder.status == "recording")
                win._updated(fixture(), None)
                win.record_button.clicked()
                wait(lambda: win.recorder.status == "stopped")
                diagnostic = win.recorder.path
                assert diagnostic.parent == reports
                assert diagnostic.suffix == ".jsonl"
                assert diagnostic.is_file()
                assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o600
                assert "Private Wifi" not in diagnostic.read_text()
                assert "Private headset" not in diagnostic.read_text()
                assert "02:00:00:00:00:01" not in diagnostic.read_text()
                assert json.loads(diagnostic.read_text().splitlines()[1])["state"]["wifi"]["frequency_mhz"] == 2447
                assert saved.exists(), "Diagnostics cleanup must not remove snapshot JSON"
                volume.assert_not_called()
                choose.assert_not_called()
            finally:
                win.destroy()
                pump()
    print("PASS shared Documents reports folder, safe JSON/JSONL and open-folder UI; no hardware writes")


if __name__ == "__main__":
    main()
