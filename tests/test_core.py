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

    def test_bluetooth_rssi_remains_optional_and_uses_dbm(self):
        measured = dc.parse_bt_info(BT_INFO + " RSSI: -62\n")
        self.assertEqual(measured['rssi'], -62)
        absent = dc.parse_bt_info(BT_INFO)
        self.assertIsNone(absent['rssi'])

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

    def test_parse_bluetooth_codec_from_active_profile_only(self):
        raw = """Card #19
    Name: alsa_card.pci-0000_00_1b.0
    Active Profile: output:analog-stereo+input:analog-stereo
Card #42
    Name: bluez_card.AA_BB_CC_DD_EE_FF
    Properties:
        device.description = "Synthetic headset"
    Profiles:
        off: Off (sinks: 0, sources: 0, priority: 0, available: yes)
        a2dp-sink: High Fidelity Playback (A2DP Sink, codec SBC) (sinks: 1, sources: 0, priority: 18, available: yes)
        a2dp-sink-sbc_xq: High Fidelity Playback (A2DP Sink, codec SBC-XQ) (sinks: 1, sources: 0, priority: 17, available: yes)
        headset-head-unit-msbc: Headset (codec mSBC) (sinks: 1, sources: 1, priority: 3, available: yes)
    Active Profile: a2dp-sink
Card #43
    Name: bluez_card.11_22_33_44_55_66
    Properties:
        device.description = "Other headset"
    Profiles:
        a2dp-sink: High Fidelity Playback (codec SBC) (sinks: 1, sources: 0)
        a2dp-sink-sbc_xq: High Fidelity Playback (codec SBC-XQ) (sinks: 1, sources: 0)
    Active Profile: a2dp-sink-sbc_xq
"""
        parsed = dc.parse_bluetooth_audio_profiles(raw)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0], {'device': 'Synthetic headset',
                                     'profile': 'a2dp-sink', 'mode': 'A2DP', 'codec': 'SBC'})
        self.assertEqual(parsed[1]['codec'], 'SBC-XQ')
        self.assertEqual(parsed[1]['profile'], 'a2dp-sink-sbc_xq')

    def test_unknown_active_profile_does_not_guess_codec(self):
        raw = """Card #12
    Name: bluez_card.AA_BB_CC_DD_EE_FF
    Profiles:
        a2dp-sink: High Fidelity Playback (codec SBC)
    Active Profile: headset-head-unit-msbc
"""
        profiles = dc.parse_bluetooth_audio_profiles(raw)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]['mode'], 'HFP/HSP')
        self.assertIsNone(profiles[0]['codec'])

    def test_get_audio_pactl_failure_keeps_volume(self):
        def mocked_run(argv, timeout=6):
            if argv[:2] == ['wpctl', 'status']:
                return WP_OLD
            if argv[:3] == ['pactl', 'list', 'cards']:
                raise dc.CommandError('pactl unavailable')
            if argv[:2] == ['wpctl', 'get-volume']:
                return 'Volume: 0.48'
            raise AssertionError(argv)
        with patch.object(dc, 'run', side_effect=mocked_run):
            audio = dc.get_audio()
        self.assertIsNone(audio['error'])
        self.assertEqual(audio['out']['percent'], 48)
        self.assertEqual(audio['in']['percent'], 48)
        self.assertEqual(audio['bluetooth_profiles'], [])
        self.assertIn('pactl', audio['bluetooth_profile_error'])

    def test_get_audio_skips_pactl_without_bluetooth_card(self):
        plain_status = WP_OLD.replace('[bluez5]', '[alsa]')
        with patch.object(dc, 'run') as query:
            query.side_effect = [plain_status, 'Volume: 0.48', 'Volume: 0.31']
            audio = dc.get_audio()
        self.assertEqual(query.call_count, 3)
        self.assertEqual(audio['bluetooth_profiles'], [])

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
