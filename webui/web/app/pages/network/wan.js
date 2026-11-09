// Network › WAN & failover: current connection and traffic, connect/disconnect, WAN manager failover
// (auto-switch, manual WAN, failback, connectivity monitor, self-recovery), WAN priorities.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, alert, button, toast, confirmSimple, loadInto } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { block, safeForm, banner, hostMsg, isUnavailable, friendly, expertMode } from './_net.js';

const WAN = { wwan: 'Mobile network', wifi: 'Wi-Fi client', ethernet: 'Ethernet', usb: 'USB', none: 'None' };
const wanText = (w) => WAN[w] || w || '—';

function stateBadge(s) {
  const t = String(s || '');
  if (/^connected$/i.test(t)) return badge('Connected', 'ok');
  if (/waiting|connecting/i.test(t)) return badge(t, 'warn');
  return badge(t || '—', 'neutral');
}

export default {
  id: 'network-wan', path: '/network/wan', group: 'network', order: 50, title: 'WAN & failover', icon: 'globe',
  async render(el, ctx) {
    const note = banner(el);
    const curBox = block(el), connBox = block(el), mgrNote = block(el), failBox = block(el), prioBox = block(el),
      expertBox = block(el), telBox = block(el);
    let wan = null;

    const statsBody = h('div', { style: { marginTop: '12px' } });
    const showCurrent = () => {
      const c = (wan && wan.current) || null;
      const connected = !!(c && c.connected);
      clear(curBox).appendChild(card('Internet connection', [
        kv([
          ['Active WAN', c ? h('span', wanText(c.wan), ' ', stateBadge(c.v4 || c.v6)) : '—'],
          ['IPv4', c ? h('span', stateBadge(c.v4), c.v4_if ? h('span.muted', ` ${c.v4_if}`) : '') : '—'],
          ['IPv6', c ? h('span', stateBadge(c.v6), c.v6_if ? h('span.muted', ` ${c.v6_if}`) : '') : '—'],
        ]),
        statsBody,
      ], { icon: 'globe', actions: [
        button('Connect', async () => { wan = await post('/api/wan/connect', {}); toast('Connecting'); refresh(); }, { small: true, disabled: connected }),
        button('Disconnect', async () => {
          if (!(await confirmSimple('Disconnect', 'Stop the internet connection? Devices stay connected to the router but lose internet access.', 'Disconnect', 'danger'))) return;
          wan = await post('/api/wan/disconnect', {}); toast('Disconnected'); refresh();
        }, { small: true, kind: 'ghost', disabled: !connected }),
        button('Refresh', () => refresh(), { small: true, icon: 'refresh' }),
      ] }));
      const which = c && c.wan && c.wan !== 'none' ? c.wan : null;
      if (!which) { clear(statsBody).appendChild(h('p.hint', 'No WAN is connected.')); return; }
      loadInto(statsBody, () => get(`/api/wan/stats?wan=${encodeURIComponent(which)}`), (s) => {
        if (!s.available) return h('p.hint', 'Traffic details are not available.');
        const v4 = s.v4 || {}, v6 = s.v6 || {};
        return kv([
          ['IPv4 address', v4.ip ? h('span.mono', v4.ip + (v4.subnet ? ` / ${v4.subnet}` : '')) : '—'],
          ['IPv4 gateway', v4.gateway ? h('span.mono', v4.gateway) : '—'],
          ['IPv6 address', v6.ip ? h('span.mono', v6.ip) : '—'],
          ['Connected for', fmt.duration(Math.max(v4.duration_s || 0, v6.duration_s || 0))],
          ['Data (IPv4)', `↓ ${fmt.bytes(v4.rx_bytes)}  ↑ ${fmt.bytes(v4.tx_bytes)}`],
          ['Data (IPv6)', `↓ ${fmt.bytes(v6.rx_bytes)}  ↑ ${fmt.bytes(v6.tx_bytes)}`],
        ]);
      });
    };

    const showConnections = () => {
      const rows = (wan && wan.connections) || [];
      clear(connBox).appendChild(card('All WAN interfaces', rows.length ? h('div.table-wrap', h('table.table',
        h('thead', h('tr', h('th', 'WAN'), h('th', 'IPv4'), h('th', 'IPv6'), h('th', 'Interface'))),
        h('tbody', rows.map(r => h('tr',
          h('td', { dataset: { label: 'WAN' } }, wanText(r.wan)),
          h('td', { dataset: { label: 'IPv4' } }, stateBadge(r.v4)),
          h('td', { dataset: { label: 'IPv6' } }, stateBadge(r.v6)),
          h('td.mono', { dataset: { label: 'Interface' } }, r.v4_if || r.v6_if || '—')))))) : h('p.hint', 'No information.'),
      { icon: 'network' }));
    };

    const showManagerNote = () => {
      clear(mgrNote);
      const m = (wan && wan.manager) || {};
      if (!m.available) { mgrNote.appendChild(alert('info', 'The WAN manager settings could not be read on this unit.')); return; }
      if (!m.running) {
        mgrNote.appendChild(alert('warn', 'The WAN failover service is not running on this unit' +
          (m.stock_supported === false ? ' (the carrier switched the feature off, so the original interface hid it)' : '') +
          '. You can still store settings; the vendor service may refuse them or apply them only when it runs.'));
      }
    };

    async function refresh() {
      try { wan = await get('/api/wan'); } catch (e) { wan = null; toast(isUnavailable(e) ? friendly(e) : e.message, 'bad'); }
      showCurrent(); showConnections(); showManagerNote();
    }
    await refresh();

    const flat = (m) => Object.assign({}, m, { dns1: (m.dns_urls || [])[0] || '', dns2: (m.dns_urls || [])[1] || '', dns3: (m.dns_urls || [])[2] || '' });

    const failForm = safeForm(failBox, {
      title: 'Failover', icon: 'refresh',
      description: 'Switch to another WAN when the current one stops working, and back again when it recovers.',
      load: async () => flat((await get('/api/wan')).manager || {}),
      save: async (ch, v) => {
        const body = {};
        for (const k of Object.keys(ch)) if (!/^dns[123]$/.test(k)) body[k] = ch[k];
        if ('dns1' in ch || 'dns2' in ch || 'dns3' in ch) body.dns_urls = [v.dns1, v.dns2, v.dns3].map(s => String(s || '').trim());
        if ('manual_wan' in body && !('auto_switch' in body)) body.auto_switch = v.auto_switch;
        const d = await put('/api/wan/failover', body);
        note.restart(d, 'The failover change');
        refresh();
        return flat(d);
      },
      fields: [
        { key: 'auto_switch', label: 'Automatic switching', type: 'switch', help: 'Pick the WAN automatically by priority and availability.' },
        { key: 'manual_wan', label: 'Use this WAN', type: 'radio', show: v => !v.auto_switch,
          options: () => get('/api/wan').then(w => ((w.manager && w.manager.priorities) || []).map(p => [p.wan, wanText(p.wan)])).catch(() => [['wwan', WAN.wwan]]) },
        { type: 'section', label: 'Failback', help: 'Return to the preferred WAN when it works again.' },
        { key: 'failback_enabled', label: 'Failback', type: 'switch' },
        { key: 'failback_min', label: 'Wait before switching back', type: 'number', unit: 'minutes', min: 1, max: 1440, required: true, show: v => v.failback_enabled },
        { key: 'data_check_min', label: 'Mobile data check interval', type: 'number', unit: 'minutes', min: 1, max: 1440, required: true, show: v => v.failback_enabled,
          help: 'How often traffic on the mobile connection is checked before switching back.' },
        { key: 'data_limit_kb', label: 'Mobile traffic threshold', type: 'number', unit: 'KB', min: 1, max: 10240, required: true, show: v => v.failback_enabled,
          help: 'Do not switch back while more than this is being transferred.' },
        { type: 'section', label: 'Connectivity monitor', help: 'Checks that the WAN really reaches the internet by looking up these names.' },
        { key: 'monitor_enabled', label: 'Connectivity monitor', type: 'switch' },
        { key: 'monitor_client_state', label: 'Only while devices are connected', type: 'switch', show: v => v.monitor_enabled,
          help: 'Check only while at least one device is connected to the router.' },
        { key: 'retry_count', label: 'Retries before switching', type: 'number', min: 0, max: 10, required: true, show: v => v.monitor_enabled },
        { key: 'routed_interval_s', label: 'Check interval (active WAN)', type: 'number', unit: 'seconds', min: 30, max: 86400, required: true, show: v => v.monitor_enabled },
        { key: 'online_interval_min', label: 'Check interval (standby WAN)', type: 'number', unit: 'minutes', min: 1, max: 1440, required: true, show: v => v.monitor_enabled },
        { key: 'failed_interval_s', label: 'Retry interval after a failure', type: 'number', unit: 'seconds', min: 10, max: 600, required: true, show: v => v.monitor_enabled },
        { key: 'dns1', label: 'Look up name 1', required: true, validate: hostMsg, show: v => v.monitor_enabled },
        { key: 'dns2', label: 'Look up name 2', required: true, validate: hostMsg, show: v => v.monitor_enabled },
        { key: 'dns3', label: 'Look up name 3', required: true, validate: hostMsg, show: v => v.monitor_enabled },
        { type: 'section', label: 'Recovery' },
        { key: 'self_recovery', label: 'Self-recovery', type: 'switch', help: 'Let the WAN manager restart a WAN that stays without connectivity.' },
        { key: 'waiting_timeout_s', label: 'No-connectivity timeout', type: 'number', unit: 'seconds', min: 1, max: 3600,
          help: 'How long a WAN may stay without connectivity before recovery starts.' },
      ],
    });

    safeForm(prioBox, {
      title: 'WAN priority', icon: 'network',
      description: 'Which WAN the router prefers when several are available. Takes effect after a restart. ' +
        'The WAN manager refuses changes ("bad parameters") while automatic switching is on or the Ethernet port is a LAN port: ' +
        'turn switching off and set the port role to WAN first.',
      load: async () => {
        const m = (await get('/api/wan')).manager || {};
        const pr = (m.priorities || []).map(p => p.wan);
        return { primary: pr[0] || 'wwan', order: pr, supported_count: m.supported_count };
      },
      save: async (ch, v) => {
        const order = [v.primary].concat((v.order || []).filter(w => w !== v.primary));
        const d = await put('/api/wan/priority', { order });
        note.restart(d, 'The new WAN priority');
        const pr = (d.priorities || []).map(p => p.wan);
        failForm.reload();
        return { primary: pr[0] || v.primary, order: pr, supported_count: d.supported_count };
      },
      fields: [
        { key: 'primary', label: 'Preferred WAN', type: 'radio',
          options: () => get('/api/wan').then(w => ((w.manager && w.manager.priorities) || []).map(p => [p.wan, wanText(p.wan)])).catch(() => []),
          help: 'The other WAN is used when the preferred one fails (with automatic switching on).' },
      ],
    });

    if (expertMode(ctx)) {
      const a = h('div'), b = h('div');
      expertBox.append(a, b);
      safeForm(a, {
        title: 'Routing priority (expert)', icon: 'tools',
        description: 'The routing layer keeps its own order of mobile and Ethernet WAN. Takes effect after a restart.',
        load: async () => { const w = await get('/api/wan'); return { first: (w.routing_priority || [])[0] || 'wwan' }; },
        save: async (ch, v) => { const d = await put('/api/wan/routing-priority', { first: v.first }); note.restart(d, 'The routing priority'); return { first: (d.routing_priority || [])[0] || v.first }; },
        fields: [{ key: 'first', label: 'First', type: 'radio', options: [['wwan', WAN.wwan], ['ethernet', WAN.ethernet]] }],
      });
      form(b, {
        title: 'Connection manager priority (expert)', icon: 'tools',
        description: 'Order in which the connection manager brings WANs up.',
        load: async () => { const w = await get('/api/wan'); const p = w.cm_priority || []; return { p1: p[0] || 'ethernet', p2: p[1] || 'wifi', p3: p[2] || 'wwan' }; },
        save: async (ch, v) => { const d = await put('/api/wan/cm-priority', { order: [v.p1, v.p2, v.p3] }); const p = d.cm_priority || []; return { p1: p[0], p2: p[1], p3: p[2] }; },
        fields: ['p1', 'p2', 'p3'].map((k, i) => ({ key: k, label: `Priority ${i + 1}`, type: 'select',
          options: [['wwan', WAN.wwan], ['wifi', WAN.wifi], ['ethernet', WAN.ethernet]],
          validate: (x, v) => ['p1', 'p2', 'p3'].filter(o => v[o] === x).length > 1 ? 'Use each WAN once' : null })),
      });
    }

    const telBody = h('div');
    telBox.appendChild(card('WAN manager statistics', telBody, { icon: 'chip' }));
    loadInto(telBody, () => get('/api/wan/telemetry').catch(e => { if (isUnavailable(e)) return { available: false, message: friendly(e) }; throw e; }),
      (d) => !d.available ? h('p.hint', d.message || 'Not available.') : kv((d.items || []).map(([k, v]) => [k, v])));
  },
};
