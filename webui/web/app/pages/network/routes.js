// Network › Static routes: vendor static-route feature (no stock UI) and the kernel routing tables.
import { h, clear } from '../../ui/dom.js';
import { card, button, loadInto, badge } from '../../ui/widgets.js';
import { get, put, post, del } from '../../api.js';
import { block, safeForm, safeList } from './_net.js';

const IFACES = [['wwan', 'Mobile network'], ['ethernet', 'Ethernet WAN'], ['wifi', 'Wi-Fi client WAN'], ['usb', 'USB WAN'],
  ['wireguard1', 'WireGuard tunnel 1'], ['wireguard2', 'WireGuard tunnel 2'], ['wireguard3', 'WireGuard tunnel 3']];

function netMsg(x, v) {
  const s = String(x || '').trim();
  if (!s) return 'Required';
  const [addr, len] = s.split('/');
  if (Number(v.family) === 4) {
    if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(addr) || addr.split('.').some(p => Number(p) > 255)) return 'Enter an IPv4 network such as 10.0.0.0/8';
    if (len !== undefined && !(/^\d+$/.test(len) && Number(len) >= 1 && Number(len) <= 32)) return 'Prefix length 1-32';
  } else {
    if (!addr.includes(':') || !/^[0-9A-Fa-f:.]+$/.test(addr)) return 'Enter an IPv6 network such as 2001:db8::/32';
    if (len !== undefined && !(/^\d+$/.test(len) && Number(len) >= 1 && Number(len) <= 128)) return 'Prefix length 1-128';
  }
  return null;
}

function gwMsg(x, v) {
  const s = String(x || '').trim();
  if (Number(v.family) === 4) return /^\d{1,3}(\.\d{1,3}){3}$/.test(s) && s.split('.').every(p => Number(p) <= 255) ? null : 'Enter the gateway IPv4 address';
  return s.includes(':') && /^[0-9A-Fa-f:.]+$/.test(s) ? null : 'Enter the gateway IPv6 address';
}

function routeTable(rows) {
  if (!rows || !rows.length) return h('p.hint', 'No routes.');
  return h('div.table-wrap', h('table.table',
    h('thead', h('tr', ['Destination', 'Gateway', 'Interface', 'Source', 'Metric', 'Origin'].map(t => h('th', t)))),
    h('tbody', rows.map(r => h('tr',
      h('td.mono', { dataset: { label: 'Destination' } }, r.type ? `${r.type} ${r.dest}` : r.dest),
      h('td.mono', { dataset: { label: 'Gateway' } }, r.via || '—'),
      h('td.mono', { dataset: { label: 'Interface' } }, r.dev || '—'),
      h('td.mono', { dataset: { label: 'Source' } }, r.src || '—'),
      h('td', { dataset: { label: 'Metric' } }, r.metric || '—'),
      h('td', { dataset: { label: 'Origin' } }, r.proto || '—'))))));
}

export default {
  id: 'network-routes', path: '/network/routes', group: 'network', order: 70, title: 'Static routes', icon: 'network',
  async render(el) {
    const enBox = block(el), listBox = block(el), tableBox = block(el);

    safeForm(enBox, {
      title: 'Static routes', icon: 'network',
      description: 'Send traffic for specific networks through a chosen WAN or VPN tunnel and gateway. The original interface had no page for this.',
      load: () => get('/api/routes'),
      save: async (ch) => put('/api/routes', ch),
      fields: [
        { key: 'enabled', label: 'Use static routes', type: 'switch', help: 'Routes below are only applied while this is on.' },
      ],
    });

    safeList(listBox, {
      title: 'Routes', icon: 'link', addLabel: 'Add route', addTitle: 'Add a static route', editTitle: 'Change route', max: 25,
      emptyText: 'No static routes.',
      load: () => get('/api/routes/static'),
      columns: [
        { key: 'network', label: 'Destination', cls: 'mono' },
        { key: 'gateway', label: 'Gateway', cls: 'mono' },
        { key: 'interface_label', label: 'Through' },
        { key: 'metric', label: 'Metric' },
        { key: 'family', label: 'Type', format: (v) => badge(Number(v) === 6 ? 'IPv6' : 'IPv4', 'neutral') },
      ],
      defaults: { family: 4, interface: 'wwan', metric: 0 },
      fields: [
        { key: 'family', label: 'Type', type: 'radio', options: [[4, 'IPv4'], [6, 'IPv6']] },
        { key: 'network', label: 'Destination network', required: true, maxLength: 50, validate: netMsg,
          placeholder: '10.0.0.0/8', help: 'A network with prefix length, or a single address.' },
        { key: 'gateway', label: 'Gateway', required: true, maxLength: 45, validate: gwMsg },
        { key: 'interface', label: 'Through', type: 'select', options: IFACES },
        { key: 'metric', label: 'Metric', type: 'number', min: 0, max: 65535, help: 'Lower wins when several routes match.' },
      ],
      rowKey: r => r.id,
      describe: r => `the route to ${r.network}`,
      add: (r) => post('/api/routes/static', { family: Number(r.family), network: r.network, gateway: r.gateway, interface: r.interface, metric: Number(r.metric) || 0 }),
      update: (r, old) => put(`/api/routes/static/${encodeURIComponent(old.id)}`, { family: Number(r.family), network: r.network, gateway: r.gateway, interface: r.interface, metric: Number(r.metric) || 0 }),
      remove: (r) => del(`/api/routes/static/${encodeURIComponent(r.id)}`),
    });

    const v4 = h('div'), v6 = h('div'), raw = h('pre.pre');
    const tabs = h('div.tabs');
    const panes = { v4, v6, raw };
    let active = 'v4';
    const showTab = (k) => {
      active = k;
      tabs.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.k === k));
      Object.entries(panes).forEach(([n, p]) => { p.style.display = n === k ? '' : 'none'; });
    };
    [['v4', 'IPv4'], ['v6', 'IPv6'], ['raw', 'Raw output']].forEach(([k, label]) =>
      tabs.appendChild(h('button.tab', { type: 'button', dataset: { k }, onclick: () => showTab(k) }, label)));
    const body = h('div');
    tableBox.appendChild(card('Routing table in use', [tabs, body, v4, v6, raw], { icon: 'chip',
      actions: [button('Refresh', () => loadTable(), { small: true, icon: 'refresh' })] }));
    const loadTable = () => loadInto(body, () => get('/api/routes/table'), (d) => {
      clear(v4).appendChild(routeTable(d.v4));
      clear(v6).appendChild(routeTable(d.v6));
      raw.textContent = `# ip route\n${d.raw_v4 || ''}\n# ip -6 route\n${d.raw_v6 || ''}`;
      showTab(active);
      return d.ok ? null : h('p.hint', 'The routing table could not be read.');
    });
    showTab('v4');
    loadTable();
  },
};
