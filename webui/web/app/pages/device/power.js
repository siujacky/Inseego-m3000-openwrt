// Device › Battery & power: battery status, power save (deep sleep, wake-up timer, automatic shutdown),
// battery preservation and charging other devices.
import { h } from '../../ui/dom.js';
import { kv, meter, button, alert, toast } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import { smartForm, infoCard, secondOptions, seconds, serviceNote } from './_helpers.js';

const IDLE = [60, 120, 300, 600, 900, 1800, 3600];
const HOURS = Array.from({ length: 24 }, (_, i) => (i + 1) * 3600);
const words = (s) => (s ? String(s).replace(/_/g, ' ') : '');
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

function batteryCard(host) {
  return infoCard(host, 'Battery', 'battery', () => get('/api/device/battery'), (b) => {
    if (!b.present) return [alert('info', 'No battery detected. The router runs from external power.')];
    const tone = b.percent < 15 ? 'bad' : b.percent < 35 ? 'warn' : 'ok';
    return [
      h('div.stats',
        h('div.stat.tone-' + tone, h('div.stat-label', 'Charge'), h('div.stat-value', b.percent === null ? '—' : `${b.percent} %`),
          h('div.stat-sub', meter(b.percent))),
        h('div.stat', h('div.stat-label', 'State'), h('div.stat-value', b.charging ? 'Charging' : (cap(words(b.state)) || '—')),
          h('div.stat-sub', b.source ? `Power from ${b.source}` : '')),
        h('div.stat', h('div.stat-label', 'Temperature'), h('div.stat-value', b.temperature_c === null ? '—' : `${b.temperature_c} °C`),
          h('div.stat-sub', words(b.temperature_state))),
        h('div.stat', h('div.stat-label', 'Voltage'), h('div.stat-value', b.voltage_v === null ? '—' : `${b.voltage_v.toFixed(2)} V`),
          h('div.stat-sub', words(b.voltage_state)))),
      kv([
        ['Current', b.current_ma === null ? '' : `${b.current_ma} mA`],
        ['Health', b.health],
        ['Design capacity', b.design_capacity_mah ? `${b.design_capacity_mah} mAh` : ''],
        ['Power state', words(b.power_state)],
        ['Charger', words(b.charger)],
        ['USB', [words(b.usb), words(b.usb_state)].filter(Boolean).join(', ')],
        ['USB-C power role', words(b.typec_role)],
        ['Last start reason', words(b.power_on_cause)],
      ], { compact: true }),
    ];
  }, { actions: [] });
}

function powersaveForm(host) {
  let f = null;
  const never = button('Never sleep (router mode)', async () => {
    const d = await post('/api/device/powersave/never-sleep', {});
    toast('Deep sleep and automatic shutdown are off');
    if (f) f.reload();
    return d;
  }, { small: true, title: 'Keep the router running all the time, like the modified firmware images do' });
  smartForm(host, {
    title: 'Power save', icon: 'power',
    description: 'Saves battery when nothing is using the router. A sleeping or switched-off router cannot be reached over the network.',
    load: () => get('/api/device/powersave'),
    save: (ch) => put('/api/device/powersave', ch),
    warn: (ch, v) => ((ch.deep_sleep_enabled === true || ch.auto_shutdown_enabled === true)
      ? 'While the router sleeps or is switched off, nobody can reach it over the network or Wi-Fi. Press its power button to wake it. Continue?'
      : null),
    actions: [never],
    onReady: (form) => { f = form; },
    fields: (d) => [
      d.service_running === false && { key: '_note', type: 'custom', full: true, noSave: true, render: () => serviceNote(false, 'power-save') },
      { key: 'enabled', label: 'Power saving', type: 'switch', full: true,
        help: 'Master switch for the vendor power manager (CPU frequency scaling and the options below).' },
      { type: 'section', label: 'Deep sleep' },
      { key: 'deep_sleep_enabled', label: 'Deep sleep when idle', type: 'switch',
        help: 'When no device has been connected for a while, the router turns Wi-Fi and the mobile connection off.' },
      { key: 'deep_sleep_idle_s', label: 'Go to deep sleep after', type: 'select', options: secondOptions(IDLE, d.deep_sleep_idle_s),
        show: (v) => !!v.deep_sleep_enabled },
      { key: 'deep_sleep_wakeup_s', label: 'Wake up from deep sleep every', type: 'select',
        options: secondOptions([0].concat(HOURS), d.deep_sleep_wakeup_s, 'Never (stay asleep)'), show: (v) => !!v.deep_sleep_enabled,
        help: 'Lets the router check for messages and updates regularly while asleep.' },
      { type: 'section', label: 'Automatic shutdown' },
      { key: 'auto_shutdown_enabled', label: 'Turn off when idle', type: 'switch',
        help: 'Switches the router off completely after a long idle time on battery.' },
      { key: 'auto_shutdown_idle_s', label: 'Turn off after', type: 'select', options: secondOptions(HOURS, d.auto_shutdown_idle_s),
        show: (v) => !!v.auto_shutdown_enabled,
        validate: (x, v) => (v.deep_sleep_enabled && v.auto_shutdown_enabled && Number(x) < Number(v.deep_sleep_idle_s)
          ? 'Must not be shorter than the deep-sleep time' : null) },
    ].filter(Boolean),
  });
}

function preservationForm(host) {
  smartForm(host, {
    title: 'Battery preservation', icon: 'battery',
    description: (d) => (d.min_pct && d.max_pct
      ? `Protects the battery when the router stays on a charger: after ${seconds(d.duration_s)} on external power the charge is held `
        + `between ${d.min_pct} % and ${d.max_pct} % instead of 100 %. Recommended for a router that is always plugged in.`
      : 'Protects the battery when the router stays on a charger by not keeping it at 100 %.'),
    load: () => get('/api/device/battery-preservation'),
    save: (ch) => put('/api/device/battery-preservation', ch),
    fields: (d) => [
      { key: 'enabled', label: 'Battery preservation', type: 'switch', full: true },
      { key: 'status', label: 'Current state', type: 'readonly', format: (v) => (v === null || v === undefined ? '—' : (Number(v) ? 'Holding charge' : 'Not active')) },
      d.min_pct ? { key: 'min_pct', label: 'Charge window', type: 'readonly', format: (v, all) => `${all.min_pct} – ${all.max_pct} % (set by the vendor)` } : null,
    ].filter(Boolean),
  });
}

function universalForm(host) {
  smartForm(host, {
    title: 'Charging other devices', icon: 'charging',
    description: 'The router can charge a phone or other device from its battery through the USB port.',
    load: async () => {
      const d = await get('/api/device/universal-charging');
      if (!d.supported) { const e = new Error('Charging other devices is not supported on this unit.'); e.status = 503; throw e; }
      return d;
    },
    save: (ch) => put('/api/device/universal-charging', ch),
    fields: (d) => [
      { key: 'enabled', label: 'Charge other devices', type: 'switch' },
      { key: 'threshold_pct', label: 'Stop when the router battery falls to', type: 'select',
        options: [[5, '5 %'], [25, '25 %'], [50, '50 %']].concat([5, 25, 50].includes(d.threshold_pct) || d.threshold_pct === null ? [] : [[d.threshold_pct, `${d.threshold_pct} % (current)`]]),
        show: (v) => !!v.enabled, help: 'Keeps enough charge for the router itself.' },
      { key: 'state', label: 'State', type: 'readonly', format: (v) => (v ? String(v).replace(/_/g, ' ') : '—') },
      { key: 'critical_battery_pct', label: 'Router shuts down at', type: 'readonly', format: (v) => (v === null || v === undefined ? '—' : `${v} %`) },
    ],
  });
}

export default {
  id: 'device-power', path: '/device/power', group: 'device', order: 10, title: 'Battery & power', icon: 'battery',
  feature: 'device.powersave',
  render(el) {
    const top = h('div'), grid = h('div.grid-2'), grid2 = h('div.grid-2');
    el.append(top, grid, grid2);
    const bat = batteryCard(top);
    powersaveForm(grid);
    preservationForm(grid);
    universalForm(grid2);
    const timer = setInterval(() => { if (!document.hidden) bat.reload(true); }, 15000);
    return () => clearInterval(timer);
  },
};
