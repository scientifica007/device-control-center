"""Offline diagnostics analyser GTK workflow with synthetic JSONL, no hardware writes."""
import json
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

from gtk_layout import DccWindow, Gtk, GLib, core, pump, wait
import dcc_analysis
import dcc_app


def snapshot():
    return {
        "timestamp": "synthetic", "bluetooth_error": None, "bluetooth": [],
        "audio": {"sinks": [], "sources": [],
                  "out": {"percent": 56, "muted": False},
                  "in": {"percent": 46, "muted": False}},
        "wifi": {"connected": False},
    }


def fixture(path):
    session = "f" * 32
    rows = [
        {"schema_version": 1, "session_id": session, "type": "session_start",
         "observed_at": "2026-10-30T08:00:00+00:00"},
        {"schema_version": 1, "session_id": session, "type": "checkpoint",
         "observed_at": "2026-10-30T08:00:12+00:00",
         "state": {"wifi": {"status": "available", "signal":
                            {"status": "available", "value": 51}},
                   "audio": {"status": "available", "profiles": []},
                   "bluetooth": {"status": "available", "devices": {}}}},
        {"schema_version": 1, "session_id": session, "type": "session_end",
         "observed_at": "2026-10-30T08:01:00+00:00", "reason": "manual"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def main():
    with tempfile.TemporaryDirectory() as temp:
        reports = Path(temp) / "Device-Control-Center" / "Reports"
        source = Path(temp) / "record.jsonl"
        fixture(source)
        actual_save = dcc_analysis.save_reports
        def fake_save(analysis):
            return actual_save(analysis, reports)
        with patch.object(dcc_analysis, "save_reports", side_effect=fake_save), \
                patch.object(core, "snapshot", return_value=snapshot()), \
                patch.object(core, "set_volume") as volume, \
                patch.object(core, "set_default_audio") as select, \
                patch.object(core, "bluetooth_action") as bt, \
                patch.object(dcc_app.Gio.AppInfo, "launch_default_for_uri") as launch:
            window = DccWindow()
            try:
                window.show_all()
                wait(lambda: window.data is not None and not window._busy)
                assert window.analysis_button.get_sensitive()
                assert not window.analysis_open_button.get_sensitive()
                assert not reports.exists()
                window.analyse_selected_file(source)
                wait(lambda: not window._analysis_busy and window._analysis_html is not None)
                assert window.analysis_button.get_sensitive()
                assert window.analysis_open_button.get_sensitive()
                assert "3 سجلًا" in window.analysis_info.get_text(), window.analysis_info.get_text()
                self_html = window._analysis_html
                self_json = self_html.with_suffix(".json")
                assert self_html.exists() and self_json.exists()
                assert stat.S_IMODE(self_html.stat().st_mode) == 0o600
                assert json.loads(self_json.read_text())["counts"]["records"] == 3
                window.analysis_open_button.clicked()
                launch.assert_called_once_with(self_html.as_uri(), None)
                # Invalid file must not destroy the application or enable an old report.
                source.write_text("not json\n")
                window.analyse_selected_file(source)
                wait(lambda: not window._analysis_busy)
                assert not window.analysis_open_button.get_sensitive()
                assert "تعذّر التحليل" in window.analysis_info.get_text()
                volume.assert_not_called()
                select.assert_not_called()
                bt.assert_not_called()
            finally:
                window.destroy()
                pump()
    print("PASS local JSONL GTK analysis, private HTML/JSON, browser open, errors and no device writes")


if __name__ == "__main__":
    main()
