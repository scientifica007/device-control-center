"""Safe control of only the Device Control Center GNOME panel extension.

Never changes the Bluetooth radio, other GNOME extensions, audio profiles,
or shell-wide extension settings. All commands run as the desktop user.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

EXTENSION_UUID = "battery@scientifica007.github.io"
TIMEOUT_SECONDS = 6


@dataclass(frozen=True)
class IndicatorState:
    installed: bool
    enabled: bool
    active: bool | None  # GNOME 46 distinguishes configured vs actually active.


class IndicatorUnavailable(RuntimeError):
    """Cannot safely read or modify the GNOME extension state."""


def _command(*args):
    try:
        result = subprocess.run(
            ["gnome-extensions", *args], check=False, capture_output=True,
            text=True, timeout=TIMEOUT_SECONDS)
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError) as exc:
        raise IndicatorUnavailable("تعذر الاتصال بأداة إدارة امتدادات GNOME") from exc
    if result.returncode != 0:
        # Avoid propagating arbitrary CLI diagnostic text or system identifiers.
        raise IndicatorUnavailable("تعذر تنفيذ أمر امتداد GNOME")
    return result.stdout


def _has_uuid(output):
    return EXTENSION_UUID in output.splitlines()


def read_indicator_state():
    """Read GNOME Shell state; no writes or hardware queries."""
    installed = _has_uuid(_command("list"))
    if not installed:
        return IndicatorState(False, False, False)
    enabled = _has_uuid(_command("list", "--enabled"))
    try:
        active = _has_uuid(_command("list", "--active"))
    except IndicatorUnavailable:
        # Older GNOME variants may lack --active; never assert panel visibility.
        active = None
    return IndicatorState(True, enabled, active)


def set_indicator_enabled(enable):
    """Explicit opt-in write, and only to our extension UUID."""
    if type(enable) is not bool:
        raise ValueError("القيمة المطلوبة ليست منطقية")
    status = read_indicator_state()
    if not status.installed:
        raise IndicatorUnavailable("الامتداد غير مثبت؛ لم يُجرَ أي تغيير")
    if status.enabled != enable:
        _command("enable" if enable else "disable", EXTENSION_UUID)
    # A zero exit status alone does not prove the indicator is visible.
    # GNOME may require re-login or the extension may fail at runtime.
    current = read_indicator_state()
    if current.enabled != enable:
        raise IndicatorUnavailable("لم تتأكد حالة الامتداد بعد تنفيذ الأمر")
    return current
