// Pure, side-effect-free projection of BlueZ and UPower D-Bus snapshots.
const DEVICE = 'org.bluez.Device1';
const BATTERY = 'org.bluez.Battery1';

function validPercent(value) {
    return typeof value === 'number' && Number.isFinite(value) &&
        value >= 0 && value <= 100 ? Math.round(value) : null;
}

export function connectedBatteries(bluezObjects = {}, upowerDevices = []) {
    const devices = [];
    const paths = Object.keys(bluezObjects).sort();
    for (const path of paths) {
        const record = bluezObjects[path] || {};
        const device = record[DEVICE];
        if (!device || device.Connected !== true)
            continue;
        const name = String(device.Alias || device.Name || 'Bluetooth');
        let battery = validPercent(record[BATTERY]?.Percentage);
        let source = battery === null ? null : 'BlueZ';
        // Component batteries may appear beneath an opaque child path.
        if (battery === null) {
            for (const [otherPath, other] of Object.entries(bluezObjects)) {
                if (otherPath === path || !other[BATTERY])
                    continue;
                if (other[BATTERY].Device === path) {
                    const candidate = validPercent(other[BATTERY].Percentage);
                    if (candidate !== null) {
                        battery = candidate;
                        source = 'BlueZ';
                        break;
                    }
                }
            }
        }
        if (battery === null) {
            const fallback = upowerDevices.find(item =>
                typeof item.NativePath === 'string' &&
                (item.NativePath === path || item.NativePath.startsWith(path + '/')) &&
                validPercent(item.Percentage) !== null &&
                item.IsPresent !== false
            );
            if (fallback) {
                battery = validPercent(fallback.Percentage);
                source = 'UPower';
            }
        }
        devices.push({path, name, battery, source,
            isAudio: /audio|headset|headphones/i.test(String(device.Icon || ''))});
    }
    return devices.sort((a, b) =>
        Number(b.battery !== null) - Number(a.battery !== null) ||
        Number(b.isAudio) - Number(a.isAudio) ||
        a.name.localeCompare(b.name));
}

export function chooseDisplay(devices, preferredPath = null) {
    const device = devices.find(d => d.path === preferredPath) || devices[0] || null;
    return {device, text: device?.battery === null || !device ? '—' : String(device.battery) + '%',
        source: device?.source || null};
}
