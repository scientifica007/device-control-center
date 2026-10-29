"""Availability/history regressions; all identifiers and readings are synthetic."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import dcc_core as core

MAC = "02:00:00:00:00:01"
PATH = "/org/freedesktop/UPower/devices/headset_dev_02_00_00_00_00_01"


class BatteryTests(unittest.TestCase):
    def test_disappearance_and_return_are_fresh_queries(self):
        old = []
        with patch.object(core, "run", side_effect=[
                f"Device {MAC} Test", "Connected: yes\nBattery Percentage: (60)",
                f"Device {MAC} Test", "Connected: yes", "",
                f"Device {MAC} Test", "Connected: yes\nBattery Percentage: (50)"]) as run:
            first, _ = core.get_bluetooth()
            old = core.retain_battery_history(first, old)
            observed = old[0]["battery_checked_at"]
            missing, _ = core.get_bluetooth()
            absent = core.retain_battery_history(missing, old)
            returned, _ = core.get_bluetooth()
        self.assertEqual(run.call_count, 7)
        self.assertTrue(absent[0]["connected"])
        self.assertIsNone(absent[0]["battery"])
        self.assertIsNone(absent[0]["battery_checked_at"])
        self.assertIsNotNone(absent[0]["battery_query_at"])
        self.assertEqual(absent[0]["battery_last_known"]["observed_at"], observed)
        self.assertEqual(absent[0]["battery_last_known"]["percent"], 60)
        again = core.retain_battery_history(missing, absent)
        self.assertEqual(again[0]["battery_last_known"], absent[0]["battery_last_known"])
        recovered = core.retain_battery_history(returned, again)
        self.assertEqual(recovered[0]["battery"], 50)
        self.assertEqual(recovered[0]["battery_last_known"]["percent"], 50)

    def test_failed_query_never_retains_current_percentage(self):
        old = [{"mac": MAC, "battery": 60, "battery_source": "BlueZ", "battery_checked_at": "old"}]
        failed = [dict(old[0], stale=True, battery_query_at="new")]
        result = core.retain_battery_history(failed, old)[0]
        self.assertIsNone(result["battery"])
        self.assertIsNone(result["battery_checked_at"])
        self.assertEqual(result["battery_query_at"], "new")
        self.assertEqual(result["battery_last_known"]["observed_at"], "old")
        self.assertEqual(core.retain_battery_history([], old), [])

    def test_no_history_is_invented_at_startup(self):
        result = core.retain_battery_history([{"mac": MAC, "battery": None}], [])
        self.assertIsNone(result[0]["battery_last_known"])

    def test_upower_one_failure_does_not_hide_later_device(self):
        errors = []
        second = PATH[:-2] + "02"
        with patch.object(core, "run", side_effect=[PATH + "\n" + second,
                core.CommandError("temporary error"), "present: yes\npercentage: 42%"]):
            values = core._upower_batteries(errors)
        self.assertEqual(values, {"02:00:00:00:00:02": 42})
        self.assertTrue(errors)

    def test_upower_failure_is_distinct_from_missing_value(self):
        with patch.object(core, "run", side_effect=[f"Device {MAC} Test",
                "Connected: yes", core.CommandError("permission denied")]):
            devices, error = core.get_bluetooth()
        self.assertIsNone(error)
        self.assertIsNone(devices[0]["battery"])
        self.assertTrue(devices[0]["battery_error"])

    def test_absent_invalid_and_composite_upower_not_used(self):
        for details in ("present: no\npercentage: 42%", "percentage: 255%", "no percentage"):
            with self.subTest(details=details), patch.object(core, "run", side_effect=[PATH, details]):
                self.assertEqual(core._upower_batteries(), {})
        with patch.object(core, "run", return_value="/org/freedesktop/UPower/devices/DisplayDevice") as run:
            self.assertEqual(core._upower_batteries(), {})
            self.assertEqual(run.call_count, 1)
