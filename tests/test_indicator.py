"""Scoped GNOME indicator controller tests; no GNOME session or radio needed."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess, TimeoutExpired

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import dcc_indicator as indicator


class IndicatorTests(unittest.TestCase):
    def test_read_active_extension(self):
        def fake(argv, **kwargs):
            self.assertEqual(argv[0], "gnome-extensions")
            out = {
                ("list",): "another@other.example\n" + indicator.EXTENSION_UUID + "\n",
                ("list", "--enabled"): indicator.EXTENSION_UUID + "\n",
                ("list", "--active"): indicator.EXTENSION_UUID + "\n",
            }[tuple(argv[1:])]
            return CompletedProcess(argv, 0, out, "")
        with patch.object(indicator.subprocess, "run", side_effect=fake) as run:
            state = indicator.read_indicator_state()
        self.assertEqual((state.installed, state.enabled, state.active),
                         (True, True, True))
        self.assertEqual(run.call_count, 3)

    def test_disabled_existing_extension(self):
        output = [indicator.EXTENSION_UUID + "\n", "", ""]
        with patch.object(indicator.subprocess, "run", side_effect=lambda argv, **kw:
                          CompletedProcess(argv, 0, output.pop(0), "")):
            self.assertEqual(indicator.read_indicator_state(),
                             indicator.IndicatorState(True, False, False))

    def test_extension_missing_disables_control_without_attempting_action(self):
        calls = []
        def fake(argv, **kwargs):
            calls.append(argv)
            return CompletedProcess(argv, 0, "someone@else.com\n", "")
        with patch.object(indicator.subprocess, "run", side_effect=fake):
            self.assertEqual(indicator.read_indicator_state(),
                             indicator.IndicatorState(False, False, False))
            with self.assertRaises(indicator.IndicatorUnavailable):
                indicator.set_indicator_enabled(True)
        self.assertEqual(calls, [["gnome-extensions", "list"], ["gnome-extensions", "list"]])

    def test_only_own_extension_is_enabled_or_disabled(self):
        for desired in (True, False):
            commands = []
            def fake(argv, **kwargs):
                commands.append(argv)
                if argv[1:] == ["list"]:
                    out = indicator.EXTENSION_UUID + "\n" + "other@ext\n"
                elif argv[1:] == ["list", "--enabled"]:
                    count = len([c for c in commands if len(c) == 3 and c[1] in ("enable", "disable")])
                    # Before the action, state is its inverse; afterwards match.
                    active = desired if count else not desired
                    out = indicator.EXTENSION_UUID + "\n" if active else ""
                elif argv[1:] == ["list", "--active"]:
                    count = len([c for c in commands if len(c) == 3 and c[1] in ("enable", "disable")])
                    out = indicator.EXTENSION_UUID + "\n" if count and desired else ""
                else:
                    self.assertEqual(argv, ["gnome-extensions",
                                            "enable" if desired else "disable",
                                            indicator.EXTENSION_UUID])
                    out = ""
                return CompletedProcess(argv, 0, out, "")
            with self.subTest(desired=desired), patch.object(indicator.subprocess, "run", side_effect=fake):
                state = indicator.set_indicator_enabled(desired)
                self.assertEqual(state.enabled, desired)
                self.assertTrue(state.installed)
                self.assertEqual(sum(x[1] in ("enable", "disable") for x in commands), 1)
                self.assertTrue(all(x[0] == "gnome-extensions" for x in commands))
                self.assertTrue(all("bluetoothctl" not in x for x in commands))
                self.assertTrue(all(kw == 3 or len(x) != 3 for x in commands for kw in [len(x)]))

    def test_idempotent_enabled_has_no_write(self):
        calls = []
        def fake(argv, **kwargs):
            calls.append(argv)
            out = indicator.EXTENSION_UUID + "\n"
            return CompletedProcess(argv, 0, out, "")
        with patch.object(indicator.subprocess, "run", side_effect=fake):
            state = indicator.set_indicator_enabled(True)
        self.assertEqual(state, indicator.IndicatorState(True, True, True))
        self.assertTrue(all(c[1] == "list" for c in calls))

    def test_nonactive_enabled_is_not_misreported_visible(self):
        def fake(argv, **kwargs):
            out = indicator.EXTENSION_UUID + "\n" if argv[1:] in (["list"], ["list", "--enabled"]) else ""
            return CompletedProcess(argv, 0, out, "")
        with patch.object(indicator.subprocess, "run", side_effect=fake):
            state = indicator.read_indicator_state()
        self.assertTrue(state.enabled)
        self.assertFalse(state.active)

    def test_active_option_unavailable_is_unknown_not_false(self):
        def fake(argv, **kwargs):
            if argv[1:] == ["list", "--active"]:
                return CompletedProcess(argv, 1, "", "unsupported")
            return CompletedProcess(argv, 0, indicator.EXTENSION_UUID + "\n", "")
        with patch.object(indicator.subprocess, "run", side_effect=fake):
            state = indicator.read_indicator_state()
        self.assertTrue(state.enabled)
        self.assertIsNone(state.active)

    def test_errors_timeout_and_bad_values_fail_closed(self):
        with self.assertRaises(ValueError):
            indicator.set_indicator_enabled("false")
        for exception in (FileNotFoundError(), TimeoutExpired("gnome-extensions", 6)):
            with patch.object(indicator.subprocess, "run", side_effect=exception):
                with self.assertRaises(indicator.IndicatorUnavailable):
                    indicator.read_indicator_state()
        with patch.object(indicator.subprocess, "run",
                          return_value=CompletedProcess([], 2, "secret", "more secret")):
            with self.assertRaises(indicator.IndicatorUnavailable) as ctx:
                indicator.read_indicator_state()
        self.assertNotIn("secret", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
