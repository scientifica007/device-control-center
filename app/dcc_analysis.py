"""Offline JSONL diagnostics analyst (schema v1); no device queries or GTK.

All externally supplied strings are rejected or reduced to strict allowlists
before JSON/HTML output. Analyses are descriptive, never causal diagnoses.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

MAX_INPUT = 25 * 1024 * 1024
MAX_LINE = 256 * 1024
SERIES_LIMIT = 250
KINDS = {"session_start", "session_end", "checkpoint", "state_change", "read_error"}
CATEGORIES = {"bluetooth", "wifi", "audio", "snapshot"}
FIELDS = {
    "bluetooth": {"battery", "connection", "rssi", "device_added", "device_removed", "source_status"},
    "wifi": {"signal", "connection", "frequency_mhz", "status"},
    "audio": {"status", "profiles", "out", "in", "default_sink", "default_source", "profile_query"},
}
CODEC_RE = re.compile(r"^[a-zA-Z0-9_+.\-]{1,48}$")
ALIAS_RE = re.compile(r"^(?:bt|audio|sink|source)-[0-9]{1,4}$")


class AnalysisError(ValueError):
    """Rejected, incomplete, or invalid diagnostic file."""


def _time(value):
    if not isinstance(value, str):
        raise AnalysisError("طابع زمني غير صالح")
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnalysisError("طابع زمني غير صالح") from exc
    if t.tzinfo is None:
        raise AnalysisError("التوقيت لا يحتوي منطقة زمنية")
    return t.astimezone(timezone.utc)


def _percent(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100 else None


def _alias(value):
    return value if isinstance(value, str) and ALIAS_RE.fullmatch(value) else None


def _signal(obj):
    if isinstance(obj, dict) and obj.get("status") == "available":
        return _percent(obj.get("value"))
    return None


def _battery(obj):
    if isinstance(obj, dict) and obj.get("status") == "available":
        return _percent(obj.get("value"))
    return None


def _safe_profile(obj):
    if not isinstance(obj, dict):
        return None
    profile = obj.get("profile")
    codec = obj.get("codec")
    if not isinstance(profile, str) or not CODEC_RE.fullmatch(profile):
        return None
    if codec is not None and (not isinstance(codec, str) or not CODEC_RE.fullmatch(codec)):
        return None
    return {"profile": profile, "codec": codec, "mode":
            obj.get("mode") if obj.get("mode") in ("A2DP", "HFP/HSP") else "غير معروف"}


def _series_points(points, limit=SERIES_LIMIT):
    """Downsample a plot, retaining local minima/maxima and first/last events.

    This only reduces SVG/JSON timeline size; all original observations still
    contribute to counts, means and bounds, and the source JSONL is untouched.
    """
    if len(points) <= limit:
        return points
    buckets = max(1, (limit - 2) // 2)
    width = math.ceil((len(points) - 2) / buckets)
    chosen = [0]
    for start in range(1, len(points) - 1, width):
        end = min(len(points) - 1, start + width)
        bucket = range(start, end)
        chosen.extend(sorted({
            min(bucket, key=lambda i: points[i][1]),
            max(bucket, key=lambda i: points[i][1]),
        }))
    chosen.append(len(points) - 1)
    return [points[i] for i in sorted(set(chosen))]


def analyse_file(path):
    """Read a single untrusted local JSONL file; return sanitized analysis only."""
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT or info.st_size <= 0:
        raise AnalysisError("يجب اختيار ملف JSONL عادي لا يتجاوز 25 MiB")
    counts = Counter()
    categories = Counter()
    fields = Counter()
    errors = Counter()
    warnings = []
    wifi_samples = []
    batteries = defaultdict(list)
    profiles = set()
    first = last = begin = end = None
    current_id = None
    previous_time = None
    schema_version = None
    checkpoints = 0
    audio_error_observations = 0
    records = 0
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if len(line.encode("utf-8")) > MAX_LINE:
                raise AnalysisError("سطر JSONL يتجاوز الحد المسموح")
            if not line.strip():
                raise AnalysisError("سطر فارغ في ملف JSONL")
            try:
                row = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-JSON number")))
            except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
                raise AnalysisError("ملف JSONL يحتوي سطرًا غير صالح") from exc
            if not isinstance(row, dict) or type(row.get("schema_version")) is not int or row.get("schema_version") != 1:
                raise AnalysisError("إصدار مخطط التسجيل غير مدعوم")
            kind = row.get("type")
            if not isinstance(kind, str) or kind not in KINDS:
                raise AnalysisError("نوع سجل غير معروف")
            sid = row.get("session_id")
            if not isinstance(sid, str) or not re.fullmatch(r"[a-f0-9]{32}", sid):
                raise AnalysisError("معرّف الجلسة غير صالح")
            if current_id is None:
                current_id = sid
            elif current_id != sid:
                raise AnalysisError("ملف يحتوي أكثر من جلسة واحدة")
            when = _time(row.get("observed_at"))
            if previous_time is not None and when < previous_time:
                raise AnalysisError("الطوابع الزمنية ليست تصاعدية")
            previous_time = when
            if first is None:
                first = when
                if kind != "session_start":
                    warnings.append("لا يحتوي الملف على سجل بداية في أول سطر")
            if end is not None:
                raise AnalysisError("سجلات إضافية بعد نهاية الجلسة")
            last = when
            records += 1
            counts[kind] += 1
            if kind == "session_start":
                if begin is not None or records != 1:
                    raise AnalysisError("بداية جلسة مكررة أو متأخرة")
                begin = when
                continue
            if kind == "session_end":
                end = when
                continue
            if kind == "read_error":
                category = row.get("category")
                errors[category if isinstance(category, str) and category in CATEGORIES else "unknown"] += 1
                continue
            if kind == "checkpoint":
                checkpoints += 1
                state = row.get("state")
                if not isinstance(state, dict):
                    raise AnalysisError("نقطة تحقق غير صالحة")
                wifi = state.get("wifi", {})
                if isinstance(wifi, dict):
                    strength = _signal(wifi.get("signal"))
                    if strength is not None:
                        wifi_samples.append((when, strength))
                bluetooth = state.get("bluetooth", {})
                if isinstance(bluetooth, dict):
                    devices = bluetooth.get("devices", {})
                    if isinstance(devices, dict):
                        for identifier, device in devices.items():
                            alias = _alias(identifier)
                            if alias and alias.startswith("bt-") and isinstance(device, dict):
                                pct = _battery(device.get("battery"))
                                if pct is not None:
                                    batteries[alias].append((when, pct))
                audio = state.get("audio", {})
                if isinstance(audio, dict):
                    if audio.get("status") == "error":
                        audio_error_observations += 1
                    if isinstance(audio.get("profiles"), list):
                        for item in audio["profiles"][:30]:
                            profile = _safe_profile(item)
                            if profile:
                                profiles.add((profile["profile"], profile["codec"], profile["mode"]))
                continue
            category, field = row.get("category"), row.get("field")
            if (not isinstance(category, str) or category not in FIELDS or
                    not isinstance(field, str) or field not in FIELDS[category]):
                # Unknown fields are counted, not echoed or treated as measurements.
                categories["unknown"] += 1
                continue
            categories[category] += 1
            fields[category + "." + field] += 1
            current = row.get("current")
            if category == "wifi" and field == "signal":
                pct = _signal(current)
                if pct is not None:
                    wifi_samples.append((when, pct))
            if category == "bluetooth" and field == "battery":
                alias = _alias(row.get("device_id"))
                if alias and alias.startswith("bt-"):
                    pct = _battery(current)
                    if pct is not None:
                        batteries[alias].append((when, pct))
            if category == "audio" and field == "status" and current == "error":
                audio_error_observations += 1
            if category == "audio" and field == "profiles" and isinstance(current, list):
                for item in current[:30]:
                    profile = _safe_profile(item)
                    if profile:
                        profiles.add((profile["profile"], profile["codec"], profile["mode"]))

    if begin is None:
        warnings.append("بداية الجلسة غير متاحة")
    if end is None:
        warnings.append("لا يحتوي الملف على سجل نهاية؛ قد يكون التسجيل غير مكتمل")
    if not checkpoints:
        warnings.append("لا توجد نقاط تحقق؛ لا يمكن إعادة بناء الحالة الابتدائية")
    if counts["read_error"]:
        warnings.append("سُجلت أخطاء قراءة؛ لا يمكن اعتبار الفترات بينها دليلًا على استمرار الرصد")
    duration = round((last - first).total_seconds(), 3) if end else None
    wifi_values = [v for _, v in wifi_samples]
    battery_output = {}
    timeline = {"wifi": [[t.isoformat(), v] for t, v in _series_points(wifi_samples)], "battery": {}}
    for device, readings in sorted(batteries.items()):
        battery_output[device] = {
            "observations": len(readings),
            "min_percent": min(v for _, v in readings),
            "max_percent": max(v for _, v in readings),
            "values_observed": sorted({v for _, v in readings}),
        }
        timeline["battery"][device] = [[t.isoformat(), v] for t, v in _series_points(readings)]
    event_count = counts["state_change"]
    wifi_events = categories["wifi"]
    insights = []
    if event_count and wifi_events * 100 >= event_count * 80:
        insights.append("أغلب أحداث التغير تخص Wi-Fi؛ يوصى بتلخيص التذبذب مع الاحتفاظ بالسجل الأصلي.")
    if fields["bluetooth.battery"]:
        insights.append("تغيرت نسبة البطارية المبلّغ عنها؛ لا يثبت ذلك حدوث شحن أو تفريغ فعلي بالمعدل نفسه.")
    if profiles:
        insights.append("الملفات والترميزات المذكورة هي مشاهدات مسجلة، وليست ضمانًا لاستمرارها دون انقطاع بين القراءات.")
    if not insights:
        insights.append("المعطيات المتاحة لا تكفي لاستخلاص نمط تشخيصي محدد.")
    return {
        "analysis_schema_version": 1,
        "source_schema_version": 1,
        "session": {"start_utc": first.isoformat(), "end_utc": end.isoformat() if end else None,
                    "duration_seconds": duration, "ended_normally": end is not None},
        "counts": {"records": records, "types": dict(counts), "events_by_category": dict(categories),
                   "events_by_field": dict(fields), "read_errors": dict(errors)},
        "wifi": {"observations": len(wifi_values),
                 "min_percent": min(wifi_values) if wifi_values else None,
                 "max_percent": max(wifi_values) if wifi_values else None,
                 "mean_observed_percent": round(sum(wifi_values) / len(wifi_values), 2) if wifi_values else None,
                 "mean_method": "event_and_checkpoint_observations_not_time_weighted"},
        "battery": battery_output,
        "audio": {"observed_profiles": [
            {"profile": p, "codec": c, "mode": m} for p, c, m in sorted(profiles, key=str)],
            "error_observations": audio_error_observations},
        "timeline": timeline,
        "warnings": warnings,
        "interpretation": insights,
    }


def _graph(points, title, axis):
    if not points:
        return '<p class="muted">لا تتوفر قراءات لرسم هذا المؤشر.</p>'
    pairs = [(_time(t).timestamp(), float(v)) for t, v in points]
    min_t, max_t = pairs[0][0], pairs[-1][0]
    lo, hi = min(v for _, v in pairs), max(v for _, v in pairs)
    lo = max(0, lo - 5)
    hi = min(100, hi + 5)
    if hi <= lo:
        hi = lo + 1
    coords = []
    for t, value in pairs:
        x = 48 + 700 * ((t - min_t) / (max_t - min_t) if max_t > min_t else 0.5)
        y = 202 - 160 * (value - lo) / (hi - lo)
        coords.append(f"{x:.1f},{y:.1f}")
    return ('<section class="chart"><h3>' + escape(title) + '</h3>'
            '<svg viewBox="0 0 790 236" role="img" aria-label="' + escape(title) + '">'
            '<line x1="48" y1="202" x2="750" y2="202" stroke="#9aacc1"/>'
            '<line x1="48" y1="34" x2="48" y2="202" stroke="#9aacc1"/>'
            '<polyline points="' + " ".join(coords) +
            '" fill="none" stroke="#306bba" stroke-width="2" stroke-linejoin="round"/>'
            '<text x="4" y="38" font-size="13">' + str(round(hi, 1)) + '%</text>'
            '<text x="4" y="200" font-size="13">' + str(round(lo, 1)) + '%</text>'
            '<text x="50" y="222" font-size="12">' + escape(points[0][0][11:19]) + '</text>'
            '<text x="690" y="222" font-size="12">' + escape(points[-1][0][11:19]) + '</text>'
            '</svg><p class="muted">' + escape(axis) + '</p></section>')


def render_html(result):
    """Standalone Arabic HTML; only strictly sanitized values are interpolated."""
    session, counts, wifi = result["session"], result["counts"], result["wifi"]
    rows = "".join('<tr><td>' + escape(category) + '</td><td>' + str(value) + '</td></tr>'
                   for category, value in sorted(counts["events_by_category"].items()))
    profs = "".join('<li>' + escape(p["mode"]) + ' / ' + escape(p["codec"] or "غير معروف") +
                    ' — ' + escape(p["profile"]) + '</li>' for p in result["audio"]["observed_profiles"])
    cautions = "".join('<li>' + escape(x) + '</li>' for x in result["warnings"] + result["interpretation"])
    graphs = [_graph(result["timeline"]["wifi"], "إشارة Wi-Fi (%)",
                     "كل نقطة: ملاحظة من حدث تغير أو نقطة تحقق؛ المتوسط غير موزون زمنيًا. وصل النقاط بصري فقط وليس قياسًا مستمرًا.")]
    for device, series in list(result["timeline"]["battery"].items())[:4]:
        graphs.append(_graph(series, "بطارية " + device + " وفق المصدر (%)",
                             "النسبة المبلّغ عنها؛ وقت قياس الجهاز الأصلي غير معروف."))
    duration = session["duration_seconds"]
    duration_text = (f"{int(duration // 3600):02d}:{int(duration % 3600 // 60):02d}:"
                     f"{int(duration % 60):02d}" if duration is not None else "غير متاحة")
    return '''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>تحليل تشخيص أجهزة Linux — DCC</title><style>
body{font:16px "Noto Sans Arabic",sans-serif;line-height:1.7;max-width:1060px;margin:2rem auto;padding:0 1rem;color:#29394b;background:#f9fafc}
h1,h2,h3{color:#213955}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.card,.chart{background:white;border:1px solid #dce3ee;border-radius:10px;padding:18px;margin:12px 0}
.big{font-size:26px;font-weight:700}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #dce3ee;padding:8px;text-align:right}
.muted{color:#64748b;font-size:13px}svg{max-width:100%;height:auto}li{margin:.35rem 0}
</style></head><body><h1>تقرير التحليل التشخيصي</h1>
<p>تحليل محلي لبيانات مرصودة، لا يمثل قياسًا مباشرًا ولا يثبت السببية.</p><div class="grid">
<div class="card"><div>مدة الجلسة</div><div class="big">''' + duration_text + '''</div></div>
<div class="card"><div>عدد السجلات</div><div class="big">''' + str(counts["records"]) + '''</div></div>
<div class="card"><div>أحداث التغير</div><div class="big">''' + str(counts["types"].get("state_change", 0)) + '''</div></div>
<div class="card"><div>أخطاء القراءة</div><div class="big">''' + str(counts["types"].get("read_error", 0)) + '''</div></div></div>
<p>بداية الرصد (UTC): <b>''' + escape(session["start_utc"]) + '''</b>.
نهاية الجلسة (UTC): <b>''' + escape(session["end_utc"] or "غير مثبتة في الملف") + '''</b>.</p>
<h2>توزيع الأحداث</h2><table><thead><tr><th>الفئة</th><th>العدد</th></tr></thead><tbody>''' + rows + '''</tbody></table>
<h2>إشارة Wi-Fi</h2><p>المدى: ''' + escape(str(wifi["min_percent"])) + '% – ' + escape(str(wifi["max_percent"])) + '''%.
المتوسط الحسابي للقراءات المسجلة: ''' + escape(str(wifi["mean_observed_percent"])) + '''%،
<strong>غير موزون زمنيًا</strong> ولا يشير إلى جودة الاتصال بالضرورة.</p>''' + "".join(graphs) + '''
<h2>ملفات الصوت المرصودة</h2><ul>''' + (profs or '<li>غير متاحة</li>') + '''</ul>
<h2>قيود وتوصيات مشروطة بالأدلة</h2><ul>''' + cautions + '''</ul>
<p class="muted">لا يتضمن هذا التقرير أسماء الأجهزة أو MAC أو SSID. لا تُرفع أي بيانات إلى الإنترنت.</p>
</body></html>'''


def save_reports(result, directory=None):
    """Write JSON and HTML into the existing private report folder (0600)."""
    if directory is None:
        from dcc_diagnostics import prepare_reports_directory
        folder = prepare_reports_directory()
    else:
        from dcc_diagnostics import _private_directory
        folder = Path(directory)
        _private_directory(folder)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    basename = "dcc-analysis-" + stamp + "-" + uuid.uuid4().hex[:12]
    outputs = []
    try:
        for extension, body in (
            (".json", json.dumps(result, ensure_ascii=False, indent=2)),
            (".html", render_html(result)),
        ):
            path = folder / (basename + extension)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags, 0o600)
            outputs.append(path)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(body)
        return outputs[0], outputs[1]
    except Exception:
        for item in outputs:
            try:
                item.unlink()
            except OSError:
                pass
        raise


def main():
    parser = argparse.ArgumentParser(description="DCC: local Arabic JSONL analysis")
    parser.add_argument("input", help="Local JSONL session")
    parser.add_argument("--output-dir", help="Private output directory (default: DCC Reports)")
    args = parser.parse_args()
    try:
        result = analyse_file(args.input)
        json_path, html_path = save_reports(result, args.output_dir)
        print("JSON:", json_path)
        print("HTML:", html_path)
    except (AnalysisError, OSError, UnicodeError) as exc:
        parser.exit(2, "تعذر التحليل: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
