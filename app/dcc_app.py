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
from gi.repository import GLib  # noqa: E402

# Match the desktop basename on Wayland and WM_CLASS on X11. Set the
# program name before importing GTK so Python's filename isn't used.
APP_ID = "device-control-center"
GLib.set_prgname(APP_ID)
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402
Gdk.set_program_class(APP_ID)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dcc_core as core  # noqa: E402


CSS = b"""
.dcc-window { font-family: "Noto Sans Arabic", "DejaVu Sans", sans-serif; font-size: 13px; }
.dcc-heading { font-size: 21px; font-weight: bold; }
.dcc-subheading { font-size: 15px; font-weight: bold; }
.dcc-muted { opacity: 0.75; font-size: 12px; }
.dcc-card { border: 1px solid alpha(@theme_fg_color, .14); border-radius: 10px; background: alpha(@theme_base_color, .6); }
.dcc-battery { font-size: 28px; font-weight: bold; }
.dcc-low { color: @error_color; }
.dcc-section { margin-top: 8px; margin-bottom: 2px; }
.dcc-status { padding: 4px 10px; font-size: 11px; }
.dcc-window progressbar trough { min-height: 9px; border-radius: 5px; }
.dcc-window progressbar progress { min-height: 9px; border-radius: 5px; }
"""


def isolate(text):
    """Keep mixed Arabic/Latin device names and identifiers in logical order."""
    return "\u2068" + str(text) + "\u2069"


def label(text: str, style: str = "", wrap: bool = True) -> Gtk.Label:
    result = Gtk.Label(label=text)
    result.set_direction(Gtk.TextDirection.RTL)
    result.set_xalign(0.0)
    result.set_yalign(0.5)
    result.set_line_wrap(wrap)
    result.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    result.set_max_width_chars(42)
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


class AudioDevicePicker(Gtk.MenuButton):
    """An explicit-choice picker: wheel scrolling must not select audio devices.

    Gtk.ComboBoxText has internal event recipients that can eat wheel events
    before a scroll-event handler attached to the combobox sees them. A plain
    MenuButton with a click-only popover avoids scrolling through options when
    closed, while preserving the existing read/set-selection interface.
    """

    def __init__(self, changed):
        super().__init__()
        self._on_changed = changed
        self._options = []
        self._active_id = None
        self.set_hexpand(True)

        button_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._display = Gtk.Label(label="—")
        self._display.set_xalign(0.0)
        self._display.set_hexpand(True)
        self._display.set_ellipsize(Pango.EllipsizeMode.END)
        self._display.set_max_width_chars(32)
        button_content.pack_start(
            Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU),
            False, False, 0)
        button_content.pack_start(self._display, True, True, 0)
        self.add(button_content)

        self._choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._choices.set_border_width(6)
        popup = Gtk.Popover.new(self)
        popup.add(self._choices)
        self.set_popover(popup)
        button_content.show_all()

    def remove_all(self):
        self._options.clear()
        self._active_id = None
        self._display.set_text("—")
        for child in self._choices.get_children():
            self._choices.remove(child)

    def append(self, identifier, name):
        identifier = str(identifier)
        self._options.append((identifier, name))
        option = Gtk.Button.new_with_label(name)
        option.set_relief(Gtk.ReliefStyle.NONE)
        option.connect("clicked", lambda _button, key=identifier: self._choose(key))
        self._choices.pack_start(option, False, False, 0)
        option.show()

    def get_active_id(self):
        return self._active_id

    def set_active_id(self, identifier):
        for key, name in self._options:
            if key == str(identifier):
                self._active_id = key
                self._display.set_text(name)
                return True
        return False

    def select_first(self):
        if self._options:
            self.set_active_id(self._options[0][0])

    def _choose(self, identifier):
        previous = self._active_id
        if self.set_active_id(identifier):
            self.get_popover().popdown()
            if self._active_id != previous:
                self._on_changed(self)


class DccWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="مركز التحكم بالأجهزة")
        self.set_default_size(780, 620)
        self.get_style_context().add_class("dcc-window")
        self.set_size_request(440, 400)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name(APP_ID)
        self.connect("destroy", self._destroyed)
        self._closed = False

        self.data = None
        self._busy = False
        self._updating = False
        self._volume_timer = {"out": None, "in": None}
        self._action_running = False

        Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)
        provider = Gtk.CssProvider()
        self._css_provider = provider
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "مركز التحكم بالأجهزة"
        header.props.subtitle = "Bluetooth • الصوت • Wi-Fi"
        self.set_titlebar(header)
        self.refresh_button = button("تحديث", lambda _: self.refresh())
        header.pack_start(self.refresh_button)
        header.pack_end(button("بحث", lambda _: self.find_devices()))

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer.pack_start(self.scrolled, True, True, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_border_width(12)
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
        audio_box.set_border_width(12)
        audio_frame.add(audio_box)
        self.audio_info = label("", "dcc-muted")
        audio_box.pack_start(self.audio_info, False, False, 0)

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

        # Persistent actions never depend on the height of the scroll content.
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        footer.pack_start(Gtk.Separator(), False, False, 0)
        self.export_button = button("تصدير تقرير JSON", lambda _: self.export_report())
        self.export_button.set_margin_start(12)
        self.export_button.set_margin_end(12)
        self.export_button.set_margin_top(6)
        self.export_button.set_tooltip_text("حفظ محلي فقط؛ قد يتضمن التقرير عناوين الأجهزة وأسماء الشبكات")
        footer.pack_start(self.export_button, False, False, 0)
        self.status = label("جارٍ بدء الفحص…", "dcc-status", wrap=False)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        footer.pack_start(self.status, False, False, 0)
        outer.pack_end(footer, False, False, 0)
        outer.show_all()
        GLib.idle_add(self.refresh)
        self._refresh_timer = GLib.timeout_add_seconds(12, self._auto_refresh)

    def _destroyed(self, *_):
        self._closed = True
        Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), self._css_provider)
        GLib.source_remove(self._refresh_timer)
        for kind, timer in self._volume_timer.items():
            if timer is not None:
                GLib.source_remove(timer)
                self._volume_timer[kind] = None
        if Gtk.main_level():
            Gtk.main_quit()

    def _device_picker(self, container: Gtk.Box, title: str, kind: str) -> AudioDevicePicker:
        heading = label(title)
        container.pack_start(heading, False, False, 0)
        picker = AudioDevicePicker(lambda widget: self.select_audio(widget, kind))
        # The closed MenuButton does not have a ComboBox's wheel-selection
        # behavior. Explicitly forward any event sent directly to the button.
        picker.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        picker.connect("scroll-event", self._scroll_over_volume)
        container.pack_start(picker, False, False, 0)
        return picker

    def _volume_row(self, container: Gtk.Box, title: str, kind: str):
        container.pack_start(label(title, "dcc-muted"), False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        container.pack_start(row, False, False, 0)

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_hexpand(True)
        scale.set_size_request(80, -1)
        scale.set_draw_value(False)
        # Wheel and two-finger scrolling over a volume control must move the
        # containing page, never silently alter speaker/microphone volume.
        scale.connect("scroll-event", self._scroll_over_volume)
        scale.connect("value-changed", lambda w: self.schedule_volume(w, kind))
        row.pack_start(scale, True, True, 0)
        value = label("—")
        value.set_width_chars(6)
        row.pack_start(value, False, False, 0)
        mute = button("كتم / إلغاء", lambda _: self.action(lambda: core.toggle_mute(kind), "تغيير حالة الكتم"))
        row.pack_start(mute, False, False, 0)
        return scale, value, mute

    def _scroll_over_volume(self, _control, event):
        """Send wheel/touchpad vertical scrolling to the page, not audio controls.

        Returning True blocks GtkRange wheel handling and any direct MenuButton
        scroll event; choosing an audio device requires an explicit click.
        Explicit slider dragging, keyboard control, and picker selection remain.
        """
        adjustment = self.scrolled.get_vadjustment()
        step = max(32.0, adjustment.get_step_increment())
        direction = event.direction
        if direction == Gdk.ScrollDirection.UP:
            delta = -step
        elif direction == Gdk.ScrollDirection.DOWN:
            delta = step
        elif direction == Gdk.ScrollDirection.SMOOTH:
            valid, _dx, dy = event.get_scroll_deltas()
            if not valid:
                return True
            delta = dy * step
        else:
            # The page has no horizontal scrollbar. Suppress volume change.
            return True
        upper = max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(max(adjustment.get_lower(),
                                 min(upper, adjustment.get_value() + delta)))
        return True

    def _auto_refresh(self):
        self.refresh()
        return True

    def refresh(self):
        if self._closed or self._busy or self._action_running:
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
        if self._closed:
            return False
        if error:
            if self.data:
                previous = self.data["bluetooth"]
                self.data["bluetooth"] = core.retain_battery_history(
                    [dict(d, stale=True, battery_stale=True, battery_query_at=None) for d in previous], previous)
                self._render_devices(self.data)
            self.status.set_text("تعذر تحديث البيانات: " + error)
        else:
            if self.data:
                # Retain last successful devices only on explicit query failures.
                if result.get("bluetooth_error"):
                    result["bluetooth"] = [dict(d, stale=True, battery_stale=True,
                                                battery_query_at=result.get("bluetooth_query_at"))
                                           for d in self.data["bluetooth"]]
                else:
                    previous = {d["mac"]: d for d in self.data["bluetooth"]}
                    result["bluetooth"] = [dict(previous[d["mac"]], **d, stale=True,
                                                battery_stale=d.get("battery") is None)
                                           if d.get("error") and d["mac"] in previous else d
                                           for d in result["bluetooth"]]
            result["bluetooth"] = core.retain_battery_history(
                result["bluetooth"], self.data["bluetooth"] if self.data else [])
            self.data = result
            self._render_devices(result)
            self._render_audio(result["audio"])
            self._render_wifi(result["wifi"])
            self.status.set_text("آخر فحص: " + GLib.DateTime.new_now_local().format("%H:%M:%S") + " — تحديث آلي كل 12 ثانية")
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
            content.set_border_width(12)
            frame.add(content)
            status = "متصل" if dev.get("connected") else "غير متصل"
            if not dev.get("paired"):
                status = "مكتشف وغير مقترن"
            if dev.get("blocked"):
                status = "محظور"
            content.pack_start(label(f"{isolate(dev.get('alias') or dev.get('name') or 'جهاز Bluetooth')} — {status}", "dcc-subheading"), False, False, 0)
            content.pack_start(label(f"عنوان الجهاز: {isolate(dev['mac'])}", "dcc-muted"), False, False, 0)
            battery = dev.get("battery")
            if battery is None:
                content.pack_start(label("البطارية الحالية: غير متاحة من النظام"), False, False, 0)
                history = dev.get("battery_last_known")
                if history:
                    observed = history.get("observed_at")
                    moment = (GLib.DateTime.new_from_iso8601(observed, None).to_local().format("%H:%M:%S")
                              if observed else "وقت غير متاح")
                    content.pack_start(label(
                        f"آخر قيمة معروفة (قديمة): {isolate(str(history['percent']) + '%')} — "
                        f"{history.get('source') or 'مصدر غير متاح'}، {isolate(moment)}", "dcc-muted"), False, False, 0)
            else:
                content.pack_start(label(isolate(f"{battery}%"), "dcc-battery"), False, False, 0)
                progress = Gtk.ProgressBar()
                progress.set_fraction(max(0, min(100, battery)) / 100)
                progress.set_show_text(False)
                content.pack_start(progress, False, False, 0)
                if battery <= 20:
                    progress.get_style_context().add_class("dcc-low")
                    content.pack_start(label("شحن منخفض", "dcc-low"), False, False, 0)
            checked = dev.get("battery_checked_at")
            query = dev.get("battery_query_at")
            if query:
                moment = GLib.DateTime.new_from_iso8601(query, None).to_local().format("%H:%M:%S")
                content.pack_start(label(f"آخر محاولة فحص البطارية: {isolate(moment)}", "dcc-muted"), False, False, 0)
            if dev.get("battery_error"):
                content.pack_start(label(dev["battery_error"], "dcc-muted"), False, False, 0)
            source = dev.get("battery_source")
            if checked and source:
                moment = GLib.DateTime.new_from_iso8601(checked, None).to_local().format("%H:%M:%S")
                content.pack_start(label(f"آخر قراءة ناجحة من {source}: {isolate(moment)}", "dcc-muted"), False, False, 0)
                content.pack_start(label("آخر قيمة متاحة في النظام؛ وقت إبلاغ السماعة غير متاح.", "dcc-muted"), False, False, 0)
            if dev.get("stale"):
                content.pack_start(label("تعذرت قراءة حالة الجهاز؛ قد تكون حالة الاتصال قديمة.", "dcc-low"), False, False, 0)
            rssi = dev.get("rssi")
            content.pack_start(label("قوة إشارة Bluetooth (RSSI): " + (f"{rssi} dBm" if rssi is not None else "غير متاحة حاليًا (لا تعني ضعف الاتصال)"), "dcc-muted"), False, False, 0)
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            content.pack_start(actions, False, False, 0)
            if dev.get("connected"):
                btn = button("قطع الاتصال", lambda _, mac=dev["mac"]: self.action(lambda: core.bluetooth_action(mac, "disconnect"), "قطع الاتصال"))
            elif dev.get("paired"):
                btn = button("اتصال", lambda _, mac=dev["mac"]: self.action(lambda: core.bluetooth_action(mac, "connect"), "الاتصال بالجهاز"))
            else:
                btn = button("محاولة إقران", lambda _, mac=dev["mac"]: self.action(lambda: core.bluetooth_action(mac, "pair"), "إقران الجهاز"))
            btn.set_sensitive(not dev.get("stale", False))
            actions.pack_start(btn, False, False, 0)
            self.bt_box.pack_start(frame, False, False, 0)
        self.bt_box.show_all()
        GLib.idle_add(self._restore_scroll, scroll_y)

    def _restore_scroll(self, amount):
        if self._closed:
            return False
        adj = self.scrolled.get_vadjustment()
        adj.set_value(min(amount, max(0, adj.get_upper() - adj.get_page_size())))
        return False

    def _render_audio(self, data):
        self._updating = True
        self.audio_info.set_text("تعذرت قراءة الصوت: " + data["error"] if data.get("error") else "الأجهزة والمستويات الافتراضية في النظام")
        try:
            for kind, combo in (("sinks", self.out_combo), ("sources", self.in_combo)):
                current = combo.get_active_id()
                combo.remove_all()
                for node in data.get(kind, []):
                    combo.append(str(node["id"]), isolate(node["name"]))
                default = next((str(x["id"]) for x in data.get(kind, []) if x["default"]), current)
                if default:
                    combo.set_active_id(default)
                elif data.get(kind):
                    combo.select_first()
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
            strength = wifi.get("signal")
            strength_text = f"{strength}%" if strength is not None else "غير متاحة"
            freq = wifi.get("freq") or "—"
            self.wifi_info.set_text(
                f"الشبكة: {isolate(wifi.get('ssid') or 'شبكة غير مسماة')}   |   "
                f"قوة إشارة Wi-Fi: {strength_text}   |   التردد: {freq}"
            )
        elif wifi.get("error"):
            self.wifi_info.set_text("بيانات Wi-Fi غير متاحة: " + wifi["error"])
        else:
            self.wifi_info.set_text("لا توجد شبكة Wi-Fi متصلة حاليًا، أو لم يتعرف NetworkManager عليها.")

    def schedule_volume(self, widget, kind):
        if self._closed or self._updating or self.data is None:
            return
        existing = self._volume_timer[kind]
        if existing is not None:
            GLib.source_remove(existing)
        value = int(round(widget.get_value()))
        self._volume_timer[kind] = GLib.timeout_add(420, self._commit_volume, kind, value)

    def _commit_volume(self, kind, value):
        if self._closed:
            self._volume_timer[kind] = None
            return False
        if self._action_running or self._busy:
            return True
        self._volume_timer[kind] = None
        self.action(lambda: core.set_volume(kind, value), "تغيير مستوى الصوت", refresh=False)
        return False

    def select_audio(self, combo, kind):
        if self._closed or self._updating or self.data is None:
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
        if self._closed or self._action_running:
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
        if self._closed:
            return False
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
