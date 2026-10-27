import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import dcc_core as dc

BT_INFO = """Device 41:42:98:45:F7:5B (public)
 Name: Condor Aero 1
 Alias: Condor Aero 1
 Icon: audio-headset
 Paired: yes
 Trusted: yes
 Connected: yes
 Battery Percentage: 0x46 (70)
"""
WP_OLD = """PipeWire 'pipewire-0'
Audio
 ├─ Devices:
 │      48. Built-in Audio                      [alsa]
 │     114. Condor Aero 1                       [bluez5]
 ├─ Sinks:
 │      51. Built-in Audio Analog Stereo        [vol: 1.35 MUTED]
 │  *  108. Condor Aero 1                       [vol: 0.40]
 ├─ Sources:
 │  *   52. Built-in Audio Analog Stereo        [vol: 0.11]
 └─ Streams:
Video
 ├─ Sources:
 │ * 49. HP Webcam-101 [vol: 1.00]
"""
WP_NEW = """PipeWire 'pipewire-0'
Audio
 ├─ Devices:
 │      48. Built-in Audio [alsa]
 │     114. Condor Aero 1 [bluez5]
 ├─ Sinks:
 │      51. Built-in Audio Analog Stereo        [vol: 1.35 MUTED]
 │  *  112. Condor Aero 1                       [vol: 0.92]
 ├─ Sources:
 │      52. Built-in Audio Analog Stereo        [vol: 0.11]
 │  *  108. Condor Aero 1                       [vol: 1.00]
 └─ Streams:
Video
"""

class CoreTests(unittest.TestCase):
    def test_devices(self):
        self.assertEqual(dc.parse_devices('Device 41:42:98:45:F7:5B Condor Aero 1')[0]['name'], 'Condor Aero 1')

    def test_battery(self):
        item = dc.parse_bt_info(BT_INFO)
        self.assertEqual(item['battery'], 70)
        self.assertTrue(item['connected'])
        self.assertTrue(item['trusted'])
        self.assertIsNone(item['rssi'])

    def test_audio_old(self):
        audio = dc.parse_wpctl(WP_OLD)
        self.assertEqual(len(audio['sinks']), 2)
        self.assertEqual(audio['sinks'][1]['volume'], 40)
        self.assertEqual(audio['sources'][0]['id'], 52)

    def test_audio_new(self):
        audio = dc.parse_wpctl(WP_NEW)
        self.assertEqual(audio['sinks'][1]['id'], 112)
        self.assertEqual(audio['sinks'][1]['volume'], 92)
        self.assertEqual(audio['sources'][1]['id'], 108)
        self.assertEqual(audio['sources'][1]['volume'], 100)
        self.assertTrue(audio['sources'][1]['default'])

    def test_volume(self):
        self.assertEqual(dc.parse_volume('Volume: 0.92')['percent'], 92)
        self.assertTrue(dc.parse_volume('Volume: 0.00 [MUTED]')['muted'])

    def test_reject_bad_actions(self):
        for action in ('exec', 'trust'):
            with self.assertRaises(ValueError):
                dc.bluetooth_action('41:42:98:45:F7:5B', action)
        with self.assertRaises(ValueError):
            dc.set_volume('out', 101)
        with self.assertRaises(ValueError):
            dc.set_default_audio(999, 'sources', dc.parse_wpctl(WP_NEW))

class HardwareFailureTests(unittest.TestCase):
    def test_disconnected_device_has_no_invented_battery(self):
        with patch.object(dc, 'run', side_effect=[
                'Device AA:BB:CC:DD:EE:FF Headset',
                'Connected: no', '']):
            devices, error = dc.get_bluetooth()
        self.assertIsNone(error)
        self.assertFalse(devices[0]['connected'])
        self.assertIsNone(devices[0]['battery'])

    def test_permission_denied_is_reported(self):
        with patch.object(dc, 'run', side_effect=dc.CommandError('Permission denied')):
            devices, error = dc.get_bluetooth()
            audio = dc.get_audio()
        self.assertEqual(devices, [])
        self.assertEqual(error, 'Permission denied')
        self.assertEqual(audio['error'], 'Permission denied')
        self.assertIsNone(audio['in']['percent'])

    def test_upower_fallback_uses_current_reading(self):
        path = '/org/freedesktop/UPower/devices/headset_dev_AA_BB_CC_DD_EE_FF'
        with patch.object(dc, 'run', side_effect=[
                'Device AA:BB:CC:DD:EE:FF Headset',
                'Connected: yes', path, ' percentage: 53%']):
            devices, error = dc.get_bluetooth()
        self.assertIsNone(error)
        self.assertEqual(devices[0]['battery'], 53)
        self.assertEqual(devices[0]['battery_source'], 'UPower')
        self.assertIsNotNone(devices[0]['battery_checked_at'])

    def test_repeated_reads_do_not_cache_battery(self):
        with patch.object(dc, 'run', side_effect=[
                'Device AA:BB:CC:DD:EE:FF Headset',
                'Connected: yes\nBattery Percentage: 0x3c (60)',
                'Device AA:BB:CC:DD:EE:FF Headset',
                'Connected: yes\nBattery Percentage: 0x32 (50)']) as query:
            first, _ = dc.get_bluetooth()
            second, _ = dc.get_bluetooth()
        self.assertEqual(query.call_count, 4)
        self.assertEqual(first[0]['battery'], 60)
        self.assertEqual(second[0]['battery'], 50)
        self.assertEqual(second[0]['battery_source'], 'BlueZ')
        self.assertIsNotNone(second[0]['battery_checked_at'])

    def test_controls_resolve_default_nodes(self):
        with patch.object(dc, 'run') as run:
            dc.set_volume('out', 39)
            dc.set_volume('in', 12)
            dc.toggle_mute('in')
        self.assertEqual(run.call_args_list[0].args[0],
                         ['wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', '39%'])
        self.assertEqual(run.call_args_list[1].args[0],
                         ['wpctl', 'set-volume', '@DEFAULT_AUDIO_SOURCE@', '12%'])
        self.assertEqual(run.call_args_list[2].args[0],
                         ['wpctl', 'set-mute', '@DEFAULT_AUDIO_SOURCE@', 'toggle'])

if __name__ == '__main__':
    unittest.main()
