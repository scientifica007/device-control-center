import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

const source = readFileSync(resolve('gnome-extension/battery-state.js'), 'utf8');
const {connectedBatteries, chooseDisplay} =
    await import('data:text/javascript,' + encodeURIComponent(source));
const DEV = 'org.bluez.Device1';
const BAT = 'org.bluez.Battery1';
const p = '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF';
const second = '/org/bluez/hci0/dev_11_22_33_44_55_66';
const object = (pct, connected = true) => ({[p]: {[DEV]: {
    Alias: 'Demo Headset', Icon: 'audio-headset', Connected: connected},
    ...(pct === undefined ? {} : {[BAT]: {Percentage: pct}})}});
let rows = connectedBatteries(object(90));
assert.equal(chooseDisplay(rows).text, '90%');
assert.equal(rows[0].source, 'BlueZ');
rows = connectedBatteries(object());
assert.equal(chooseDisplay(rows).text, '—');
rows = connectedBatteries(object(), [{NativePath: p, Percentage: 80, IsPresent: true}]);
assert.equal(rows[0].battery, 80);
assert.equal(rows[0].source, 'UPower');
rows = connectedBatteries(object(), [{NativePath: '/other/path', Percentage: 90}]);
assert.equal(rows[0].battery, null);
rows = connectedBatteries(object(101));
assert.equal(rows[0].battery, null);
rows = connectedBatteries(object(0));
assert.equal(rows[0].battery, 0);
rows = connectedBatteries(object(70, false));
assert.equal(rows.length, 0);
rows = connectedBatteries({...object(60), [second]: {[DEV]:
    {Alias: 'Mouse', Connected: true}, [BAT]: {Percentage: 50}}});
assert.equal(chooseDisplay(rows, second).text, '50%');
assert.equal(chooseDisplay(rows, '/missing').text, '60%');
rows = connectedBatteries({[p]: {[DEV]: {Alias: 'Demo', Connected: true}},
    [p + '/battery_component']: {[BAT]: {Device: p, Percentage: 54}}});
assert.equal(rows[0].battery, 54);
assert.equal(chooseDisplay([]).text, '—');
console.log('PASS: Bluetooth battery state projection and fallbacks');
