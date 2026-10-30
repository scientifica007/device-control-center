"""Opt-in, local, bounded and privacy-preserving device diagnostics.

No hardware commands live here. The GTK application provides its already
collected snapshots; only the worker thread handles JSONL disk I/O.
"""
from __future__ import annotations

import json
import os
import queue
import re
import stat
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

CHECKPOINT_SECONDS = 300
MAX_SESSION_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024
RETENTION_SECONDS = 7 * 86400
QUEUE_LIMIT = 128
_SAFE_CODEC = re.compile(r"^[A-Za-z0-9_.+\-]{1,48}$")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def state_directory():
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".local" / "state"
    return root / "device-control-center" / "diagnostics"


def _private_directory(path):
    """Create a private managed directory; reject existing symlinks/shared dirs."""
    # The XDG base may not exist on a fresh installation.
    path.parent.parent.mkdir(parents=True, exist_ok=True)
    for item in (path.parent, path):
        try:
            item.mkdir(mode=0o700)
        except FileExistsError:
            pass
        mode = item.lstat()
        if not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.getuid() or mode.st_mode & 0o077:
            raise OSError("مجلد التسجيل ليس خاصًا بالمستخدم")


def _cleanup(directory, now):
    files = []
    for item in directory.glob("dcc-*.jsonl"):
        try:
            info = item.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                continue
            if now - info.st_mtime > RETENTION_SECONDS:
                item.unlink()
            else:
                files.append((info.st_mtime, item, info.st_size))
        except FileNotFoundError:
            continue
    files.sort()
    total = sum(size for _, _, size in files)
    for _, item, size in files:
        if total + MAX_SESSION_BYTES <= MAX_TOTAL_BYTES:
            break
        try:
            item.unlink()
        except FileNotFoundError:
            pass
        total -= size
    if total + MAX_SESSION_BYTES > MAX_TOTAL_BYTES:
        raise OSError("تعذر توفير المساحة المحددة لسجلات التشخيص")


def _percent(value):
    return value if type(value) is int and 0 <= value <= 100 else None


def _safe_token(value):
    return value if isinstance(value, str) and _SAFE_CODEC.fullmatch(value) else None


class _Projector:
    """Map real identifiers to session-local pseudonyms, never persisted."""
    def __init__(self):
        self.identifiers = {}

    def ident(self, category, raw):
        key = (category, str(raw))
        if key not in self.identifiers:
            count = sum(1 for item in self.identifiers if item[0] == category) + 1
            self.identifiers[key] = f"{category}-{count}"
        return self.identifiers[key]

    def snapshot(self, raw, previous=None):
        bt_error = bool(raw.get("bluetooth_error"))
        bluetooth = {"status": "error" if bt_error else "available", "devices": {}}
        if bt_error and previous:
            bluetooth["devices"] = previous["bluetooth"]["devices"]
        elif not bt_error:
            for dev in raw.get("bluetooth", [])[:20]:
                identifier = dev.get("mac")
                if not isinstance(identifier, str) or not identifier:
                    continue
                alias = self.ident("bt", identifier)
                failed = bool(dev.get("error") or dev.get("stale"))
                connection = {"status": "error" if failed else "available",
                              "value": None if failed else ("connected" if dev.get("connected") else "disconnected")}
                battery = _percent(dev.get("battery"))
                battery_status = "stale" if dev.get("battery_stale") or failed else ("available" if battery is not None else "unavailable")
                if battery_status != "available":
                    battery = None
                source = dev.get("battery_source") if battery_status == "available" else None
                rssi = dev.get("rssi")
                bluetooth["devices"][alias] = {
                    "connection": connection,
                    "battery": {"status": battery_status, "value": battery,
                                "source": source if source in ("BlueZ", "UPower") else None,
                                "unit": "percent", "source_reported_at": None},
                    "rssi": {"status": "error" if failed else ("available" if type(rssi) is int else "unavailable"),
                             "value": rssi if type(rssi) is int and not failed else None, "unit": "dBm"},
                }
        audio = raw.get("audio") or {}
        audio_failed = bool(audio.get("error"))
        if audio_failed and previous:
            sound = dict(previous["audio"], status="error")
        else:
            sound = {"status": "error" if audio_failed else "available"}
            for kind, nodes in (("sink", audio.get("sinks", [])), ("source", audio.get("sources", []))):
                default = next((n for n in nodes if n.get("default")), None)
                sound["default_" + kind] = self.ident(kind, default["id"]) if default is not None else None
            for key in ("out", "in"):
                volume = audio.get(key) or {}
                sound[key] = {"status": "available" if _percent(volume.get("percent")) is not None and not audio_failed else "unavailable",
                              "percent": _percent(volume.get("percent")) if not audio_failed else None,
                              "muted": bool(volume.get("muted")) if not audio_failed and _percent(volume.get("percent")) is not None else None}
            sound["profile_query"] = "error" if audio.get("bluetooth_profile_error") else "available"
            profiles = []
            for profile in audio.get("bluetooth_profiles", []):
                profiles.append({"device_id": self.ident("audio", profile.get("device")),
                                 "profile": _safe_token(profile.get("profile")),
                                 "mode": profile.get("mode") if profile.get("mode") in ("A2DP", "HFP/HSP", "متوقف", "غير معروف") else "غير معروف",
                                 "codec": _safe_token(profile.get("codec"))})
            sound["profiles"] = sorted(profiles, key=lambda obj: obj["device_id"])
        wifi = raw.get("wifi") or {}
        if wifi.get("error") and previous:
            network = dict(previous["wifi"], status="error")
        else:
            signal = _percent(wifi.get("signal"))
            freq = wifi.get("freq")
            network = {"status": "error" if wifi.get("error") else "available",
                       "connection": "unknown" if wifi.get("error") else ("connected" if wifi.get("connected") else "disconnected"),
                       "signal": {"status": "error" if wifi.get("error") else ("available" if signal is not None else "unavailable"),
                                  "value": signal, "unit": "percent"},
                       "frequency_mhz": int(freq) if isinstance(freq, str) and freq.isdecimal() and 1000 <= int(freq) <= 10000 else None}
        return {"bluetooth": bluetooth, "audio": sound, "wifi": network}


def _changes(before, after):
    """Yield only observed changes; failures never become inferred disconnections."""
    for category in ("bluetooth", "audio", "wifi"):
        left, right = before[category], after[category]
        if category == "bluetooth":
            if left["status"] != right["status"]:
                yield (category, None, "source_status", left["status"], right["status"])
            old, new = left["devices"], right["devices"]
            for identifier in sorted(old.keys() | new.keys()):
                if identifier not in old:
                    yield (category, identifier, "device_added", None, new[identifier])
                elif identifier not in new:
                    yield (category, identifier, "device_removed", old[identifier], None)
                else:
                    for field in ("connection", "battery", "rssi"):
                        if old[identifier][field] != new[identifier][field]:
                            yield (category, identifier, field, old[identifier][field], new[identifier][field])
        else:
            for field in sorted(left.keys() | right.keys()):
                if left.get(field) != right.get(field):
                    yield (category, None, field, left.get(field), right.get(field))


class SessionRecorder:
    """Start only on explicit user action. No GTK/BlueZ operations or autostart."""
    def __init__(self, directory=None, checkpoint_seconds=CHECKPOINT_SECONDS, max_bytes=MAX_SESSION_BYTES):
        self.directory = Path(directory) if directory is not None else state_directory()
        self.checkpoint_seconds = checkpoint_seconds
        self.max_bytes = max_bytes
        self.session_id = uuid.uuid4().hex
        self.path = None
        self.status = "inactive"
        self.error = None
        self._events = queue.Queue(maxsize=QUEUE_LIMIT)
        self._stop = threading.Event()
        self._abort = threading.Event()
        self._thread = None

    def start(self):
        if self.status != "inactive":
            raise RuntimeError("جلسة التسجيل بدأت بالفعل")
        self.status = "starting"
        self._thread = threading.Thread(target=self._worker, name="dcc-jsonl-writer", daemon=False)
        self._thread.start()

    def record(self, snapshot):
        if self.status not in ("starting", "recording"):
            return
        self._enqueue(("snapshot", snapshot))

    def read_error(self, category="snapshot"):
        if self.status in ("starting", "recording"):
            self._enqueue(("error", category if category in ("snapshot", "bluetooth", "audio", "wifi") else "snapshot"))

    def _enqueue(self, item):
        try:
            self._events.put_nowait(item)
        except queue.Full:
            self.error = "امتلأ مخزن التسجيل المؤقت؛ أوقف التسجيل حمايةً للبيانات"
            self._abort.set()
            self.status = "error"

    def stop(self):
        if self.status in ("starting", "recording"):
            self.status = "stopping"
            self._stop.set()

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def _worker(self):
        try:
            _private_directory(self.directory)
            _cleanup(self.directory, time.time())
            name = f"dcc-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{self.session_id[:12]}.jsonl"
            self.path = self.directory / name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                size = 0

                def write(record):
                    nonlocal size
                    line = json.dumps({"schema_version": 1, "session_id": self.session_id, **record},
                                      ensure_ascii=False, separators=(",", ":")) + "\n"
                    encoded = len(line.encode("utf-8"))
                    if size + encoded > self.max_bytes:
                        raise OSError("بلغ سجل الجلسة الحد الأقصى المسموح به")
                    handle.write(line)
                    size += encoded

                write({"type": "session_start", "observed_at": utc_now(),
                       "checkpoint_seconds": self.checkpoint_seconds})
                self.status = "stopping" if self._stop.is_set() else "recording"
                projector = _Projector()
                previous = None
                last_checkpoint = time.monotonic()
                while not self._abort.is_set():
                    try:
                        item_type, payload = self._events.get(timeout=1)
                    except queue.Empty:
                        if self._stop.is_set():
                            break
                        handle.flush()
                        continue
                    if item_type == "error":
                        write({"type": "read_error", "observed_at": utc_now(), "category": payload})
                    else:
                        now, monotonic = utc_now(), time.monotonic()
                        state = projector.snapshot(payload, previous)
                        if previous is not None:
                            for category, device, field, old, new in _changes(previous, state):
                                write({"type": "state_change", "observed_at": now,
                                       "category": category, "device_id": device, "field": field,
                                       "previous": old, "current": new})
                        if previous is None or monotonic - last_checkpoint >= self.checkpoint_seconds:
                            write({"type": "checkpoint", "observed_at": now, "state": state})
                            last_checkpoint = monotonic
                        previous = state
                    if self._stop.is_set() and self._events.empty():
                        break
                if not self._abort.is_set():
                    write({"type": "session_end", "observed_at": utc_now()})
                handle.flush()
                os.fsync(handle.fileno())
            self.status = "error" if self._abort.is_set() else "stopped"
        except (OSError, ValueError, TypeError) as exc:
            # Never log raw command errors or device identifiers to disk.
            self.error = "تعذرت كتابة السجل أو تجاوز حدّه: " + type(exc).__name__
            self.status = "error"
