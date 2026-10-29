"""GTK display of active Bluetooth audio profile/codec, synthetic only."""
from unittest.mock import patch
from gtk_layout import DccWindow, core, wait, pump


def snapshot(profiles, error=None):
    return {"bluetooth": [], "bluetooth_error": None,
            "audio": {"sinks": [], "sources": [],
                      "out": {"percent": None, "muted": False},
                      "in": {"percent": None, "muted": False},
                      "bluetooth_profiles": profiles, "bluetooth_profile_error": error,
                      "error": None},
            "wifi": {}, "timestamp": "synthetic"}


def main():
    with patch.object(core, "snapshot", return_value=snapshot([
        {"device": "Synthetic", "profile": "a2dp-sink",
         "mode": "A2DP", "codec": "SBC"}])), \
            patch.object(core, "set_volume") as volume, \
            patch.object(core, "set_default_audio") as select:
        window = DccWindow()
        try:
            window.show_all()
            wait(lambda: window.data is not None and not window._busy)
            assert "A2DP / SBC" in window.bluetooth_codec_info.get_text()
            assert "SBC-XQ" not in window.bluetooth_codec_info.get_text()
            window._render_audio(snapshot([
                {"device": "Synthetic", "profile": "a2dp-sink-sbc_xq",
                 "mode": "A2DP", "codec": "SBC-XQ"}])["audio"])
            assert "A2DP / SBC-XQ" in window.bluetooth_codec_info.get_text()
            assert "تنبيه توافق" in window.bluetooth_codec_info.get_text()
            window._render_audio(snapshot([
                {"device": "Synthetic", "profile": "headset-head-unit",
                 "mode": "HFP/HSP", "codec": None}])["audio"])
            assert "غير محدد من النظام" in window.bluetooth_codec_info.get_text()
            window._render_audio(snapshot([], "pactl unavailable")["audio"])
            assert "pactl unavailable" in window.bluetooth_codec_info.get_text()
            window._render_audio(snapshot([])["audio"])
            assert "لا تتوفر بطاقة" in window.bluetooth_codec_info.get_text()
            volume.assert_not_called()
            select.assert_not_called()
        finally:
            window.destroy()
            pump()
    print("PASS Bluetooth active profile/codec GTK display; no device writes")


if __name__ == "__main__":
    main()
