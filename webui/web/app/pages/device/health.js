// Device › Temperature & health: thermal zones, SAR sensors, memory/CPU, watchdog, low-memory reboot,
// memory diagnostics and the firmware fail-safe (read-only).
import { h } from '../../ui/dom.js';
import { kv, badge, meter, alert } from '../../ui/widgets.js';
import { get, put } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { smartForm, infoCard, yesNo } from './_helpers.js';

const ZONE_NAMES = { 'cpu0-usr': 'Processor', 'modem0-usr': 'Modem', 'pa-therm0': 'Power amplifier', battery: 'Battery' };
const tempTone = (c) => (c >= 70 ? 'bad' : c >= 55 ? 'warn' : 'ok');
// one /api/device/health request shared by the cards of this page (the server caches it for 5 s anyway)
let shared = { t: 0, p: null };
function health() {
  const now = Date.now();
  if (!shared.p || now - shared.t > 3000) shared = { t: now, p: get('/api/device/health') };
  return shared.p;
}

function thermalCard(host) {
  return infoCard(host, 'Temperatures', 'thermo', health, (d) => {
    const zones = d.thermal || [];
    const top = zones.slice(0, 4);
    return [
      top.length ? h('div.stats', top.map(z => h('div.stat.tone-' + tempTone(z.celsius),
        h('div.stat-label', ZONE_NAMES[z.type] || z.type || z.zone), h('div.stat-value', `${z.celsius} °C`), h('div.stat-sub', z.zone))))
        : alert('info', 'No temperature sensors were found.'),
      zones.length > 4 && h('details', h('summary', `All ${zones.length} sensors`),
        h('div.table-wrap', h('table.table', h('thead', h('tr', h('th', 'Sensor'), h('th', 'Zone'), h('th', 'Temperature'))),
          h('tbody', zones.map(z => h('tr', h('td', ZONE_NAMES[z.type] || z.type), h('td', z.zone), h('td', `${z.celsius} °C`))))))),
      d.thermal_report && d.thermal_report.length ? h('p.hint', 'Vendor thermal monitor: ',
        d.thermal_report.map(t => `${t.zone} ${t.celsius} °C`).join(', ')) : null,
    ];
  });
}

function systemCard(host) {
  return infoCard(host, 'System', 'chip', health, (d) => {
    const m = d.memory || {};
    const used = m.total ? Math.round((1 - m.available / m.total) * 100) : null;
    return kv([
      ['Memory in use', used === null ? '' : h('span', meter(100 - used, { tone: used > 85 ? 'bad' : used > 70 ? 'warn' : 'ok' }),
        ` ${used} % (${fmt.bytes(m.available)} free of ${fmt.bytes(m.total)})`)],
      ['Processor', d.cpu ? [d.cpu.model, d.cpu.freq_mhz ? `${d.cpu.freq_mhz} MHz` : '', d.cpu.cores ? `${d.cpu.cores} core(s)` : '']
        .filter(Boolean).join(' · ') : ''],
      ['Load', d.load ? d.load.join(' ') : ''],
      ['Running for', fmt.duration(d.uptime_s)],
      ['Health monitor', badge(d.health_running ? 'Running' : 'Not running', d.health_running ? 'ok' : 'neutral')],
    ]);
  });
}

function sarCard(host) {
  infoCard(host, 'Body-proximity sensors (SAR)', 'antenna', health, (d) => {
    const s = d.sar || {};
    const keys = Object.keys(s);
    return [
      h('p.card-desc', 'These sensors notice a body close to the antennas so the router can lower its transmit power, as radio '
        + 'regulations require. Shown for information only.'),
      kv([
        ['Service', badge(d.sar_running ? 'Running' : 'Not running', d.sar_running ? 'ok' : 'neutral')],
        ...keys.map(k => [`Sensor ${k}`, s[k] ? 'Enabled' : 'Disabled']),
      ]),
    ];
  });
}

function watchdogForm(host) {
  smartForm(host, {
    title: 'Watchdog', icon: 'refresh',
    description: 'Restarts the router automatically when a vendor service stops responding.',
    load: () => get('/api/device/watchdog'),
    save: (ch) => put('/api/device/watchdog', ch, {
      confirmTitle: 'Turn the watchdog off',
      confirmMessage: 'Without the watchdog a hung vendor service is no longer recovered automatically; the router may stay '
        + 'unreachable until it is restarted by hand.',
    }),
    fields: [
      { key: 'enabled', label: 'Watchdog', type: 'switch' },
      { key: 'interval_s', label: 'Check interval', type: 'number', unit: 's', min: 30, max: 3600,
        help: 'How often each registered service must report that it is alive.' },
      { key: 'service_running', label: 'Service', type: 'readonly', format: (v) => yesNo(v, 'Running', 'Not running') },
    ],
  });
  infoCard(host, 'Watched services', 'services', health, (d) => (
    d.watchdog_apps && d.watchdog_apps.length
      ? [h('p.hint', 'Vendor services that currently report to the watchdog:'),
        h('div.chips', d.watchdog_apps.map(a => h('span.chip', a)))]
      : [h('p.hint', 'No services are registered with the watchdog.')]));
}

function lowmemForm(host) {
  smartForm(host, {
    title: 'Low-memory restart', icon: 'chip',
    description: 'Restarts the router during a quiet time window when free memory stays below the threshold.',
    load: () => get('/api/device/health/lowmem'),
    save: (ch) => put('/api/device/health/lowmem', ch),
    fields: [
      { key: 'threshold_mb', label: 'Restart when free memory is below', type: 'number', unit: 'MB', min: 15, max: 400 },
      { key: 'check_interval_s', label: 'Check every', type: 'number', unit: 's', min: 10, max: 86400 },
      { key: 'min_uptime_h', label: 'Only after running for at least', type: 'number', unit: 'h', min: 0, max: 8760 },
      { key: 'window_start_h', label: 'Allowed from (hour)', type: 'number', min: 0, max: 23, help: '24-hour clock, router time' },
      { key: 'window_end_h', label: 'Allowed until (hour)', type: 'number', min: 0, max: 23 },
    ],
  });
}

function memdiagForm(host) {
  smartForm(host, {
    title: 'Memory diagnostics', icon: 'chip',
    description: 'Vendor memory-statistics logging used for fault analysis. Change only when asked to collect diagnostics.',
    load: () => get('/api/device/health/memdiag'),
    save: (ch) => put('/api/device/health/memdiag', ch),
    fields: [
      { key: 'max_log_kb', label: 'Log file size', type: 'number', unit: 'KB', min: 1, max: 2048 },
      { key: 'max_backups', label: 'Log files kept', type: 'number', min: 0, max: 100 },
      { key: 'safe_zone_mb', label: 'Safe zone from', type: 'number', unit: 'MB free', min: 1, max: 400 },
      { key: 'critical_zone_mb', label: 'Critical zone below', type: 'number', unit: 'MB free', min: 1, max: 400 },
      { key: 'safe_poll_s', label: 'Poll (safe)', type: 'number', unit: 's', min: 1, max: 86400 },
      { key: 'moderate_poll_s', label: 'Poll (moderate)', type: 'number', unit: 's', min: 1, max: 86400 },
      { key: 'critical_poll_s', label: 'Poll (critical)', type: 'number', unit: 's', min: 1, max: 86400 },
      { key: 'safe_store_min', label: 'Store stats (safe)', type: 'number', unit: 'min', min: 1, max: 1440 },
      { key: 'moderate_store_min', label: 'Store stats (moderate)', type: 'number', unit: 'min', min: 1, max: 1440 },
      { key: 'critical_store_min', label: 'Store stats (critical)', type: 'number', unit: 'min', min: 1, max: 1440 },
      { key: 'top_cpu_threshold', label: 'Log processes above', type: 'number', unit: '% CPU', min: 0, max: 100 },
      { key: 'debug_mode', label: 'Debug mode', type: 'select', options: [[0, 'Off'], [1, 'On']],
        help: 'Very verbose logging; uses flash space.' },
    ],
  });
}

function failsafeCard(host) {
  infoCard(host, 'Update fail-safe', 'system', health, (d) => {
    const f = d.failsafe;
    if (!f) return [alert('info', 'Not available on this unit.')];
    return [
      h('p.card-desc', 'After a firmware update the router checks itself for a while and goes back to the previous firmware '
        + 'when it keeps failing. Shown for information only.'),
      kv([
        ['Check in progress', yesNo(d.failsafe_running)],
        ['Check duration', f.verify_duration_min === null ? '' : `${f.verify_duration_min} min`],
        ['Allowed service crashes', f.crash_count],
        ['Allowed watchdog restarts', f.wdt_reboot_count],
        ['Mobile connection attempts', f.pdn_retry_count],
      ]),
    ];
  });
}

export default {
  id: 'device-health', path: '/device/health', group: 'device', order: 30, title: 'Temperature & health', icon: 'thermo',
  render(el, ctx) {
    const g1 = h('div.grid-2'), g2 = h('div.grid-2'), g3 = h('div.grid-2');
    el.append(g1, g2, g3);
    const t = thermalCard(g1);
    const s = systemCard(g1);
    sarCard(g2);
    failsafeCard(g2);
    const left = h('div'), right = h('div');
    g3.append(left, right);
    watchdogForm(left);
    lowmemForm(right);
    if (ctx && ctx.session && ctx.session.expert_mode) memdiagForm(right);
    const timer = setInterval(() => { if (!document.hidden) { t.reload(true); s.reload(true); } }, 10000);
    return () => clearInterval(timer);
  },
};
