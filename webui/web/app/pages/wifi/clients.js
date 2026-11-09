// Wi-Fi > Clients: every connected device (Wi-Fi, USB, Ethernet) with rename, block/unblock and the Wi-Fi limit.
import { h, clear } from '../../ui/dom.js';
import { list } from '../../ui/list.js';
import { form } from '../../ui/form.js';
import { card, badge, button, toast, confirmSimple, stat } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { guard, encMac } from './_util.js';

const CONN = { wifi: ['Wi-Fi', 'accent'], usb: ['USB', 'info'], ethernet: ['Ethernet', 'info'], other: ['Other', 'neutral'], unknown: ['Not connected', 'neutral'] };

export default {
  id: 'wifi-clients', path: '/wifi/clients', group: 'wifi', order: 50, title: 'Clients', icon: 'users', feature: 'wifi.clients',
  render(el) {
    const summary = h('div'), table = h('div'), lower = h('div.grid-2');
    el.append(summary, table, lower);
    const loadAll = async () => { const d = await get('/api/clients'); paintSummary(summary, d); return d; };

    const tbl = list(table, {
      title: 'Connected devices', icon: 'users',
      description: 'Devices using the router now, plus devices with a recent address lease. Names you give are shown everywhere in this interface.',
      load: async () => (await loadAll()).clients,
      rowKey: (r) => r.mac + r.connection,
      columns: [
        { key: 'name', label: 'Name', format: (v, r) => h('span', v || h('span.muted', 'Unnamed'), r.is_self && badge('This device', 'ok'),
          r.custom_name && r.hostname && r.custom_name !== r.hostname ? h('span.hint', ` (${r.hostname})`) : null) },
        { key: 'connection', label: 'Connection', format: (v, r) => h('span', badge(...(CONN[v] || CONN.other)),
          r.network === 'guest' ? badge('Guest', 'warn') : null, !r.online && r.connection !== 'unknown' ? badge('Offline', 'neutral') : null) },
        { key: 'ip', label: 'IP address', cls: 'mono', format: (v, r) => h('span', v || '—', r.ipv6 ? h('div.hint', r.ipv6) : null) },
        { key: 'mac', label: 'MAC address', cls: 'mono' },
        { key: 'lease_remaining_s', label: 'Lease', format: (v) => (v ? fmt.duration(v) : '—') },
        { key: 'blocked', label: 'Status', format: (v, r) => (v ? badge('Blocked', 'bad') : r.allowed ? badge('Allowed', 'ok') : badge('OK', 'neutral')) },
      ],
      fields: [
        { key: 'name', label: 'Name', type: 'text', maxLength: 32, placeholder: 'For example: Anna\'s phone',
          help: 'Leave empty to use the name the device reports.' },
      ],
      editTitle: 'Rename device',
      update: async (row) => {
        const r = await put(`/api/clients/${encMac(row.mac)}`, { name: (row.name || '').trim() });
        if (!r.vendor_synced && row.name) toast('Saved here; the router\'s own device list could not be updated', 'warn', 5000);
      },
      rowActions: (r, reload) => [
        r.blocked
          ? button('Unblock', async () => { await post(`/api/clients/${encMac(r.mac)}/unblock`, {}); toast('Unblocked'); await reload(); blocked.reload(); }, { small: true })
          : !r.is_self && button('Block', async () => {
            if (!(await confirmSimple('Block device', `Block ${r.name || r.mac}? It is disconnected from Wi-Fi and cannot join again until you unblock it.`, 'Block', 'danger'))) return;
            const x = await post(`/api/clients/${encMac(r.mac)}/block`, {});
            toast(x.block_method === 'runtime' ? 'Blocked until Wi-Fi restarts (the permanent block list is not available)' : 'Blocked');
            await reload(); blocked.reload();
          }, { small: true }),
      ].filter(Boolean),
      emptyText: 'No devices are connected.',
      refreshMs: 15000,
    });

    const blockedHost = h('div'), limitHost = h('div');
    lower.append(blockedHost, limitHost);
    const blocked = list(blockedHost, {
      title: 'Blocked devices', icon: 'firewall', description: 'Blocked devices cannot join Wi-Fi.',
      load: async () => (await get('/api/clients/blocked')).blocked,
      columns: [{ key: 'name', label: 'Name', format: (v) => v || h('span.muted', 'Unnamed') }, { key: 'mac', label: 'MAC address', cls: 'mono' }],
      rowActions: (r, reload) => [button('Unblock', async () => { await post(`/api/clients/${encMac(r.mac)}/unblock`, {}); toast('Unblocked'); await tbl.reload(); reload(); }, { small: true })],
      emptyText: 'No device is blocked.',
    });

    guard(limitHost, () => get('/api/clients/limit'), (d) => {
      let pending = d;
      form(limitHost, {
        title: 'Wi-Fi connection limit', icon: 'wifi',
        description: 'How many Wi-Fi devices may be connected at the same time (main and guest network together).',
        load: async () => { if (pending) { const x = pending; pending = null; return x; } return get('/api/clients/limit'); },
        save: (ch) => put('/api/clients/limit', ch),
        fields: [{ key: 'max_clients', label: 'Maximum Wi-Fi devices', type: 'number', min: 1, max: d.max_clients_limit,
          required: true, unit: `of ${d.max_clients_limit}` }],
      });
      return null;
    });

    return () => { tbl.destroy(); };
  },
};

function paintSummary(el, d) {
  const c = d.counts || {};
  clear(el).appendChild(card(null, h('div.stats',
    stat('Wi-Fi', String(c.wifi ?? 0), d.max_clients ? `limit ${d.max_clients}` : ''),
    stat('USB', String(c.usb ?? 0)), stat('Ethernet', String(c.ethernet ?? 0)),
    stat('Blocked', String((d.blocked || []).length)),
    d.allow_list_mode ? stat('MAC filter', 'Allow-list', 'only listed devices') : null)));
}
