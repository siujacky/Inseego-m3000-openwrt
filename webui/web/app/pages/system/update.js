// System › Software update: over-the-air update status and control, automatic updates, history.
import { h, clear } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { card, kv, badge, alert, button, toast, spinner, errorBox, empty, meter } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { notify } from './_common.js';

const TONE = { idle: 'neutral', checking: 'info', up_to_date: 'ok', available: 'accent', downloading: 'info', ready: 'accent',
  installing: 'warn', installed: 'ok', error: 'bad', deferred: 'warn', cancelled: 'neutral', confirm_download: 'accent',
  config_updated: 'ok', unknown: 'neutral' };
const BUSY = ['checking', 'downloading', 'installing'];

function statusCard(host, ctx) {
  const body = h('div');
  const root = card('Update status', body, { icon: 'refresh' });
  host.appendChild(root);
  let timer = null, alive = true;

  async function act(path, opts, done) {
    try {
      const d = await post(path, {}, opts);
      draw(d);
      if (done) toast(done);
    } catch (e) { notify(e); }
  }

  function draw(d) {
    clear(body);
    const s = d.state || {};
    const rows = [
      ['Status', h('span', badge(s.label || s.name, TONE[s.name] || 'neutral'), s.user_initiated ? h('span.hint', ' started here') : '')],
      s.progress !== null && s.progress !== undefined && ['Download', h('span', meter(s.progress, { tone: 'ok' }), ` ${s.progress}%`)],
      s.version && ['New version', h('span.mono', s.version)],
      s.error && ['Problem', h('span', { class: 'q-poor' }, s.error.label)],
      ['Last check', d.last_check || 'never'],
      d.install_duration_min && ['Installation takes', `about ${d.install_duration_min} minutes`],
      d.install_defer_s ? ['Installs automatically in', fmt.duration(d.install_defer_s)] : null,
      d.restart_defer_s ? ['Restarts automatically in', fmt.duration(d.restart_defer_s)] : null,
      d.install_in_progress && ['Installer', badge('running', 'warn')],
    ];
    const av = d.available;
    body.append(kv(rows));
    if (av) {
      body.append(h('div.form-section', h('h3', 'Available update')), kv([
        ['From version', av.source_version], ['To version', av.target_version && h('span.mono', av.target_version)],
        ['Size', av.size_bytes ? fmt.bytes(av.size_bytes) : ''], ['Installation time', av.estimated_min ? `about ${av.estimated_min} minutes` : ''],
        av.description && ['Description', av.description],
      ]));
    }
    const a = d.actions || {};
    const btns = [];
    if (a.check) btns.push(button('Check for updates', () => act('/api/update/check', {
      confirmTitle: 'Check for updates',
      confirmMessage: 'The router will contact the vendor\'s update servers over the mobile connection (a small amount of data). '
        + 'If an update is offered you can choose when to download and install it.',
    }, 'Checking for updates…'), { kind: 'primary', icon: 'refresh' }));
    if (a.download) btns.push(button('Download', () => act('/api/update/download', {
      confirmTitle: 'Download the update', confirmMessage: 'Download the update now over the mobile connection? It may use a lot of data.',
    }, 'Download started'), { icon: 'download' }));
    if (a.install) btns.push(button('Install now', () => act('/api/update/install', {
      confirmTitle: 'Install the update',
      confirmMessage: 'The router will install new vendor firmware and restart. Do not switch it off. Vendor firmware can '
        + 'change or remove features that this interface relies on.',
      confirmPhrase: 'INSTALL',
    }, 'Installation started'), { kind: 'danger', icon: 'upload' }));
    if (a.download_postpone) btns.push(button('Download later', () => act('/api/update/download/postpone', {}, 'Postponed'), {}));
    if (a.install_postpone) btns.push(button('Install later', () => act('/api/update/install/postpone', {}, 'Postponed'), {}));
    btns.push(button('Refresh', () => load(), { kind: 'ghost', icon: 'refresh' }));
    body.append(h('div.form-foot', h('div.form-actions', btns)), h('p.hint', d.note || ''));
    clearTimeout(timer);
    if (alive) timer = setTimeout(load, BUSY.includes(s.name) ? 4000 : 30000);
  }

  async function load() {
    try { draw(await get('/api/update/status')); } catch (e) {
      clear(body).appendChild(e.status === 503 ? alert('warn', e.message) : errorBox(e, load));
    }
  }
  body.appendChild(spinner());
  load();
  return () => { alive = false; clearTimeout(timer); };
}

function currentCard(host) {
  const body = h('div', spinner());
  host.appendChild(card('Installed software', body, { icon: 'chip' }));
  get('/api/update/status').then((d) => {
    const c = d.current || {};
    clear(body).append(kv([
      ['Firmware', c.firmware && h('span.mono', c.firmware)], ['Modem firmware', c.modem && h('span.mono', c.modem)],
      ['Vendor web interface', c.web], ['Carrier configuration (PRI)', c.pri], ['Configuration version', d.configuration_version],
      ['Software build', c.software],
      ['Largest update package', d.max_package_bytes ? fmt.bytes(d.max_package_bytes) : ''],
    ]), h('p.hint', (d.sideload && d.sideload.reason) || ''));
  }).catch((e) => clear(body).appendChild(e.status === 503 ? alert('warn', e.message) : errorBox(e)));
}

function settingsCard(host, ctx) {
  return form(host, {
    title: 'Automatic updates', icon: 'clock',
    description: 'Controls whether the router looks for and installs vendor updates by itself.',
    load: async () => { const d = await get('/api/update/settings'); return d; },
    save: (ch) => put('/api/update/settings', ch),
    warn: (ch) => ch.auto_update === false ? 'With automatic updates off the router no longer receives vendor fixes, including security fixes. Turn it off?' : null,
    fields: [
      { key: 'auto_update', label: 'Automatic updates', type: 'switch', full: true,
        help: 'Download and install updates offered by the vendor (the stock "opt out" switch, hidden on this firmware).' },
      { key: 'auto_check', label: 'Check automatically', type: 'switch', help: 'Ask the update server for new versions from time to time.' },
      { key: 'low_battery_threshold', label: 'Minimum battery', type: 'number', min: 1, max: 99, unit: '%',
        help: 'Updates are not installed below this battery level.' },
    ],
  });
}

function nuaCard(host) {
  return form(host, {
    title: 'Generic update agent (NUA)', icon: 'tools',
    description: 'Settings of the vendor\'s generic update agent. On carrier firmware updates are handled by device management '
      + 'instead, so these settings usually have no effect.',
    load: async () => {
      const d = await get('/api/update/settings');
      if (!d.nua) throw Object.assign(new Error('The NUA agent settings are not available'), { status: 503 });
      return Object.assign({}, d.nua, { running_text: d.nua.running ? 'Running' : 'Not running' });
    },
    save: async (ch) => {
      const rest = Object.assign({}, ch);
      const poll = rest.poll_interval_s;
      delete rest.poll_interval_s;
      if (Object.keys(rest).length) await put('/api/update/nua', rest);
      if (poll) await put('/api/update/nua/poll-interval', { seconds: poll });
      return undefined;
    },
    fields: [
      { key: 'running_text', label: 'Agent', type: 'readonly' },
      { key: 'fumo', label: 'Firmware updates', type: 'switch' },
      { key: 'poll', label: 'Periodic checks', type: 'switch' },
      { key: 'opt_out_disabled', label: 'Opt-out disabled', type: 'switch', help: 'When on, users cannot opt out of updates.' },
      { key: 'low_battery', label: 'Minimum battery', type: 'number', min: 1, max: 99, unit: '%' },
      { key: 'low_battery_charger', label: 'Minimum battery on charger', type: 'number', min: 0, max: 99, unit: '%' },
      { key: 'poll_interval_s', label: 'Check interval', type: 'number', min: 0, max: 2592000, unit: 'seconds',
        validate: (v) => (v && v < 60) ? 'Minimum 60 seconds' : null,
        help: 'Applies after the agent or the router restarts. 0 means the vendor default.' },
    ],
  });
}

function historyCard(host) {
  const body = h('div', spinner());
  host.appendChild(card('Update history', body, { icon: 'clock' }));
  get('/api/update/history').then((d) => {
    const rows = d.records || [];
    if (!rows.length) { clear(body).appendChild(empty('No updates have been installed yet.')); return; }
    const td = (label, v, cls) => h('td' + (cls ? '.' + cls : ''), { dataset: { label } }, v === null || v === undefined || v === '' ? '—' : v);
    clear(body).appendChild(h('div.table-wrap', h('table.table',
      h('thead', h('tr', ['Date', 'Version', 'Result', 'Kind', 'Source', 'Size'].map(x => h('th', x)))),
      h('tbody', rows.map(r => h('tr',
        td('Date', r.date), td('Version', r.version, 'mono'),
        td('Result', badge(r.status_text || String(r.status_code), r.success ? 'ok' : 'bad')),
        td('Kind', [r.type, r.update_type].filter(Boolean).join(', ')),
        td('Source', [r.source, r.agent && `via ${r.agent}`].filter(Boolean).join(' ')),
        td('Size', r.size_bytes ? fmt.bytes(r.size_bytes) : '')))))));
    if (d.reverted && d.reverted.length) body.appendChild(h('p.hint', `${d.reverted.length} configuration update(s) were reverted.`));
  }).catch((e) => clear(body).appendChild(e.status === 503 ? alert('warn', e.message) : errorBox(e)));
}

export default {
  id: 'system-update', path: '/system/update', group: 'system', order: 30, title: 'Software update', icon: 'refresh',
  async render(el, ctx) {
    const top = h('div.grid-2'), st = h('div'), cur = h('div');
    const mid = h('div.grid-2'), set = h('div'), nua = h('div');
    const hist = h('div');
    top.append(st, cur);
    mid.append(set, nua);
    el.append(top, mid, hist);
    const stop = statusCard(st, ctx);
    currentCard(cur);
    settingsCard(set, ctx);
    if (ctx.session && ctx.session.expert_mode) nuaCard(nua);
    else nua.appendChild(card('Firmware files', h('p.card-desc', 'Installing a firmware file from this computer (sideload) is not '
      + 'offered: a wrong or interrupted package can make the router unusable. Updates arrive over the air only.'), { icon: 'about' }));
    historyCard(hist);
    return stop;
  },
};
