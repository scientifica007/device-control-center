"""Unit and evidence-shape tests for offline v0.1.15 analysis; no personal files in Git."""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from dcc_analysis import AnalysisError, analyse_file, render_html, save_reports

SID = "a" * 32
START = datetime(2026, 10, 30, 13, 55, 52, tzinfo=timezone.utc)


def row(kind, seconds, **extra):
    return {"schema_version": 1, "session_id": SID, "type": kind,
            "observed_at": (START + timedelta(seconds=seconds)).isoformat(), **extra}


def checkpoint(seconds=3, signal=52, battery=100, error=False):
    return row("checkpoint", seconds, state={
        "wifi": {"status": "available", "signal": {"status": "available",
                  "value": signal, "unit": "percent"}},
        "bluetooth": {"status": "available", "devices": {
            "bt-1": {"battery": {"status": "available", "value": battery,
                                 "source": "BlueZ", "source_reported_at": None}}}},
        "audio": {"status": "error" if error else "available", "profiles": [
            {"device_id": "audio-1", "profile": "a2dp-sink",
             "mode": "A2DP", "codec": "SBC"}]}})


def fixture(n=5, end=True):
    data = [row("session_start", 0, duration_seconds=None), checkpoint()]
    for i in range(n):
        data.append(row("state_change", 15 + i*12, category="wifi", field="signal",
                        previous={"status": "available", "value": 52+i%4},
                        current={"status": "available", "value": 53+i%4}))
    data.append(row("state_change", 120, category="bluetooth", field="battery",
                    device_id="bt-1", previous={"status": "available", "value": 100},
                    current={"status": "available", "value": 90}))
    if end:
        data.append(row("session_end", 130, reason="manual"))
    return data


def write_rows(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


class AnalysisTests(unittest.TestCase):
    def test_summary_and_stats_from_known_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.jsonl"
            write_rows(source, fixture())
            result = analyse_file(source)
            self.assertEqual(result["counts"]["records"], 9)
            self.assertEqual(result["counts"]["events_by_category"], {"wifi": 5, "bluetooth": 1})
            self.assertEqual(result["counts"]["types"]["checkpoint"], 1)
            self.assertEqual(result["session"]["duration_seconds"], 130)
            self.assertTrue(result["session"]["ended_normally"])
            self.assertEqual(result["battery"]["bt-1"]["values_observed"], [90, 100])
            self.assertEqual(result["audio"]["observed_profiles"][0]["codec"], "SBC")
            self.assertEqual(result["wifi"]["observations"], 6)
            self.assertIn("not_time_weighted", result["wifi"]["mean_method"])
            self.assertEqual(len(result["timeline"]["wifi"]), 6)

    def test_long_recording_shape_25_checkpoints_516_events(self):
        records = [row("session_start", 0)]
        for i in range(25):
            records.append(checkpoint(i * 300 + 2))
        for i in range(502):
            records.append(row("state_change", i*15 + 3, category="wifi", field="signal",
                               current={"status": "available", "value": 52 + i%5}))
        for i in range(8):
            records.append(row("state_change", 100 + i * 900, category="bluetooth",
                               field="battery", device_id="bt-1",
                               current={"status": "available", "value": 90 if i%2 else 100}))
        for i in range(6):
            records.append(row("state_change", 120 + i * 900, category="audio",
                               field="out", current={"percent": 70-i*2}))
        records.append(row("session_end", 7600))
        records.sort(key=lambda r: r["observed_at"])
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "long.jsonl"
            write_rows(source, records)
            result = analyse_file(source)
            self.assertEqual(result["counts"]["records"], 543)
            self.assertEqual(result["counts"]["types"]["state_change"], 516)
            self.assertEqual(result["counts"]["events_by_category"],
                             {"wifi": 502, "bluetooth": 8, "audio": 6})
            self.assertEqual(result["counts"]["types"]["checkpoint"], 25)
            self.assertLessEqual(len(result["timeline"]["wifi"]), 250)
            self.assertEqual(result["session"]["duration_seconds"], 7600)

    def test_privacy_allowlist_in_html_json(self):
        secret = "MY_PRIVATE_WIFI_AND_HEADSET"
        raw = fixture()
        raw[1]["state"]["wifi"]["ssid"] = secret
        raw[1]["state"]["bluetooth"]["devices"]["bt-1"]["name"] = secret
        raw[2]["previous"]["ssid"] = secret
        raw.insert(-1, row("read_error", 125, category=secret, message=secret))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "DCC" / "Reports"
            folder.parent.mkdir(mode=0o700)
            source = Path(tmp) / "sensitive.jsonl"
            write_rows(source, raw)
            result = analyse_file(source)
            json_path, html_path = save_reports(result, folder)
            for p in (json_path, html_path):
                self.assertNotIn(secret, p.read_text())
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
            self.assertEqual(result["counts"]["read_errors"], {"unknown": 1})
            self.assertIn('lang="ar" dir="rtl"', html_path.read_text())
            self.assertIn("<svg", html_path.read_text())
            self.assertIn("أحداث التغير", html_path.read_text())

    def test_incomplete_session_does_not_invent_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"open.jsonl"
            write_rows(p, fixture(end=False))
            result = analyse_file(p)
            self.assertIsNone(result["session"]["duration_seconds"])
            self.assertIsNone(result["session"]["end_utc"])
            self.assertFalse(result["session"]["ended_normally"])
            self.assertTrue(result["warnings"])

    def test_input_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "bad.jsonl"
            cases = [
                fixture() + [row("read_error", 131)],
                [dict(fixture()[0], schema_version=99)],
                [dict(fixture()[0], session_id="bad")],
                [fixture()[0], dict(checkpoint(), observed_at="not-a-date")],
                [fixture()[0], dict(checkpoint(), session_id="b"*32)],
                [fixture()[0], row("state_change", -3, category="wifi", field="signal")],
                [fixture()[0], row("something_wrong", 3)],
                [fixture()[0], fixture()[0]],
            ]
            for rows in cases:
                with self.subTest(rows=rows[-1]["type"]):
                    write_rows(p, rows)
                    with self.assertRaises(AnalysisError):
                        analyse_file(p)
            p.write_text('{"schema_version":1,\n')
            with self.assertRaises(AnalysisError):
                analyse_file(p)
            p.write_text(" ")
            with self.assertRaises(AnalysisError):
                analyse_file(p)
            target = root / "real"
            write_rows(target, fixture())
            p.unlink()
            p.symlink_to(target)
            with self.assertRaises(AnalysisError):
                analyse_file(p)

    def test_html_escapes_untrusted_interpretation_if_ever_supplied(self):
        result = {"session":{"duration_seconds":None},
                  "counts":{"records":1,"types":{},"events_by_category":{"<script>":1}},
                  "wifi":{"min_percent":None,"max_percent":None,"mean_observed_percent":None},
                  "audio":{"observed_profiles":[]},
                  "warnings":["<img src=x onerror=alert(1)>"],"interpretation":[],
                  "timeline":{"wifi":[],"battery":{}}}
        html = render_html(result)
        self.assertNotIn("<script>",html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)


    def test_plot_thinning_keeps_hidden_peaks(self):
        from dcc_analysis import _series_points
        samples = [(START + timedelta(seconds=i), 50) for i in range(2000)]
        samples[333] = (samples[333][0], 1)
        samples[1744] = (samples[1744][0], 99)
        reduced = _series_points(samples)
        self.assertLessEqual(len(reduced), 250)
        self.assertEqual(reduced[0], samples[0])
        self.assertEqual(reduced[-1], samples[-1])
        self.assertEqual(min(value for _, value in reduced), 1)
        self.assertEqual(max(value for _, value in reduced), 99)

    def test_malformed_field_types_rejected_without_type_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "strange.jsonl"
            for records in (
                [dict(fixture()[0], type={"unexpected": 1})],
                [fixture()[0], row("read_error", 2, category=["private", "data"])],
                [fixture()[0], row("state_change", 2, category={"not": "a category"}, field="signal")],
                [fixture()[0], row("state_change", 2, category="wifi", field=["signal"])],
            ):
                with self.subTest(last=records[-1]):
                    write_rows(p, records)
                    if records[-1]["type"] == "state_change" or records[-1]["type"] == "read_error":
                        result = analyse_file(p)
                        self.assertTrue(result["counts"]["events_by_category"] or result["counts"]["read_errors"])
                    else:
                        with self.assertRaises(AnalysisError):
                            analyse_file(p)


if __name__ == "__main__":
    unittest.main()
