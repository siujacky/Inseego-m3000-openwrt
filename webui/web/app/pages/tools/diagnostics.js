// Tools › Diagnostics: network tests, modem & network info (stock Diagnostics incl. hidden rows), field test,
// lab test (hidden stock page), carrier account events (PCO) and Internet session history.
import { h, clear } from '../../ui/dom.js';
import { card, kv, stat, badge, alert, button, toast, confirmSimple } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { list } from '../../ui/list.js';
import * as fmt from '../../ui/format.js';
import { get, post, put } from '../../api.js';
import { tabs, section, field, setFieldError, pre, copyButton, downloadLink, friendlyError, when, num } from './_helpers.js';

// ---- network tests --------------------------------------------------------------------------------------------
function toolCard({ title, iconName, description, fields, run, renderResult }) {
  const out = h('div', { style: { marginTop: '12px' } });
  const inputs = {};
  const controls = fields.map(f => {
    let ctl;
    if (f.type === 'checkbox') {
      ctl = h('label.check', h('input', { type: 'checkbox', dataset: { key: f.key }, checked: !!f.value }), h('span', f.checkLabel || ''));
      inputs[f.key] = () => ctl.querySelector('input').checked;
    } else {
      ctl = h('input', { type: f.type || 'text', dataset: { key: f.key }, value: f.value == null ? '' : f.value, placeholder: f.placeholder || '',
        min: f.min, max: f.max, autocomplete: 'off', spellcheck: 'false', inputmode: f.type === 'number' ? 'numeric' : undefined,
        onkeydown: (e) => { if (e.key === 'Enter') go.click(); } });
      inputs[f.key] = () => (f.type === 'number' ? Number(ctl.value) : ctl.value.trim());
    }
    return field(f.label, ctl, f.help);
  });
  const root = h('div');
  const go = button('Run', async () => {
    const body = {};
    for (const [k, fn] of Object.entries(inputs)) { const v = fn(); if (v !== '' && v !== null) body[k] = v; }
    setFieldError(root, null);
    clear(out).appendChild(h('div.loading', h('span.spinner'), h('span', 'Running on the router…')));
    try {
      const res = await run(body);
      clear(out).append(...[renderResult(res), h('details', { open: !res.ok }, h('summary', 'Raw output'), pre(res.output))].filter(Boolean));
    } catch (e) {
      clear(out);
      if (e.field && e.field !== '_') setFieldError(root, e.field, e.message);
      else { const b = friendlyError(e); if (b) out.appendChild(b); }
    }
  }, { kind: 'primary', icon: 'refresh' });
  root.append(h('div.form-grid', controls), h('div.form-foot', go), out);
  return card(title, [description && h('p.card-desc', description), root], { icon: iconName });
}

function renderTests(el) {
  const ping = toolCard({
    title: 'Ping', iconName: 'globe', description: 'Checks that a host answers and how long the round trip takes.',
    fields: [{ key: 'host', label: 'Host or IP address', placeholder: 'e.g. 1.1.1.1 or example.com', value: '1.1.1.1' },
             { key: 'count', label: 'Requests', type: 'number', value: 4, min: 1, max: 20 },
             { key: 'ipv6', label: 'IP version', type: 'checkbox', checkLabel: 'Use IPv6', value: false }],
    run: (b) => post('/api/tools/ping', b),
    renderResult: (r) => {
      const s = r.summary || {};
      return h('div.stats',
        stat('Result', r.ok ? 'Reachable' : (r.timed_out ? 'Timed out' : 'No reply'), r.host, r.ok ? 'good' : 'poor'),
        stat('Received', s.sent !== undefined ? `${s.received}/${s.sent}` : '—', s.loss_pct !== undefined ? `${s.loss_pct}% loss` : ''),
        stat('Average', s.rtt_avg_ms !== undefined ? `${s.rtt_avg_ms} ms` : '—', s.rtt_min_ms !== undefined ? `min ${s.rtt_min_ms} / max ${s.rtt_max_ms} ms` : ''));
    },
  });
  const trace = toolCard({
    title: 'Traceroute', iconName: 'network', description: 'Lists the routers between this device and a host.',
    fields: [{ key: 'host', label: 'Host or IP address', placeholder: 'e.g. example.com', value: '' },
             { key: 'max_hops', label: 'Maximum hops', type: 'number', value: 20, min: 1, max: 30 }],
    run: (b) => post('/api/tools/traceroute', b),
    renderResult: (r) => (r.hops && r.hops.length ? h('div.table-wrap', h('table.table',
      h('thead', h('tr', h('th', 'Hop'), h('th', 'Address'), h('th', 'Time'))),
      h('tbody', r.hops.map(x => h('tr', h('td', { dataset: { label: 'Hop' } }, String(x.hop)),
        h('td.mono', { dataset: { label: 'Address' } }, x.timeout ? '* (no answer)' : x.address || x.host),
        h('td', { dataset: { label: 'Time' } }, x.rtt_ms == null ? '—' : `${x.rtt_ms} ms`)))))) : alert('warn', 'No hops were reported.')),
  });
  const dns = toolCard({
    title: 'DNS lookup', iconName: 'link', description: 'Resolves a name with the router\'s DNS, or with a server you choose.',
    fields: [{ key: 'host', label: 'Name to look up', placeholder: 'e.g. example.com', value: '' },
             { key: 'server', label: 'DNS server (optional)', placeholder: 'e.g. 9.9.9.9', value: '' }],
    run: (b) => post('/api/tools/nslookup', b),
    renderResult: (r) => (r.addresses && r.addresses.length
      ? kv([['Addresses', h('span.mono', r.addresses.join(', '))], ['Server', r.server || 'router default']])
      : alert('warn', 'The name did not resolve.')),
  });
  el.appendChild(h('div.grid', ping, trace, dns));
}

// ---- modem & network ------------------------------------------------------------------------------------------
function renderModem(el, ctx, goTab) {
  const host = h('div');
  let reveal = null;
  const toggle = button('Show identifiers', async () => {
    if (reveal) { reveal = null; toggle.querySelector('span').textContent = 'Show identifiers'; load(); return; }
    reveal = await get('/api/tools/modem-info/identifiers');
    toggle.querySelector('span').textContent = 'Hide identifiers';
    load();
  }, { small: true, icon: 'eye' });
  const refresh = button('Refresh', () => load(), { small: true, icon: 'refresh' });
  el.append(h('div.page-head', h('div', h('p', 'Identity of the modem and SIM, and the current mobile network. Includes the rows the original firmware hid (IMSI, MEID, PCI, cell ID, legacy 3G sections).')),
    h('div.actions', toggle, refresh)), host);
  const dash = (v) => (v === null || v === undefined || v === '' || v === 0 ? '' : String(v));
  function load() {
    return section(host, () => get('/api/tools/modem-info'), (d) => {
      const m = Object.assign({}, d.modem, reveal || {});
      const n = d.network, nr = n.nr || {}, lg = d.legacy || {};
      const tone = { connected: 'ok', available: 'info', radio_off: 'warn', no_sim: 'bad', not_available: 'bad' }[n.status] || 'neutral';
      const idCell = (v) => (v ? h('span.mono', v) : '');
      return [
        n.status === 'no_sim' ? alert('warn', 'No SIM card detected: subscriber identifiers and network rows stay empty.') : null,
        h('div.grid-2',
          card('Modem and SIM', kv([
            ['Phone number', idCell(m.phone_number)], ['IMEI', idCell(m.imei)], ['IMEI SV', idCell(m.imei_sv)],
            ['MEID', idCell(m.meid)], ['IMSI', idCell(m.imsi)], ['ICCID', idCell(m.iccid)], ['MSID / MIN', idCell(m.msid)],
            ['ESN', idCell(m.esn && m.esn !== '0' ? m.esn : '')], ['Home SID', m.home_sid],
            ['Modem firmware', m.firmware], ['Hardware version', m.hardware], ['PRI version', m.pri_version],
            ['PRL version', dash(m.prl_version)], ['ERI version', dash(m.eri_version)],
            ['SIM status', badge(m.sim_status || 'Unknown', /ready/i.test(m.sim_status) ? 'ok' : 'warn')],
            ['SIM PIN lock', m.sim_pin_enabled ? `On (${m.sim_pin_retries ?? '?'} tries left, PUK ${m.sim_puk_retries ?? '?'})` : 'Off'],
          ]), { icon: 'sim', subtitle: reveal ? 'identifiers shown' : 'identifiers masked' }),
          card('Mobile network', kv([
            ['Status', badge(n.status_text, tone)], ['Technology', n.technology], ['Enabled modes', (n.enabled_tech || []).join(', ')],
            ['Operator', n.operator], ['Band', n.band && n.band !== '0' ? 'B' + n.band : ''],
            ['Bandwidth', num(n.bandwidth_mhz || '', 'MHz')], ['RSRP', fmt.dbm(n.rsrp)], ['RSRQ', fmt.dbm(n.rsrq, 'dB')],
            ['SINR', fmt.dbm(n.sinr, 'dB')], ['SNR', fmt.dbm(n.snr, 'dB')], ['RSSI', fmt.dbm(n.rssi)],
            ['Roaming', n.roaming ? badge('Roaming', 'warn') : 'No'], ['PCI', dash(n.pci)], ['Cell ID', n.cell_id],
            ['ECGI', n.ecgi && n.ecgi !== '0' ? h('span.mono', n.ecgi) : ''], ['TAC', n.tac], ['Channel (EARFCN)', dash(n.channel)],
            ['Mobile data', n.data_enabled ? 'On' : 'Off'], ['Radio', n.radio_on ? 'On' : 'Off (airplane mode)'],
          ]), { icon: 'cellular' })),
        h('div.grid-2',
          card('5G NR', nr.available ? kv([
            ['Mode', nr.mode], ['Band', nr.band ? 'n' + nr.band : ''], ['Bandwidth', num(nr.bandwidth_mhz || '', 'MHz')],
            ['PCI', dash(nr.pci)], ['RSRP', fmt.dbm(nr.rsrp)], ['RSRQ', fmt.dbm(nr.rsrq, 'dB')], ['SNR', fmt.dbm(nr.snr, 'dB')],
            ['Channel (NR-ARFCN)', dash(nr.channel)],
          ]) : h('p.muted', 'No 5G service at the moment.'), { icon: 'antenna' }),
          card('2G / 3G (legacy)', kv([
            ['Technology', lg.technology], ['RSSI', fmt.dbm(lg.rssi)], ['Ec/Io', fmt.dbm(lg.ecio, 'dB')],
            ['System ID (SID)', dash(lg.sid)], ['Network ID (NID)', dash(lg.nid)], ['LAC', lg.lac], ['Cell', lg.cell_id],
            ['Protocol revision', dash(lg.p_rev)], ['Femtocell', lg.femto ? 'Yes' : 'No'], ['Operator ID', lg.operator_id],
            ['Home network', lg.home_network === null || lg.home_network === undefined ? '' : (lg.home_network ? 'Yes' : 'No')],
          ]), { icon: 'antenna', subtitle: 'shown by the original firmware only on 3G networks' })),
        h('p.hint', 'Engineering dump: ', h('a', { href: '#', onclick: (e) => { e.preventDefault(); goTab('field'); } }, 'Field test data'), '.'),
      ];
    });
  }
  load();
}

// ---- field test -----------------------------------------------------------------------------------------------
function renderField(el) {
  const host = h('div');
  let reveal = false, last = null;
  const toggle = button('Show identifiers', () => { reveal = !reveal; toggle.querySelector('span').textContent = reveal ? 'Hide identifiers' : 'Show identifiers'; return load(); }, { small: true, icon: 'eye' });
  el.append(card('Field test', [h('p.card-desc', 'The modem\'s diagnostic buffer (the hidden "field test" page of the original firmware). Mostly CDMA-era fields; values shown as the modem reports them.'), host],
    { icon: 'chip', actions: [toggle, button('Refresh', () => load(), { small: true, icon: 'refresh' }), copyButton(() => (last ? last.text : ''))] }));
  function load() {
    return section(host, () => get('/api/tools/field-test' + (reveal ? '?reveal=1' : '')), (d) => {
      last = d;
      return [
        d.title ? h('h3', { style: { fontSize: '14px', margin: '0 0 8px' } }, d.title) : null,
        kv(d.items.map(it => [it.label, it.values ? h('div.chips', it.values.map(v => h('span.chip', v))) : (it.value === '' ? '' : h('span' + (it.sensitive ? '.mono' : ''), it.value))]), { compact: true }),
        h('details', { style: { marginTop: '12px' } }, h('summary', 'Raw buffer'), pre(d.text)),
        h('p.hint', 'Captured ' + when(d.captured)),
      ];
    });
  }
  load();
}

// ---- lab test ---------------------------------------------------------------------------------------------------
function renderLab(el, ctx, goTab) {
  const top = h('div');
  const logHost = h('div');
  el.append(h('p.hint', 'The original firmware\'s hidden lab-test page (reachable only by typing its address). Settings that belong to other pages are shown read-only with a pointer to where they are changed.'), top, logHost);
  section(top, () => get('/api/tools/lab-test'), (d) => {
    const t = d.technology || {}, ac = d.auto_connect || {}, ip = d.ippt || {};
    return h('div.grid-2',
      card('Software', kv([['Version', d.software.version], ['Modem firmware', d.software.modem_firmware],
        ['Web UI version (original)', d.software.web_ui], ['PRI', d.software.pri]]), { icon: 'system' }),
      card('Connection', kv([
        ['Status', badge(d.connection.status, d.connection.connected ? 'ok' : 'warn')],
        ['Auto-connect', ac.label || (ac.enabled ? 'On' : 'Off'), 'Connect / disconnect from the dashboard'],
        ['Roaming allowed', ac.roaming_allowed ? 'Yes' : 'No'],
      ]), { icon: 'globe', actions: h('a.btn.btn-sm', { href: '#/' }, 'Dashboard') }),
      card('Network technology', kv([
        ['Current', t.label ? `${t.label} (${t.mask})` : (t.mask != null ? String(t.mask) : '')],
        ['Enabled modes', (t.names || []).join(', ')], ['Modem supports', (t.supported || []).join(', ')],
        ['Offered by the original UI', (t.options || []).map(o => `${o.label} (${o.value})`).join(', ')],
      ]), { icon: 'cellular', actions: h('a.btn.btn-sm', { href: '#/cellular/mode' }, 'Network mode') }),
      card('IP passthrough', kv([
        ['Mode', ip.active === null || ip.active === undefined ? '' : (ip.active ? 'IP passthrough' : 'Router (NAT)')],
        ['Interface', ip.interface == null ? '' : String(ip.interface)], ['Feature mode', ip.feature_mode == null ? '' : String(ip.feature_mode)],
      ]), { icon: 'network', actions: h('a.btn.btn-sm', { href: '#/network/ippt' }, 'IP passthrough') }));
  });

  const formHost = h('div');
  const clearBtn = button('Delete modem logs', async () => {
    if (!(await confirmSimple('Delete modem logs', 'Delete all stored modem logs? This cannot be undone.', 'Continue', 'danger'))) return;
    await post('/api/tools/modem-logging/clear', {}, { confirmTitle: 'Delete modem logs', confirmMessage: 'The stored modem (QXDM-style) logs will be deleted.' });
    toast('Modem logs deleted');
  }, { kind: 'danger', icon: 'trash' });
  logHost.appendChild(formHost);
  form(formHost, {
    title: 'Modem logging', icon: 'chip',
    description: 'Records low-level modem logs for troubleshooting. Logs take storage space: at most 3 files and 75 MB in total. Hidden in the original firmware.',
    load: () => get('/api/tools/modem-logging'),
    save: (ch) => put('/api/tools/modem-logging', ch),
    actions: [clearBtn],
    fields: [
      { key: 'enabled', label: 'Modem logging', type: 'switch', help: 'Turn off when you are done: logging slows the device a little and uses storage.' },
      { key: 'file_count', label: 'Number of log files', type: 'number', min: 1, max: 3, required: true },
      { key: 'file_size_mb', label: 'Size of each file', type: 'number', min: 1, max: 75, unit: 'MB', required: true,
        validate: (v, vals) => { const t = Number(v) * Number(vals.file_count); return t < 10 || t > 75 ? `Files × size must be 10–75 MB (now ${t} MB)` : null; } },
      { key: 'total_mb', label: 'Total', type: 'readonly', noSave: true, show: () => true,
        format: (v, vals) => `${Number(vals.file_size_mb || 0) * Number(vals.file_count || 0)} MB` },
    ],
  });
  el.appendChild(alert('info', 'Not offered here: USB "debug / enterprise" device modes (debug mode opens diagnostic ports; normal USB modes are under System › USB) and the vendor cloud-agent settings (vendor service, not supported).'));
  el.appendChild(h('p.hint', 'The diagnostic buffer shown at the bottom of the original page is under ',
    h('a', { href: '#', onclick: (e) => { e.preventDefault(); goTab('field'); } }, 'Field test'), '.'));
}

// ---- PCO ----------------------------------------------------------------------------------------------------------
function renderPco(el) {
  const host = h('div');
  el.append(card('Carrier account events', [h('p.card-desc', 'Some carriers signal the account state to the device (protocol configuration options, "PCO"): for example out of credit or not activated. The original firmware then showed a page offering limited access to the carrier\'s portal.'), host],
    { icon: 'sim', actions: button('Refresh', () => load(), { small: true, icon: 'refresh' }) }));
  function load() {
    return section(host, () => get('/api/tools/pco'), (d) => {
      const tone = { none: 'ok', restricted: 'warn', out_of_credit: 'bad', not_active: 'bad' }[d.status] || 'info';
      return [
        h('div.stats', stat('Account status', d.status === 'none' ? 'OK' : d.status.replace(/_/g, ' '), `PCO value ${d.pco_value}`, tone === 'ok' ? 'good' : 'poor'),
          stat('PDN', String(d.pdn), 'data connection index'), stat('Data', d.connected ? 'Connected' : 'Not connected')),
        h('p', { style: { marginTop: '12px' } }, d.text),
        d.can_allow_access ? h('div.form-foot', button('Allow limited access', async () => {
          if (!(await confirmSimple('Allow limited access', 'Ask the carrier for temporary data access so you can top up or activate the account?', 'Allow'))) return;
          await post('/api/tools/pco/accept', {});
          toast('Limited access requested');
          load();
        }, { kind: 'primary' })) : null,
      ];
    });
  }
  load();
}

// ---- sessions -----------------------------------------------------------------------------------------------------
function renderSessions(el) {
  const totals = h('div');
  const tableHost = h('div');
  el.append(totals, tableHost);
  let cached = null;
  const lst = list(tableHost, {
    title: 'Internet sessions', icon: 'clock',
    description: 'Past mobile data sessions recorded by the device (newest first).',
    load: async () => {
      try {
        cached = await get('/api/tools/sessions');
      } catch (e) {
        if (e.status === 503) { clear(totals).appendChild(friendlyError(e)); return []; }
        throw e;
      }
      clear(totals).appendChild(h('div.stats', { style: { marginBottom: '12px' } },
        stat('Sessions', String(cached.count)), stat('Received', fmt.bytes(cached.totals.rx_bytes)),
        stat('Sent', fmt.bytes(cached.totals.tx_bytes)), stat('Connected time', fmt.duration(cached.totals.duration_s))));
      return cached.sessions;
    },
    columns: [
      { key: 'start', label: 'Started', format: (v) => when(v) },
      { key: 'duration_s', label: 'Duration', format: (v) => fmt.duration(v) },
      { key: 'rx_bytes', label: 'Received', format: (v) => fmt.bytes(v) },
      { key: 'tx_bytes', label: 'Sent', format: (v) => fmt.bytes(v) },
      { key: 'total_bytes', label: 'Total', format: (v) => fmt.bytes(v) },
      { key: 'roaming', label: 'Roaming', format: (v) => (v === 'home' ? 'Home' : badge(v === 'roaming' ? 'Roaming' : 'Partly roaming', 'warn')) },
      { key: 'ipv4', label: 'IPv4', cls: 'mono' },
      { key: 'ipv6', label: 'IPv6', cls: 'mono' },
    ],
    emptyText: 'No sessions recorded yet.',
    headerActions: [downloadLink('/api/tools/sessions/export', 'Export CSV', 'internet-sessions.csv'),
                    h('button.btn.btn-sm', { type: 'button', onclick: () => lst.reload() }, 'Refresh')],
  });
}

export default {
  id: 'tools-diagnostics', path: '/tools/diagnostics', group: 'tools', order: 10, title: 'Diagnostics', icon: 'tools',
  render(el, ctx) {
    el.appendChild(h('div.page-head', h('div', h('h1', 'Diagnostics'), h('p', 'Test the connection, look at modem and network details, and the engineering data the original firmware kept hidden.'))));
    let t = null;
    const goTab = (id) => t && t.select(id);
    t = tabs(el, [
      { id: 'tests', title: 'Network tests', render: (p) => renderTests(p) },
      { id: 'modem', title: 'Modem & network', render: (p) => renderModem(p, ctx, goTab) },
      { id: 'field', title: 'Field test', render: (p) => renderField(p) },
      { id: 'lab', title: 'Lab test', render: (p) => renderLab(p, ctx, goTab) },
      { id: 'pco', title: 'Carrier events', render: (p) => renderPco(p) },
      { id: 'sessions', title: 'Internet sessions', render: (p) => renderSessions(p) },
    ], { initial: (ctx.query && ctx.query.tab) || 'tests', hashBase: '/tools/diagnostics' });
  },
};
