"""Local hardware information collectors and narrowly scoped control commands.

No shell=True, network services, privilege escalation or telemetry.
"""
from __future__ import annotations

import re
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$", re.IGNORECASE)
DEVICE_RE = re.compile(r"^Device\s+([0-9A-F:]{17})\s+(.+)$", re.IGNORECASE)
BATTERY_RE = re.compile(r"^\s*Battery Percentage:\s*(?:0x[0-9A-Fa-f]+\s*)?\((\d{1,3})\)", re.MULTILINE)
NODE_RE = re.compile(r"^[\s│├└─]*(\*)?\s*(\d+)\.\s+(.*?)\s+\[vol:\s*([\d.]+)(?:\s+(MUTED))?\]")


class CommandError(RuntimeError):
    pass


def run(argv: list[str], timeout: int = 7) -> str:
    if not argv or not shutil.which(argv[0]):
        raise CommandError(f"الأداة غير متوفرة: {argv[0] if argv else '?'}")
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                errors="replace", timeout=timeout, check=False,
                                env={**os.environ, "LC_ALL": "C"})
    except subprocess.TimeoutExpired as exc:
        raise CommandError(f"انتهت المهلة: {argv[0]}") from exc
    except OSError as exc:
        raise CommandError(str(exc)) from exc
    if result.returncode != 0:
        raise CommandError((result.stderr or result.stdout or "تعذر تنفيذ الأمر").strip()[:280])
    return result.stdout


def parse_devices(output: str) -> list[dict[str, str]]:
    devices = []
    seen = set()
    for line in output.splitlines():
        match = DEVICE_RE.match(line.strip())
        if match:
            mac, name = match.groups()
            mac = mac.upper()
            if mac not in seen:
                devices.append({"mac": mac, "name": name})
                seen.add(mac)
    return devices


def parse_bt_info(output: str) -> dict[str, Any]:
    result: dict[str, Any] = {"battery": None, "connected": False, "paired": False,
                              "trusted": False, "blocked": False, "rssi": None,
                              "icon": "bluetooth", "name": ""}
    for name, prop in [("Connected", "connected"), ("Paired", "paired"),
                       ("Trusted", "trusted"), ("Blocked", "blocked")]:
        match = re.search(rf"^\s*{name}:\s*(yes|no)\s*$", output, re.M | re.I)
        if match:
            result[prop] = match.group(1).lower() == "yes"
    for name, prop in [("Name", "name"), ("Icon", "icon"), ("Alias", "alias")]:
        match = re.search(rf"^\s*{name}:\s*(.*?)\s*$", output, re.M)
        if match:
            result[prop] = match.group(1)
    match = BATTERY_RE.search(output)
    if match:
        result["battery"] = min(100, max(0, int(match.group(1))))
    else:
        match = re.search(r"^\s*Battery Percentage:\s*(\d{1,3})\s*$", output, re.M)
        if match:
            result["battery"] = min(100, max(0, int(match.group(1))))
    match = re.search(r"^\s*RSSI:\s*(-?\d+)\s*$", output, re.M)
    if match:
        result["rssi"] = int(match.group(1))
    return result


def parse_wpctl(output: str) -> dict[str, list[dict[str, Any]]]:
    nodes: dict[str, list[dict[str, Any]]] = {"sinks": [], "sources": []}
    top = ""
    subsection = ""
    for line in output.splitlines():
        if line.strip() == "Audio":
            top = "audio"
            subsection = ""
        elif line.strip() in ("Video", "Settings"):
            top = ""
            subsection = ""
        elif top == "audio":
            if re.search(r"(?:^|─\s*)(Sinks|Sources):\s*$", line):
                subsection = "sinks" if "Sinks:" in line else "sources"
            elif re.search(r"(?:^|─\s*)(Devices|Sink endpoints|Source endpoints|Streams):\s*$", line):
                subsection = ""
            elif subsection:
                m = NODE_RE.match(line)
                if m:
                    star, uid, name, volume, muted = m.groups()
                    nodes[subsection].append({"id": int(uid), "name": name.strip(),
                                              "default": bool(star), "volume": round(float(volume) * 100),
                                              "muted": bool(muted)})
    return nodes


def parse_volume(output: str) -> dict[str, Any]:
    match = re.search(r"Volume:\s*([\d.]+)(?:\s+(\[MUTED\]))?", output)
    if not match:
        return {"percent": None, "muted": False}
    return {"percent": round(float(match.group(1)) * 100), "muted": bool(match.group(2))}


def _upower_batteries(errors: list[str] | None = None) -> dict[str, int]:
    batteries: dict[str, int] = {}
    errors = errors if errors is not None else []
    try:
        paths = run(["upower", "-e"], timeout=5)
    except CommandError:
        errors.append("تعذر تعداد مصادر UPower")
        return batteries
    for path in paths.splitlines():
        mac = re.search(r"headset_dev_((?:[0-9a-fA-F]{2}_){5}[0-9a-fA-F]{2})", path)
        if not mac:
            continue
        try:
            details = run(["upower", "-i", path], timeout=5)
        except CommandError:
            errors.append("تعذرت قراءة أحد مصادر UPower")
            continue
        if re.search(r"^\s*present:\s*no\s*$", details, re.M):
            continue
        value = re.search(r"^\s*percentage:\s*(\d+(?:\.\d+)?)%", details, re.M)
        if value and 0 <= float(value.group(1)) <= 100:
            batteries[mac.group(1).replace("_", ":").upper()] = round(float(value.group(1)))
    return batteries


def get_bluetooth() -> tuple[list[dict[str, Any]], str | None]:
    try:
        raw = run(["bluetoothctl", "devices"], timeout=7)
        devices = parse_devices(raw)
    except CommandError as err:
        return [], str(err)
    upower_batts = None
    upower_errors: list[str] = []
    for device in devices[:20]:
        try:
            details = parse_bt_info(run(["bluetoothctl", "info", device["mac"]], timeout=6))
            device.update(details)
            device["battery_source"] = "BlueZ" if details["battery"] is not None else None
        except CommandError as err:
            device["error"] = str(err)
        if device.get("battery") is None:
            if upower_batts is None:
                upower_batts = _upower_batteries(upower_errors)
            device["battery"] = upower_batts.get(device["mac"])
            if device["battery"] is not None:
                device["battery_source"] = "UPower"
        device["battery_query_at"] = datetime.now(timezone.utc).isoformat()
        device["battery_error"] = ("؛ ".join(upower_errors) or None) if device.get("battery") is None else None
        device["battery_checked_at"] = (device["battery_query_at"]
                                         if device.get("battery") is not None else None)
    return devices[:20], None


def retain_battery_history(devices, previous):
    """Session-only observations, always separate from the current reading.

    No persistence, guessed values or device-report timestamps. A successful
    empty device list stays empty; old devices are never resurrected here.
    """
    old = {d["mac"]: d for d in previous}
    result = []
    for device in devices:
        item = dict(device)
        prior = old.get(item["mac"], {})
        history = prior.get("battery_last_known")
        if prior.get("battery") is not None and not prior.get("stale"):
            history = {"percent": prior["battery"], "source": prior.get("battery_source"),
                       "observed_at": prior.get("battery_checked_at")}
        if item.get("battery_stale", item.get("stale", False)):
            item.update(battery=None, battery_source=None, battery_checked_at=None)
        elif item.get("battery") is not None:
            history = {"percent": item["battery"], "source": item.get("battery_source"),
                       "observed_at": item.get("battery_checked_at")}
        item["battery_last_known"] = history
        result.append(item)
    return result


def parse_bluetooth_audio_profiles(output: str) -> list[dict[str, Any]]:
    """Parse active Bluetooth cards only; do not infer active codecs from availability."""
    profiles = []
    for block in re.split(r"(?=^Card #\d+\s*$)", output, flags=re.M):
        card = re.search(r"^\s*Name:\s*(bluez_card\.[^\s]+)\s*$", block, re.M)
        if not card:
            continue
        current = re.search(r"^\s*Active Profile:\s*(\S+)\s*$", block, re.M)
        if not current:
            continue
        active = current.group(1)
        description = re.search(
            r'^\s*(?:device\.description|device\.alias)\s*=\s*"([^"]+)"\s*    try:
        status = run(["wpctl", "status"], timeout=6)
        audio = parse_wpctl(status)
        audio["bluetooth_profiles"] = []
        audio["bluetooth_profile_error"] = None
        # Independent read-only diagnosis. An unavailable pactl must never
        # prevent reading or controlling volume through wpctl.
        if "[bluez5]" in status:
            try:
                audio["bluetooth_profiles"] = parse_bluetooth_audio_profiles(
                    run(["pactl", "list", "cards"], timeout=6))
            except CommandError as err:
                audio["bluetooth_profile_error"] = str(err)
        for key, target in (("out", "@DEFAULT_AUDIO_SINK@"), ("in", "@DEFAULT_AUDIO_SOURCE@")):
            try:
                audio[key] = parse_volume(run(["wpctl", "get-volume", target], timeout=5))
            except CommandError:
                audio[key] = {"percent": None, "muted": False}
        audio["error"] = None
        return audio
    except CommandError as err:
        return {"sinks": [], "sources": [], "out": {"percent": None, "muted": False},
                "in": {"percent": None, "muted": False},
                "bluetooth_profiles": [], "bluetooth_profile_error": None,
                "error": str(err)}


def get_wifi() -> dict[str, Any]:
    try:
        lines = run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,FREQ", "device", "wifi", "list", "--rescan", "no"], timeout=6)
        for line in lines.splitlines():
            if not line.startswith("*:"):
                continue
            parts = re.split(r"(?<!\\):", line)
            if len(parts) >= 4:
                freq = parts[-1].replace("\\:", ":")
                signal = parts[-2]
                ssid = ":".join(parts[1:-2]).replace("\\:", ":").replace("\\\\", "\\")
                return {"connected": True, "ssid": ssid or "شبكة مخفية",
                        "signal": int(signal) if signal.isdecimal() else None, "freq": freq}
        return {"connected": False, "ssid": None, "signal": None, "freq": None}
    except (CommandError, ValueError) as err:
        return {"connected": False, "ssid": None, "signal": None,
                "freq": None, "error": str(err)}


def snapshot() -> dict[str, Any]:
    devices, error = get_bluetooth()
    return {"timestamp": datetime.now(timezone.utc).isoformat(),
            "bluetooth_query_at": datetime.now(timezone.utc).isoformat(),
            "bluetooth": devices, "bluetooth_error": error,
            "audio": get_audio(), "wifi": get_wifi()}


def set_volume(kind: str, percent: int) -> None:
    if kind not in ("out", "in") or not isinstance(percent, int) or not 0 <= percent <= 100:
        raise ValueError("مستوى الصوت أو نوعه غير صالح")
    target = "@DEFAULT_AUDIO_SINK@" if kind == "out" else "@DEFAULT_AUDIO_SOURCE@"
    run(["wpctl", "set-volume", target, f"{percent}%"], timeout=6)


def toggle_mute(kind: str) -> None:
    if kind not in ("out", "in"):
        raise ValueError("نوع الصوت غير صالح")
    target = "@DEFAULT_AUDIO_SINK@" if kind == "out" else "@DEFAULT_AUDIO_SOURCE@"
    run(["wpctl", "set-mute", target, "toggle"], timeout=6)


def set_default_audio(node_id: int, kind: str, audio: dict[str, Any]) -> None:
    if kind not in ("sinks", "sources"):
        raise ValueError("نوع الجهاز غير صالح")
    if node_id not in [item["id"] for item in audio.get(kind, [])]:
        raise ValueError("لم يعد الجهاز مدرجًا؛ حدّث البيانات")
    run(["wpctl", "set-default", str(node_id)], timeout=6)


def bluetooth_action(mac: str, action: str) -> None:
    if not MAC_RE.fullmatch(mac) or action not in ("connect", "disconnect", "pair"):
        raise ValueError("الأمر أو عنوان الجهاز غير صالح")
    run(["bluetoothctl", action, mac.upper()], timeout=18)


def scan() -> str:
    try:
        return run(["bluetoothctl", "--timeout", "8", "scan", "on"], timeout=13)
    finally:
        try:
            run(["bluetoothctl", "scan", "off"], timeout=5)
        except CommandError:
            pass
,
            block, re.M)
        display_name = description.group(1) if description else "جهاز صوت Bluetooth"
        section = re.search(r"^\s*Profiles:\s*$([\s\S]*?)^\s*Active Profile:",
                            block, re.M)
        details = ""
        if section:
            active_line = re.search(
                rf"^\s*{re.escape(active)}:\s*(.+)$", section.group(1), re.M)
            if active_line:
                details = active_line.group(1)
        codec_match = re.search(r"\bcodec\s+([a-zA-Z0-9_+.-]+)", details, re.I)
        codec = codec_match.group(1).replace("_", "-").upper() if codec_match else None
        mode = ("A2DP" if active.startswith("a2dp-")
                else "HFP/HSP" if active.startswith("headset-")
                else "متوقف" if active == "off" else "غير معروف")
        profiles.append({"device": display_name, "profile": active,
                         "mode": mode, "codec": codec})
    return profiles


def get_audio() -> dict[str, Any]:
    try:
        audio = parse_wpctl(run(["wpctl", "status"], timeout=6))
        for key, target in (("out", "@DEFAULT_AUDIO_SINK@"), ("in", "@DEFAULT_AUDIO_SOURCE@")):
            try:
                audio[key] = parse_volume(run(["wpctl", "get-volume", target], timeout=5))
            except CommandError:
                audio[key] = {"percent": None, "muted": False}
        audio["error"] = None
        return audio
    except CommandError as err:
        return {"sinks": [], "sources": [], "out": {"percent": None, "muted": False},
                "in": {"percent": None, "muted": False}, "error": str(err)}


def get_wifi() -> dict[str, Any]:
    try:
        lines = run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,FREQ", "device", "wifi", "list", "--rescan", "no"], timeout=6)
        for line in lines.splitlines():
            if not line.startswith("*:"):
                continue
            parts = re.split(r"(?<!\\):", line)
            if len(parts) >= 4:
                freq = parts[-1].replace("\\:", ":")
                signal = parts[-2]
                ssid = ":".join(parts[1:-2]).replace("\\:", ":").replace("\\\\", "\\")
                return {"connected": True, "ssid": ssid or "شبكة مخفية",
                        "signal": int(signal) if signal.isdecimal() else None, "freq": freq}
        return {"connected": False, "ssid": None, "signal": None, "freq": None}
    except (CommandError, ValueError) as err:
        return {"connected": False, "ssid": None, "signal": None,
                "freq": None, "error": str(err)}


def snapshot() -> dict[str, Any]:
    devices, error = get_bluetooth()
    return {"timestamp": datetime.now(timezone.utc).isoformat(),
            "bluetooth_query_at": datetime.now(timezone.utc).isoformat(),
            "bluetooth": devices, "bluetooth_error": error,
            "audio": get_audio(), "wifi": get_wifi()}


def set_volume(kind: str, percent: int) -> None:
    if kind not in ("out", "in") or not isinstance(percent, int) or not 0 <= percent <= 100:
        raise ValueError("مستوى الصوت أو نوعه غير صالح")
    target = "@DEFAULT_AUDIO_SINK@" if kind == "out" else "@DEFAULT_AUDIO_SOURCE@"
    run(["wpctl", "set-volume", target, f"{percent}%"], timeout=6)


def toggle_mute(kind: str) -> None:
    if kind not in ("out", "in"):
        raise ValueError("نوع الصوت غير صالح")
    target = "@DEFAULT_AUDIO_SINK@" if kind == "out" else "@DEFAULT_AUDIO_SOURCE@"
    run(["wpctl", "set-mute", target, "toggle"], timeout=6)


def set_default_audio(node_id: int, kind: str, audio: dict[str, Any]) -> None:
    if kind not in ("sinks", "sources"):
        raise ValueError("نوع الجهاز غير صالح")
    if node_id not in [item["id"] for item in audio.get(kind, [])]:
        raise ValueError("لم يعد الجهاز مدرجًا؛ حدّث البيانات")
    run(["wpctl", "set-default", str(node_id)], timeout=6)


def bluetooth_action(mac: str, action: str) -> None:
    if not MAC_RE.fullmatch(mac) or action not in ("connect", "disconnect", "pair"):
        raise ValueError("الأمر أو عنوان الجهاز غير صالح")
    run(["bluetoothctl", action, mac.upper()], timeout=18)


def scan() -> str:
    try:
        return run(["bluetoothctl", "--timeout", "8", "scan", "on"], timeout=13)
    finally:
        try:
            run(["bluetoothctl", "scan", "off"], timeout=5)
        except CommandError:
            pass
