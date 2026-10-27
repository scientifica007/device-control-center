import sys
import unittest
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

if __name__ == '__main__':
    unittest.main()
