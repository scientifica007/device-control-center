"""No GTK, Bluetooth, audio hardware or network access required."""
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from dcc_diagnostics import SessionRecorder, _Projector, _changes, state_directory


MAC = "02:00:00:00:00:01"  # synthetic identifier: never written to logs
SSID = "PRIVATE-WIFI-DO-NOT-RECORD"
NAME = "PRIVATE-HEADSET-NAME-DO-NOT-RECORD"


def snapshot(battery=90, connected=True, bterror=False, wifierror=False,
             profile="a2dp-sink", codec="SBC"):
    return {"timestamp": "2026-10-29T21:05:00Z", "bluetooth_error": "private error" if bterror else None,
            "bluetooth": [{"mac": MAC, "name": NAME, "connected": connected,
                           "battery": battery, "battery_source": "BlueZ" if battery is not None else None,
                           "rssi": None, "battery_last_known": {"percent": 100}}],
            "audio": {"sinks": [{"id": 17, "name": NAME, "default": True}],
                      "sources": [{"id": 19, "name": "private mic", "default": True}],
                      "out": {"percent": 70, "muted": False}, "in": {"percent": 31, "muted": False},
                      "bluetooth_profiles": [{"device": NAME, "mode": "A2DP", "profile": profile, "codec": codec}]},
            "wifi": {"connected": True, "ssid": SSID, "signal": 49, "freq": "2462",
                     **({"error": "private wifi exception"} if wifierror else {})}}


class DiagnosticsTests(unittest.TestCase):
    def test_private_projection_has_only_allowed_values(self):
        state = _Projector().snapshot(snapshot())
        text = json.dumps(state)
        for secret in (MAC, SSID, NAME, "private mic", "private error", "battery_last_known"):
            self.assertNotIn(secret, text)
        self.assertIn("bt-1", text)
        self.assertEqual(state["bluetooth"]["devices"]["bt-1"]["battery"]["source_reported_at"], None)

    def test_battery_missing_return_and_source_failures(self):
        projection = _Projector()
        s0 = projection.snapshot(snapshot(100))
        s1 = projection.snapshot(snapshot(90), s0)
        s2 = projection.snapshot(snapshot(None), s1)
        s3 = projection.snapshot(snapshot(100), s2)
        self.assertEqual([s["bluetooth"]["devices"]["bt-1"]["battery"]["status"] for s in (s0,s1,s2,s3)],
                         ["available", "available", "unavailable", "available"])
        self.assertEqual(len(list(_changes(s0,s1))), 1)
        failed = projection.snapshot(snapshot(bterror=True), s3)
        self.assertEqual(failed["bluetooth"]["status"], "error")
        self.assertEqual(failed["bluetooth"]["devices"], s3["bluetooth"]["devices"])
        self.assertFalse(any(e[2] == "device_removed" for e in _changes(s3,failed)))
        restored = projection.snapshot(snapshot(100), failed)
        self.assertFalse(any(e[2] == "device_added" for e in _changes(failed,restored)))

    def test_wifi_error_is_not_a_disconnect(self):
        p = _Projector()
        good = p.snapshot(snapshot())
        bad = p.snapshot(snapshot(wifierror=True), good)
        self.assertEqual(bad["wifi"]["status"], "error")
        self.assertEqual(bad["wifi"]["connection"], "connected")
        self.assertEqual([x[2] for x in _changes(good,bad)], ["status"])

    def test_bad_battery_is_not_exported(self):
        p = _Projector()
        for invalid in (-1, 101, "90", True):
            state = p.snapshot(snapshot(battery=invalid))
            battery = state["bluetooth"]["devices"]["bt-1"]["battery"]
            self.assertEqual(battery["status"], "unavailable")
            self.assertIsNone(battery["value"])

    def test_lifecycle_jsonl_and_privacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "device-control-center" / "diagnostics"
            rec = SessionRecorder(folder, checkpoint_seconds=0)
            self.assertEqual(rec.status, "inactive")
            self.assertFalse(folder.exists())
            rec.start()
            for b in (100, 90, None, 100):
                rec.record(snapshot(battery=b))
            rec.read_error("snapshot")
            rec.stop()
            rec.join(5)
            self.assertEqual(rec.status, "stopped", rec.error)
            self.assertTrue(rec.path.is_file())
            text = rec.path.read_text()
            for secret in (MAC, SSID, NAME, "private mic", "private error"):
                self.assertNotIn(secret, text)
            lines = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(lines[0]["type"], "session_start")
            self.assertEqual(lines[-1]["type"], "session_end")
            self.assertEqual(len([x for x in lines if x["type"] == "checkpoint"]), 4)
            battery_changes = [x for x in lines if x.get("field") == "battery"]
            self.assertEqual([x["current"]["value"] for x in battery_changes], [90,None,100])
            self.assertTrue(all(x["session_id"] == rec.session_id for x in lines))
            self.assertEqual(stat.S_IMODE(rec.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)

    def test_file_budget_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = SessionRecorder(Path(tmp) / "dcc" / "diagnostics", max_bytes=200)
            rec.start()
            rec.record(snapshot())
            rec.stop()
            rec.join(5)
            self.assertEqual(rec.status, "error")
            self.assertIn("OSError", rec.error)
            self.assertLessEqual(rec.path.stat().st_size, 200)

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "private"
            inner.mkdir()
            outer = Path(tmp) / "device-control-center"
            outer.symlink_to(inner, target_is_directory=True)
            rec = SessionRecorder(outer / "diagnostics")
            rec.start()
            rec.join(5)
            self.assertEqual(rec.status, "error")
            self.assertIsNone(rec.path)

    def test_xdg_state_home(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": "/tmp/dcc-xdg-example"}):
            self.assertEqual(state_directory(), Path("/tmp/dcc-xdg-example/device-control-center/diagnostics"))

    def test_retention_removes_expired_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "device-control-center" / "diagnostics"
            path.mkdir(parents=True, mode=0o700)
            path.parent.chmod(0o700)
            path.chmod(0o700)
            stale = path / "dcc-old.jsonl"
            stale.write_text("old")
            os.utime(stale, (time.time() - 8*86400, time.time() - 8*86400))
            rec = SessionRecorder(path)
            rec.start()
            rec.stop()
            rec.join(5)
            self.assertEqual(rec.status, "stopped", rec.error)
            self.assertFalse(stale.exists())


    def test_duration_stops_without_new_device_snapshots(self):
        """The recorder must enforce the limit even if GTK refresh fails or stops."""
        with tempfile.TemporaryDirectory() as tmp:
            recorder = SessionRecorder(Path(tmp) / "dcc" / "diagnostics",
                                       duration_seconds=0.25)
            self.assertEqual(recorder.elapsed_seconds, 0)
            recorder.start()
            recorder.record(snapshot())
            recorder.join(4)
            self.assertEqual(recorder.status, "stopped", recorder.error)
            self.assertEqual(recorder.stop_reason, "duration_elapsed")
            self.assertAlmostEqual(recorder.elapsed_seconds, 0.25, places=2)
            rows = [json.loads(x) for x in recorder.path.read_text().splitlines()]
            self.assertEqual(rows[0]["duration_seconds"], 0.25)
            self.assertEqual(rows[-1]["type"], "session_end")
            self.assertEqual(rows[-1]["reason"], "duration_elapsed")
            before = recorder.path.read_bytes()
            recorder.record(snapshot(battery=10))
            recorder.read_error()
            self.assertEqual(recorder.path.read_bytes(), before)
            stopped_at = recorder.elapsed_seconds
            time.sleep(0.06)
            self.assertEqual(recorder.elapsed_seconds, stopped_at)

    def test_manual_stop_precedes_scheduled_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = SessionRecorder(Path(tmp) / "dcc" / "diagnostics",
                                       duration_seconds=120)
            recorder.start()
            recorder.record(snapshot())
            recorder.stop()
            recorder.join(5)
            self.assertEqual(recorder.status, "stopped", recorder.error)
            self.assertEqual(recorder.stop_reason, "manual")
            lines = [json.loads(x) for x in recorder.path.read_text().splitlines()]
            self.assertEqual(lines[-1]["reason"], "manual")
            self.assertGreaterEqual(recorder.elapsed_seconds, 0)
            self.assertLess(recorder.elapsed_seconds, 120)

    def test_unlimited_duration_does_not_autostop(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = SessionRecorder(Path(tmp) / "dcc" / "diagnostics")
            recorder.start()
            time.sleep(0.15)
            self.assertEqual(recorder.status, "recording", recorder.error)
            recorder.stop()
            recorder.join(5)
            self.assertEqual(recorder.status, "stopped")

    def test_invalid_duration_is_rejected(self):
        for invalid in (0, -1, 86401, True, float("inf"), float("nan"), "60"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    SessionRecorder(duration_seconds=invalid)

    def test_window_close_reason_is_distinct_from_manual_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = SessionRecorder(Path(tmp) / "dcc" / "diagnostics",
                                       duration_seconds=60)
            recorder.start()
            recorder.stop(reason="window_closed")
            recorder.join(5)
            lines = [json.loads(x) for x in recorder.path.read_text().splitlines()]
            self.assertEqual(lines[-1]["reason"], "window_closed")


if __name__ == "__main__":
    unittest.main()
