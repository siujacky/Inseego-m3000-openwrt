// Services › Privacy & vendor services: turn vendor cloud / telemetry / carrier-management services off or on,
// keep vendor logs in RAM, and the health-log upload.
import { h, clear, append } from '../../ui/dom.js';
import { card, alert, badge, button, toast, spinner, errorBox } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { smartForm, isUnavailable, secondOptions } from '../device/_helpers.js';

function servicesCard(host) {
  const body = h('div', spinner());
  host.appendChild(card('Vendor services', [
    h('p.card-desc', 'Background services that talk to the vendor or the carrier. Turning one off stops it now and keeps it off '
      + 'after restarts; turning it on again restores how it was. The router itself keeps working without them.'),
    alert('warn', 'Services marked "watched" report to the vendor watchdog. When one of them is stopped the watchdog may restart '
      + 'the router once (after about two minutes); after that the service stays off.'),
    body,
  ], { icon: 'lock' }));

  async function act(s, on) {
    const url = `/api/privacy/services/${encodeURIComponent(s.name)}/${on ? 'enable' : 'disable'}`;
    await post(url, {}, {
      confirmTitle: on ? `Turn ${s.label} on` : `Turn ${s.label} off`,
      confirmMessage: on ? `${s.label} will start again and start automatically at boot as before.`
        : `This stops: ${s.stops}${s.watched ? ' The router may restart once because the vendor watchdog watches this service.' : ''}`,
    });
    toast(on ? `${s.label} turned on` : `${s.label} turned off`);
    await load();
  }

  function row(s) {
    const state = !s.installed ? badge('Not installed', 'neutral')
      : s.running ? badge('Running', 'warn') : badge('Stopped', 'ok');
    const boot = s.installed ? badge(s.blocked ? 'Kept off' : (s.enabled === 'enabled' ? 'Starts at boot' : s.enabled), s.blocked ? 'ok' : 'neutral') : null;
    return h('tr',
      h('td', { dataset: { label: 'Service' } }, h('strong', s.label), h('div.hint', s.description),
        h('div.hint.mono', s.units.map(u => u.unit).join(', '))),
      h('td', { dataset: { label: 'State' } }, state, ' ', boot, ' ', s.watched ? badge('watched', 'info') : null,
        s.turned_off_at ? h('div.hint', `Turned off ${fmt.ago(s.turned_off_at)}`) : null),
      h('td.actions', s.installed && (s.blocked
        ? button('Turn on', () => act(s, true), { small: true })
        : button('Turn off', () => act(s, false), { small: true, kind: 'primary' }))));
  }

  async function load() {
    try {
      const d = await get('/api/privacy/services');
      append(clear(body), [
        d.mock ? h('p.hint', 'Demo mode: service states are simulated.') : null,
        h('div.table-wrap', h('table.table', h('thead', h('tr', h('th', 'Service'), h('th', 'State'), h('th.actions', ''))),
          h('tbody', d.services.map(row))))]);
    } catch (e) { clear(body).appendChild(isUnavailable(e) ? alert('info', e.message) : errorBox(e, load)); }
  }
  load();
}

function logsForm(host) {
  smartForm(host, {
    title: 'Keep vendor logs in RAM', icon: 'chip',
    description: (d) => `Vendor services write their logs to ${d.path} on the flash memory. Keeping them in a RAM disk `
      + `(${d.size}) saves flash wear; the logs are then lost at every restart. Nothing is deleted: turning this off brings the `
      + 'flash logs back.',
    load: () => get('/api/privacy/vendor-logs'),
    save: (ch) => put('/api/privacy/vendor-logs', ch, {
      confirmTitle: ch.enabled ? 'Keep vendor logs in RAM' : 'Keep vendor logs on flash again',
      confirmMessage: ch.enabled ? 'A RAM disk is mounted over the vendor log folder now and again after each restart of this interface. '
        + 'Logs already open by vendor services continue on flash until those services restart.'
        : 'The RAM disk is removed and the vendor logs are written to flash again. Logs collected in RAM are discarded.',
    }),
    fields: (d) => [
      d.foreign_mount && { key: '_n', type: 'custom', full: true, noSave: true,
        render: () => alert('warn', `Something else is mounted on ${d.path}; this setting leaves it alone.`) },
      { key: 'enabled', label: 'Keep vendor logs in RAM', type: 'switch', full: true },
      { key: 'active', label: 'RAM disk', type: 'readonly', format: (v) => (v ? 'Mounted' : 'Not mounted') },
    ].filter(Boolean),
  });
}

function uploadForm(host) {
  smartForm(host, {
    title: 'Health-log upload', icon: 'upload',
    description: 'The vendor health monitor can upload diagnostic logs (memory, crashes) to the vendor at a fixed interval.',
    load: () => get('/api/privacy/health-upload'),
    save: (ch) => put('/api/privacy/health-upload', ch),
    fields: (d) => [
      { key: 'enabled', label: 'Upload health logs', type: 'switch' },
      { key: 'interval_s', label: 'Upload every', type: 'select', show: (v) => !!v.enabled,
        options: secondOptions([3600, 6 * 3600, 12 * 3600, 86400, 7 * 86400], d.interval_s) },
    ],
  });
}

export default {
  id: 'services-privacy', path: '/services/privacy', group: 'services', order: 60, title: 'Privacy & vendor services', icon: 'lock',
  render(el) {
    const top = h('div');
    const grid = h('div.grid-2');
    el.append(top, grid);
    servicesCard(top);
    logsForm(grid);
    uploadForm(grid);
    grid.appendChild(card('Not offered', [
      h('p', 'Zero-touch provisioning (enrolling the router in a remote fleet manager) and carrier provisioning sessions are not '
        + 'offered here: they hand control of the router to a remote party.'),
    ], { icon: 'about' }));
  },
};
