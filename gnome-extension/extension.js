import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {connectedBatteries, chooseDisplay} from './battery-state.js';

const BLUEZ = 'org.bluez';
const UPOWER = 'org.freedesktop.UPower';
const DBUS_PROPERTIES = 'org.freedesktop.DBus.Properties';
const OBJECT_MANAGER = 'org.freedesktop.DBus.ObjectManager';
const DEVICE_IFACE = 'org.freedesktop.UPower.Device';

function unpack(value) {
    if (value && typeof value.deep_unpack === 'function')
        return unpack(value.deep_unpack());
    if (Array.isArray(value))
        return value.map(unpack);
    if (value !== null && typeof value === 'object')
        return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, unpack(item)]));
    return value;
}

export default class DeviceControlBatteryExtension extends Extension {
    enable() {
        this._alive = true;
        this._cancellable = new Gio.Cancellable();
        this._connection = null;
        this._signalIds = [];
        this._refreshId = 0;
        this._bluezObjects = {};
        this._upowerDevices = [];
        this._preferredPath = null;
        this._fetchVersions = {bluez: 0, upower: 0};

        this._indicator = new PanelMenu.Button(0.0, 'بطارية أجهزة Bluetooth', false);
        const container = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        container.add_child(new St.Icon({icon_name: 'bluetooth-symbolic',
            style_class: 'system-status-icon'}));
        this._batteryLabel = new St.Label({text: '—',
            y_align: Clutter.ActorAlign.CENTER});
        container.add_child(this._batteryLabel);
        this._indicator.add_child(container);
        Main.panel.addToStatusArea('dcc-bluetooth-battery', this._indicator, 0, 'right');
        this._render();

        Gio.bus_get(Gio.BusType.SYSTEM, this._cancellable, (_source, result) => {
            if (!this._alive)
                return;
            try {
                this._connection = Gio.bus_get_finish(result);
                this._subscribe();
                this._refresh();
            } catch (error) {
                // An inaccessible bus is unavailable, not 0% battery.
                console.warn('Device Control Center battery: ' + error.message);
            }
        });
    }

    _subscribe() {
        const connection = this._connection;
        const subscribe = (sender, iface, signal, path, callback) => {
            this._signalIds.push(connection.signal_subscribe(sender, iface, signal,
                path, null, Gio.DBusSignalFlags.NONE, callback));
        };
        const queue = () => this._scheduleRefresh();
        subscribe(BLUEZ, OBJECT_MANAGER, 'InterfacesAdded', null, queue);
        subscribe(BLUEZ, OBJECT_MANAGER, 'InterfacesRemoved', null, queue);
        subscribe(BLUEZ, DBUS_PROPERTIES, 'PropertiesChanged', null,
            (_c, _sender, _path, _iface, _signal, args) => {
                const [interfaceName] = unpack(args);
                if (interfaceName === 'org.bluez.Device1' || interfaceName === 'org.bluez.Battery1')
                    queue();
            });
        subscribe(UPOWER, UPOWER, 'DeviceAdded', '/org/freedesktop/UPower', queue);
        subscribe(UPOWER, UPOWER, 'DeviceRemoved', '/org/freedesktop/UPower', queue);
        subscribe(UPOWER, DBUS_PROPERTIES, 'PropertiesChanged', null,
            (_c, _sender, path, _iface, _signal, args) => {
                const [interfaceName] = unpack(args);
                if (path.startsWith('/org/freedesktop/UPower/devices/') && interfaceName === DEVICE_IFACE)
                    queue();
            });
        subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
            '/org/freedesktop/DBus', (_c, _sender, _path, _iface, _signal, args) => {
                const [name] = unpack(args);
                if (name === BLUEZ) {
                    this._bluezObjects = {};
                    queue();
                    this._render();
                } else if (name === UPOWER) {
                    this._upowerDevices = [];
                    queue();
                    this._render();
                }
            });
    }

    _scheduleRefresh() {
        if (!this._alive || this._refreshId)
            return;
        this._refreshId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
            this._refreshId = 0;
            this._refresh();
            return GLib.SOURCE_REMOVE;
        });
    }

    _call(destination, path, iface, method, params = null) {
        return new Promise((resolve, reject) => {
            this._connection.call(destination, path, iface, method, params, null,
                Gio.DBusCallFlags.NONE, 5000, this._cancellable, (connection, result) => {
                    try {
                        resolve(unpack(connection.call_finish(result)));
                    } catch (error) {
                        reject(error);
                    }
                });
        });
    }

    _refresh() {
        if (!this._alive || !this._connection)
            return;
        void this._readBluez();
        void this._readUpower();
    }

    async _readBluez() {
        const version = ++this._fetchVersions.bluez;
        try {
            const [objects] = await this._call(BLUEZ, '/', OBJECT_MANAGER, 'GetManagedObjects');
            if (!this._alive || version !== this._fetchVersions.bluez)
                return;
            this._bluezObjects = objects;
        } catch (_error) {
            if (!this._alive || version !== this._fetchVersions.bluez)
                return;
            this._bluezObjects = {};
        }
        this._render();
    }

    async _readUpower() {
        const version = ++this._fetchVersions.upower;
        let devices = [];
        try {
            const [paths] = await this._call(UPOWER, '/org/freedesktop/UPower',
                UPOWER, 'EnumerateDevices');
            const responses = await Promise.allSettled(paths.slice(0, 40).map(path =>
                this._call(UPOWER, path, DBUS_PROPERTIES, 'GetAll',
                    new GLib.Variant('(s)', [DEVICE_IFACE]))));
            devices = responses.filter(item => item.status === 'fulfilled')
                .map(item => item.value[0]);
        } catch (_error) {
            // The BlueZ reading remains useful when UPower is unavailable.
        }
        if (!this._alive || version !== this._fetchVersions.upower)
            return;
        this._upowerDevices = devices;
        this._render();
    }

    _render() {
        if (!this._alive || !this._indicator)
            return;
        const devices = connectedBatteries(this._bluezObjects, this._upowerDevices);
        const {device, text} = chooseDisplay(devices, this._preferredPath);
        this._batteryLabel.text = text;
        this._indicator.menu.removeAll();
        const heading = new PopupMenu.PopupMenuItem(device
            ? device.name + ': ' + text : 'لا توجد أجهزة Bluetooth متصلة', {reactive: false});
        heading.setSensitive(false);
        this._indicator.menu.addMenuItem(heading);
        for (const entry of devices) {
            const percent = entry.battery === null ? '—' : String(entry.battery) + '%';
            const item = new PopupMenu.PopupMenuItem(entry.name + ' — ' + percent);
            item.connect('activate', () => {
                this._preferredPath = entry.path;
                this._render();
            });
            this._indicator.menu.addMenuItem(item);
        }
        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const launch = new PopupMenu.PopupMenuItem('فتح مركز التحكم بالأجهزة');
        launch.connect('activate', () => {
            try {
                const app = Gio.DesktopAppInfo.new('device-control-center.desktop');
                if (app)
                    app.launch([], null);
            } catch (error) {
                console.warn('Device Control Center launcher: ' + error.message);
            }
        });
        this._indicator.menu.addMenuItem(launch);
    }

    disable() {
        this._alive = false;
        if (this._refreshId)
            GLib.Source.remove(this._refreshId);
        this._refreshId = 0;
        this._cancellable?.cancel();
        if (this._connection) {
            for (const id of this._signalIds)
                this._connection.signal_unsubscribe(id);
        }
        this._signalIds = [];
        this._connection = null;
        this._indicator?.destroy();
        this._indicator = null;
        this._batteryLabel = null;
        this._cancellable = null;
        this._bluezObjects = {};
        this._upowerDevices = [];
    }
}
