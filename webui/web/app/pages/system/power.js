// System › Restart & reset: restart, shut down, factory reset, hardware reset button, scheduled restart.
import { h, clear } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { card, kv, badge, alert, button, spinner, errorBox } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { waitForRestart, notify } from './_common.js';

const DAYS = [['mon', 'Mon'], ['tue', 'Tue'], ['wed', 'Wed'], ['thu', 'Thu'], ['fri', 'Fri'], ['sat', 'Sat'], ['sun', 'Sun']];
const CAUSES = { reboot: 'Restart', power_key: 'Power button', charger: 'Charger connected', usb: 'USB connected' };

async function reboot() {
  try {
    const r = await post('/api/system/power/reboot', {}, {
      confirmTitle: 'Restart the router',
      confirmMessage: 'The router restarts now. The Internet connection and all connected devices are interrupted for about two minutes.',
    });
    waitForRestart({ instance: r.instance, title: 'Restarting', expectedS: r.expected_s || 120,
      message: 'The router is restarting. This page reloads when it is back; you will need to log in again.' });
  } catch (e) { notify(e); }
}

async function shutdown() {
  try {
    const r = await post('/api/system/power/shutdown', {}, {
      confirmTitle: 'Turn the router off',
      confirmMessage: 'The router switches off completely. It cannot be turned on again from this page or over the network: '
        + 'someone has to press its power button.',
      confirmPhrase: 'OFF',
    });
    waitForRestart({ instance: r.instance, title: 'Shutting down', shutdown: true,
      message: 'The router is switching off. Press and hold the power button to turn it on again.' });
  } catch (e) { notify(e); }
}

async function factoryReset() {
  try {
    const r = await post('/api/system/power/factory-reset', {}, {
      confirmTitle: 'Factory reset',
      confirmMessage: 'Every setting is erased and the router restarts with its factory settings. This cannot be undone. '
        + 'Make a backup first if you may want the settings back.',
      confirmPhrase: 'RESET',
    });
    waitForRestart({ instance: r.instance, title: 'Factory reset', expectedS: r.expected_s || 180,
      message: 'The router is erasing its settings and restarting.',
      tips: 'Afterwards the Wi-Fi name, the Wi-Fi password and the admin password are the ones printed on the router\'s label, '
        + 'and its address is the default one (usually 192.168.1.1). Reconnect your device if needed.' });
  } catch (e) { notify(e); }
}

function statusCard(host) {
  const body = h('div', spinner());
  host.appendChild(card('Power', body, { icon: 'power' }));
  return (d) => {
    clear(body).append(kv([
      ['Running for', fmt.duration(d.uptime_s)],
      ['Started', fmt.dateTime(d.boot_time)],
      ['Last start reason', d.power_on_cause ? (CAUSES[d.power_on_cause] || d.power_on_cause) : ''],
      ['Power source', d.power_state ? d.power_state.replace(/_/g, ' ') : ''],
      ['Router time', `${d.router_time} (${d.router_timezone})`],
    ]));
  };
}

function actionsCard(host) {
  host.appendChild(card('Restart or turn off', [
    h('p.card-desc', 'Restarting fixes most temporary problems. Settings are kept.'),
    h('div.form-actions', button('Restart', reboot, { kind: 'primary', icon: 'refresh' }), button('Turn off', shutdown, { icon: 'power' })),
    h('p.hint', { style: { marginTop: '10px' } }, 'Turning off is permanent until someone presses the power button on the router.'),
  ], { icon: 'refresh' }));
}

function factoryCard(host) {
  host.appendChild(card('Factory reset', [
    alert('warn', 'Erases all settings and restarts the router. This cannot be undone.'),
    h('p', { style: { margin: '12px 0 6px' } }, 'What is lost:'),
    h('ul', { style: { margin: '0 0 12px', paddingLeft: '20px' } }, [
      'Wi-Fi names, Wi-Fi passwords and the admin password (back to the ones on the label)',
      'Mobile network settings: APN profiles, network mode, roaming, data limits',
      'Local network settings: router address, DHCP and reserved addresses, DNS',
      'Firewall, port forwarding, port filtering, DMZ, VPN and other advanced settings',
      'Usage statistics, messages kept by the vendor software and event logs',
    ].map(t => h('li', t))),
    h('p.hint', 'This interface itself is installed separately; if it is missing after the reset, install it again. '
      + 'Make a backup first on the Backup & restore page.'),
    h('div.form-actions', { style: { marginTop: '10px' } }, button('Factory reset…', factoryReset, { kind: 'danger', icon: 'trash' }),
      h('a.btn.btn-ghost', { href: '#/system/backup' }, 'Make a backup')),
  ], { icon: 'trash' }));
}

function resetButtonCard(host, onData) {
  return form(host, {
    title: 'Reset button', icon: 'device',
    description: 'The small button on the router that restores factory settings when held down.',
    load: async () => { const d = await get('/api/system/power'); onData(d); return { reset_button_enabled: d.reset_button_enabled }; },
    save: async (ch) => { const d = await put('/api/system/power/reset-button', { enabled: ch.reset_button_enabled }, {
      confirmTitle: 'Disable the reset button',
      confirmMessage: 'Without the reset button the router can only be reset from the device screen or this interface. If you '
        + 'forget the admin password and the screen settings are locked, recovery becomes very hard.',
    }); onData(d); return { reset_button_enabled: d.reset_button_enabled }; },
    fields: [
      { key: 'reset_button_enabled', label: 'Hardware reset button', type: 'switch', onLabel: 'Enabled', offLabel: 'Disabled', full: true,
        help: 'Takes effect after the next restart.' },
    ],
  });
}

function scheduleCard(host, getTime) {
  return form(host, {
    title: 'Scheduled restart', icon: 'clock',
    description: 'Restart the router automatically on the chosen days. Skipped when the router clock is not set or it started '
      + 'less than 15 minutes ago.',
    load: async () => { const d = await get('/api/system/power'); return Object.assign({}, d.schedule, { _now: `${d.router_time} (${d.router_timezone})` }); },
    save: async (ch, vals) => { const d = await put('/api/system/power/schedule', { enabled: vals.enabled, days: vals.days, time: vals.time });
      return Object.assign({}, d.schedule, { _now: `${d.router_time} (${d.router_timezone})` }); },
    fields: [
      { key: 'enabled', label: 'Restart automatically', type: 'switch', full: true },
      { key: 'days', label: 'Days', type: 'chips', options: DAYS, show: (v) => v.enabled,
        validate: (v, vals) => vals.enabled && (!v || !v.length) ? 'Choose at least one day' : null },
      { key: 'time', label: 'Time', type: 'text', placeholder: '03:30', maxLength: 5, show: (v) => v.enabled,
        pattern: /^([01]?\d|2[0-3]):[0-5]\d$/, patternMsg: 'Use HH:MM, for example 03:30' },
      { key: '_now', label: 'Router time now', type: 'readonly', show: (v) => v.enabled },
      { key: 'last_run', label: 'Last automatic restart', type: 'readonly', show: (v) => v.enabled && v.last_run,
        format: (v) => fmt.dateTime(v) },
    ],
  });
}

export default {
  id: 'system-power', path: '/system/power', group: 'system', order: 40, title: 'Restart & reset', icon: 'power',
  async render(el) {
    const top = h('div.grid-2'), a = h('div'), b = h('div');
    const mid = h('div.grid-2'), c = h('div'), d = h('div');
    const lower = h('div');
    top.append(a, b);
    mid.append(c, d);
    el.append(top, mid, lower);
    actionsCard(a);
    const show = statusCard(b);
    resetButtonCard(c, show);
    scheduleCard(d);
    factoryCard(lower);
    get('/api/system/power').then(show).catch((e) => clear(b).appendChild(errorBox(e)));
  },
};
