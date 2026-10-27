#!/usr/bin/python3
"""Arabic RTL GTK desktop interface for locally attached devices."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dcc_core as core  # noqa: E402


CSS = b"""
.dcc-heading { font-size: 19px; font-weight: 700; }
.dcc-subheading { font-size: 15px; font-weight: bold; }
.dcc-muted { opacity: 0.72; }
.dcc-card { border: 1px solid alpha(@theme_fg_color, .16); border-radius: 12px; padding: 13px; margin-bottom: 5px; }
.dcc-padded { padding: 8px; }
.dcc-pill { padding: 4px 7px; border-radius: 7px; background: alpha(@theme_selected_bg_color, .12); }
.dcc-section { margin-top: 11px; margin-bottom: 5px; }
.dcc-status { padding: 7px; }
"""


def label(text: str, style: str = "", wrap: bool = True) -> Gtk.Label:
    result = Gtk.Label(label=text)
    result.set_xalign(1.0)
    result.set_yalign(0.5)
    result.set_line_wrap(wrap)
    result.set_selectable(False)
    if style:
        result.get_style_context().add_class(style)
    return result


def button(text: str, callback, sensitive: bool = True) -> Gtk.Button:
    btn = Gtk.Button.new_with_label(text)
    btn.set_sensitive(sensitive)
    btn.connect("clicked", callback)
    return btn


def clear_box(box: Gtk.Box) -> None:
    for child in box.get_children():
        box.remove(child)


class DccWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="مركز التحكم بالأجهزة")
        self.set_default_size(840, 630)
        self.set_size_request(520, 470)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("bluetooth")
        self.connect("destroy", Gtk.main_quit)

        self.data = None
        self._busy = False
        self._updating = False
        self._volume_timer = {"out": None, "in": None}
        self._action_running = False

        Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "مركز التحكم بالأجهزة"
        header.props.subtitle = "Bluetooth • الصوت • Wi-Fi"
        self.set_titlebar(header)
        header.pack_start(button("تحديث", lambda _: self.refresh()))
        header.pack_end(button("بحث عن أجهزة", lambda _: self.find_devices()))

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer.pack_start(self.scrolled, True, True, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_border_width(17)
        self.scrolled.add(body)

        body.pack_start(label("الأجهزة المتصلة بالحاسوب", "dcc-heading"), False, False, 0)
        body.pack_start(label("معلومات حقيقية من Ubuntu؛ القيم غير المدعومة لا تُقدّر افتراضيًا.", "dcc-muted"), False, False, 0)

        self.bt_header = label("Bluetooth", "dcc-subheading")
        self.bt_header.get_style_context().add_class("dcc-section")
        body.pack_start(self.bt_header, False, False, 0)
        self.bt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.pack_start(self.bt_box, False, False, 0)

        self.audio_header = label("الصوت والميكروفون", "dcc-subheading")
        self.audio_header.get_style_context().add_class("dcc-section")
        body.pack_start(self.audio_header, False, False, 0)
        audio_frame = Gtk.Frame()
        audio_frame.get_style_context().add_class("dcc-card")
        body.pack_start(audio_frame, False, False, 0)
        audio_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        audio_box.set_border_width(8)
        audio_frame.add(audio_box)

        self.out_combo = self._device_picker(audio_box, "جهاز إخراج الصوت", "sinks")
        self.out_scale, self.out_value, self.out_mute = self._volume_row(audio_box, "مستوى صوت الإخراج", "out")
        audio_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)
        self.in_combo = self._device_picker(audio_box, "جهاز إدخال الصوت / الميكروفون", "sources")
        self.in_scale, self.in_value, self.in_mute = self._volume_row(audio_box, "مستوى ضبط إدخال الميكروفون", "in")
        audio_box.pack_start(label("تنبيه: هذه النسب تعبّر عن ضبط الصوت، وليست قياسًا مباشرًا لشدة الإشارة المسموعة أو الملتقطة.", "dcc-muted"), False, False, 0)

        self.wifi_header = label("Wi-Fi", "dcc-subheading")
        self.wifi_header.get_style_context().add_class("dcc-section")
        body.pack_start(self.wifi_header, False, False, 0)
        wifi_frame = Gtk.Frame()
        wifi_frame.get_style_context().add_class("dcc-card")
        body.pack_start(wifi_frame, False, False, 0)
        self.wifi_info = label("جارٍ فحص الشبكة…")
        self.wifi_info.set_margin_top(8)
        self.wifi_info.set_margin_bottom(8)
        self.wifi_info.set_margin_start(8)
        self.wifi_info.set_margin_end(8)
        wifi_frame.add(self.wifi_info)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        body.pack_start(bottom, False, False, 0)
        bottom.pack_start(button("تصدير تقرير JSON", lambda _: self.export_report()), False, False, 0)
        bottom.pack_start(label("التقرير يُحفظ محليًا فقط. لا يوجد إرسال بيانات إلى خوادم.", "dcc-muted"), True, True, 0)
        self.status = label("جاري بدء الفحص…", "dcc-status")
        outer.pack_end(self.status, False, False, 0)
        outer.show_all()
        GLib.idle_add(self.refresh)
        GLib.timeout_add_seconds(12, self._auto_refresh)

    def _device_picker(self, container: Gtk.Box, title: str, kind: str) -> Gtk.ComboBoxText:
        heading = label(title)
        container.pack_start(heading, False, False, 0)
        combo = Gtk.ComboBoxText()
        combo.set_hexpand(True)
        container.pack_start(combo, False, False, 0)
        combo.connect("changed", lambda widget: self.select_audio(widget, kind))
        return combo

    def _volume_row(self, container: Gtk.Box, title: str, kind: str):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        container.pack_start(row, False, False, 0)
        row.pack_start(label(title), False, False, 0)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_hexpand(True)
        scale.set_draw_value(False)
        scale.connect("value-changed", lambda w: self.schedule_volume(w, kind))
        row.pack_start(scale, True, True, 0)
        value = label("—")
        value.set_width_chars(6)
        row.pack_start(value, False, False, 0)
        mute = button("كتم / إلغاء", lambda _: self.action(lambda: core.toggle_mute(kind), "تغيير حالة الكتم"))
        row.pack_start(mute, False, False, 0)
        return scale, value, mute

    def _auto_refresh(self):
        self.refresh()
        return True

    def refresh(self):
        if self._busy or self._action_running:
            return False
        self._busy = True
        self.status.set_text("جارٍ قراءة حالة الأجهزة…")

        def work():
            try:
                result = core.snapshot()
                GLib.idle_add(self._updated, result, None)
            except Exception as exc:
                GLib.idle_add(self._updated, None, str(exc))
        threading.Thread(target=work, daemon=True).start()
        return False

    def _updated(self, result, error):
        self._busy = False
        if error:
            self.status.set_text("تعذر تحديث البيانات: " + error)
        else:
            self.data = result
            self._render_devices(result)
            self._render_audio(result["audio"])
            self._render_wifi(result["wifi"])
            self.status.set_text("آخر تحديث: " + GLib.DateTime.new_now_local().format("%H:%M:%S") + " — تحديث آلي كل 12 ثانية")
        return False

    def _render_devices(self, data):
        scroll_y = self.scrolled.get_vadjustment().get_value()
        clear_box(self.bt_box)
        devices = data.get("bluetooth", [])
        if data.get("bluetooth_error"):
            self.bt_box.pack_start(label("تعذر قراءة Bluetooth: " + data["bluetooth_error"]), False, False, 0)
        elif not devices:
            self.bt_box.pack_start(label("لا تظهر أجهزة مقترنة حاليًا. استخدم «بحث عن أجهزة» للكشف عن الأجهزة القريبة."), False, False, 0)
        for dev in devices:
            frame = Gtk.Frame()
            frame.get_style_context().add_class("dcc-card")
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            content.set_border_width(7)
            frame.add(content)
            status = "متصل" if dev.get("connected") else "غير متصل"
            if not dev.get("paired"):
                status = "مكتشف وغير مقترن"
            if dev.get("blocked"):
                status = "محظور"
            content.pack_start(label(f"{dev.get('alias') or dev.get('name') or 'جهاز Bluetooth'} — {status}", "dcc-subheading"), False, False, 0)
            content.pack_start(label(f"عنوان الجهاز: {dev['mac']}", "dcc-muted"), False, False, 0)
            battery = dev.get("battery")
            if battery is None:
                content.pack_start(label("البطارية: لا يوفر النظام قراءة متاحة لهذا الجهاز"), False, False, 0)
            else:
                content.pack_start(label(f"بطارية الجهاز: {battery}%"), False, False, 0)
                progress = Gtk.ProgressBar()
                progress.set_fraction(max(0, min(100, battery)) / 100)
                progress.set_show_text(False)
                content.pack_start(progress, False, False, 0)
                if battery <= 20:
                    content.pack_start(label("تنبيه: مستوى البطارية منخفض"), False, False, 0)
            rssi = dev.get("rssi")
            content.pack_start(label("قوة الإشارة (RSSI): " + (f"{rssi} dBm" if rssi is not None else "غير متاحة حاليًا"), "dcc-muted"), False, False, 0)
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            content.pack_start(actions, False, False, 0)
            if dev.get("connected"):
                btn = button("قطع الاتصال", lambda _, mac=dev["mac"]: self.action(lambda: core.bluetooth_action(mac, "disconnect"), "قطع الاتصال"))
            elif dev.get("paired"):
                btn = button("اتصال", lambda _, mac=dev["mac"]: self.action(lambda: core.bluetooth_action(mac, "connect"), "الاتصال بالجهاز"))
            else:
                btn = button("محاولة إقران", lambda _, mac=dev["mac"]: self.action(lambda: core.bluetooth_action(mac, "pair"), "إقران الجهاز"))
            actions.pack_start(btn, False, False, 0)
            self.bt_box.pack_start(frame, False, False, 0)
        self.bt_box.show_all()
        GLib.idle_add(self._restore_scroll, scroll_y)

    def _restore_scroll(self, amount):
        adj = self.scrolled.get_vadjustment()
        adj.set_value(min(amount, max(0, adj.get_upper() - adj.get_page_size())))
        return False

    def _render_audio(self, data):
        self._updating = True
        try:
            for kind, combo in (("sinks", self.out_combo), ("sources", self.in_combo)):
                current = combo.get_active_id()
                combo.remove_all()
                for node in data.get(kind, []):
                    combo.append(str(node["id"]), node["name"])
                default = next((str(x["id"]) for x in data.get(kind, []) if x["default"]), current)
                if default:
                    combo.set_active_id(default)
                elif data.get(kind):
                    combo.set_active(0)
                combo.set_sensitive(bool(data.get(kind)))
            for kind, scale, value, mute in (("out", self.out_scale, self.out_value, self.out_mute),
                                             ("in", self.in_scale, self.in_value, self.in_mute)):
                volume = data.get(kind, {})
                percent = volume.get("percent")
                pending = self._volume_timer.get(kind)
                if percent is not None and pending is None:
                    scale.set_value(min(100, percent))
                scale.set_sensitive(percent is not None)
                value.set_text("—" if percent is None else f"{percent}%" + (" · مكتوم" if volume.get("muted") else ""))
                mute.set_sensitive(percent is not None)
        finally:
            self._updating = False

    def _render_wifi(self, wifi):
        if wifi.get("connected"):
            self.wifi_info.set_text(f"الشبكة: {wifi.get('ssid', '')}   |   قوة الإشارة: {wifi.get('signal', '—')}%   |   التردد: {wifi.get('freq', '—')}")
        elif wifi.get("error"):
            self.wifi_info.set_text("بيانات Wi-Fi غير متاحة: " + wifi["error"])
        else:
            self.wifi_info.set_text("لا توجد شبكة Wi-Fi متصلة حاليًا، أو لم يتعرف NetworkManager عليها.")

    def schedule_volume(self, widget, kind):
        if self._updating or self.data is None:
            return
        existing = self._volume_timer[kind]
        if existing is not None:
            GLib.source_remove(existing)
        value = int(round(widget.get_value()))
        self._volume_timer[kind] = GLib.timeout_add(420, self._commit_volume, kind, value)

    def _commit_volume(self, kind, value):
        self._volume_timer[kind] = None
        self.action(lambda: core.set_volume(kind, value), "تغيير مستوى الصوت", refresh=False)
        return False

    def select_audio(self, combo, kind):
        if self._updating or self.data is None:
            return
        selected = combo.get_active_id()
        if not selected:
            return
        audio = self.data["audio"]
        default = next((x for x in audio.get(kind, []) if x.get("default")), None)
        if default is not None and str(default["id"]) == selected:
            return
        self.action(lambda: core.set_default_audio(int(selected), kind, audio), "تغيير جهاز الصوت الافتراضي")

    def action(self, operation, description, refresh=True):
        if self._action_running:
            return
        self._action_running = True
        self.status.set_text("جارٍ تنفيذ: " + description + "…")

        def worker():
            try:
                operation()
                GLib.idle_add(self._action_done, description, None, refresh)
            except Exception as exc:
                GLib.idle_add(self._action_done, description, str(exc), refresh)
        threading.Thread(target=worker, daemon=True).start()

    def _action_done(self, description, error, refresh):
        self._action_running = False
        if error:
            self.status.set_text(f"لم ينجح «{description}»: {error}")
            self._dialog("تعذّر تنفيذ الأمر", error)
        else:
            self.status.set_text("تم تنفيذ: " + description)
        if refresh:
            self.refresh()
        return False

    def find_devices(self):
        self.action(core.scan, "البحث عن الأجهزة القريبة")

    def _dialog(self, title, message):
        dialog = Gtk.MessageDialog(transient_for=self, flags=Gtk.DialogFlags.MODAL,
                                   message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK,
                                   text=title)
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def export_report(self):
        if self.data is None:
            self._dialog("لا توجد بيانات", "انتظر اكتمال القراءة الأولى ثم جرّب مجددًا.")
            return
        dialog = Gtk.FileChooserDialog(title="حفظ تقرير الأجهزة", transient_for=self,
                                        action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons("إلغاء", Gtk.ResponseType.CANCEL, "حفظ", Gtk.ResponseType.OK)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name("device-control-report.json")
        try:
            if dialog.run() == Gtk.ResponseType.OK:
                path = dialog.get_filename()
                try:
                    with open(path, "w", encoding="utf-8") as file:
                        json.dump({"schema_version": 1, **self.data}, file, ensure_ascii=False, indent=2)
                    self.status.set_text("تم حفظ التقرير محليًا: " + path)
                except OSError as exc:
                    self._dialog("فشل حفظ الملف", str(exc))
        finally:
            dialog.destroy()


def main():
    win = DccWindow()
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
