// Wi-Fi > MAC filter: allow-list mode (only listed devices may join) or block-list mode (everyone except blocked
// devices), both lists, and "add my device".
import { h, clear } from '../../ui/dom.js';
import { list } from '../../ui/list.js';
import { card, alert, badge, button, toast, confirmSimple, modal } from '../../ui/widgets.js';
import { get, put, post, del } from '../../api.js';
import { guard, encMac } from './_util.js';

const MAC_RX = /^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$/;
const macField = { key: 'mac', label: 'MAC address', type: 'text', required: true, maxLength: 17, placeholder: '00:11:22:33:44:55',
  pattern: MAC_RX, patternMsg: 'Enter a MAC address like 00:11:22:33:44:55',
  help: 'Some phones use a random ("private") address per network; use the one shown for this network.' };
const nameField = { key: 'name', label: 'Name (optional)', type: 'text', maxLength: 32 };

export default {
  id: 'wifi-macfilter', path: '/wifi/macfilter', group: 'wifi', order: 60, title: 'MAC filter', icon: 'firewall',
  feature: 'wifi.macfilter',
  render(el) {
    const modeHost = h('div'), allowHost = h('div'), blockHost = h('div');
    el.append(modeHost, h('div.grid-2', allowHost, blockHost));
    let view = null;
    let allowList = null, blockList = null;
    const refresh = async () => { view = await get('/api/wifi/macfilter'); paintMode(); return view; };

    function paintMode() {
      const v = view;
      const on = v.mode === 'allow';
      const body = h('div',
        h('p.card-desc', 'Block-list mode: every device may join Wi-Fi except the blocked ones. Allow-list mode: only devices on the allow-list may join; '
          + 'all others are disconnected. The filter applies to Wi-Fi only, not to USB or Ethernet.'),
        h('div.chips', { style: { marginBottom: '12px' } },
          v.mode ? badge(on ? 'Allow-list mode is on' : 'Block-list mode', on ? 'warn' : 'ok') : badge('Mode unknown', 'neutral'),
          badge(`${v.allowed.length} allowed`, 'neutral'), badge(`${v.blocked.length} blocked`, 'neutral'),
          v.self_mac && badge(`Your device: ${v.self_mac}`, v.self_allowed ? 'ok' : 'neutral')),
        !v.mode_known && alert('info', 'The current mode cannot be read on this device; choosing a mode still works.'),
        on ? null : (v.wps_on && alert('info', 'Switching to allow-list mode turns WPS off on the main network.')),
        h('div.form-actions',
          on ? button('Switch to block-list mode', async () => {
            if (!(await confirmSimple('Switch to block-list mode', 'Every device that is not blocked will be able to join Wi-Fi again.', 'Switch'))) return;
            await put('/api/wifi/macfilter', { mode: 'block' }); toast('Block-list mode on'); await reloadAll();
          }) : button('Switch to allow-list mode', async () => {
            if (!v.allowed.length) { toast('Add at least one device to the allow-list first', 'bad'); return; }
            if (!(await confirmSimple('Turn on the allow-list', 'Wi-Fi devices that are not on the allow-list are disconnected and cannot join. Make sure your own devices are on the list.', 'Turn on', 'danger'))) return;
            const r = await put('/api/wifi/macfilter', { mode: 'allow' });
            toast(r.wps_switched_off ? 'Allow-list on; WPS was switched off' : 'Allow-list on');
            await reloadAll();
          }, { kind: 'primary' }),
          v.self_mac && !v.self_allowed && button('Add my device to the allow-list', async () => {
            await post('/api/wifi/macfilter/allow-self', {}); toast('Your device was added'); await reloadAll();
          })));
      clear(modeHost).appendChild(card('MAC filter', body, { icon: 'firewall' }));
    }

    async function reloadAll() {
      await refresh();
      allowList && allowList.reload();
      blockList && blockList.reload();
    }

    const quickAdd = (which) => async () => {
      const clients = await get('/api/clients');
      const have = new Set((which === 'allowed' ? view.allowed : view.blocked).map(e => e.mac));
      const cands = clients.clients.filter(c => c.connection === 'wifi' || c.connection === 'unknown').filter(c => !have.has(c.mac) && !(which === 'blocked' && c.is_self));
      if (!cands.length) { toast('No connected Wi-Fi device to add', 'warn'); return; }
      const m = modal({ title: which === 'allowed' ? 'Allow a connected device' : 'Block a connected device',
        body: h('div', cands.map(c => h('div.input-group', { style: { justifyContent: 'space-between', margin: '6px 0' } },
          h('span', c.name || 'Unnamed', ' ', h('span.mono.hint', c.mac), c.is_self ? badge('This device', 'ok') : null),
          button(which === 'allowed' ? 'Allow' : 'Block', async () => {
            await post('/api/wifi/macfilter/' + which, { mac: c.mac, name: c.custom_name || '' });
            toast(which === 'allowed' ? 'Added to the allow-list' : 'Blocked'); m.close(true); await reloadAll();
          }, { small: true, kind: which === 'blocked' ? 'danger' : 'primary' })))),
        actions: [{ label: 'Close', value: null }] });
    };

    guard(modeHost, async () => { view = await get('/api/wifi/macfilter'); return view; }, () => {
      paintMode();
      allowList = list(allowHost, {
        title: 'Allow-list', icon: 'check', description: 'Devices that may join while allow-list mode is on.',
        load: async () => (await refresh()).allowed,
        columns: [{ key: 'name', label: 'Name', format: (v, r) => h('span', v || h('span.muted', 'Unnamed'), r.is_self ? badge('This device', 'ok') : null, r.online ? badge('Online', 'ok') : null) },
          { key: 'mac', label: 'MAC address', cls: 'mono' }],
        fields: [macField, nameField], addLabel: 'Add', addTitle: 'Add to the allow-list',
        add: async (row) => { await post('/api/wifi/macfilter/allowed', { mac: row.mac, name: row.name || '' }); },
        remove: async (row) => { await del(`/api/wifi/macfilter/allowed/${encMac(row.mac)}`); await refresh(); },
        describe: (row) => row.name || row.mac, max: view ? view.max_allowed : 100,
        headerActions: [button('From connected', quickAdd('allowed'), { small: true })],
        emptyText: 'The allow-list is empty.',
      });
      blockList = list(blockHost, {
        title: 'Block-list', icon: 'close', description: 'Devices that can never join Wi-Fi.',
        load: async () => (await refresh()).blocked,
        columns: [{ key: 'name', label: 'Name', format: (v) => v || h('span.muted', 'Unnamed') }, { key: 'mac', label: 'MAC address', cls: 'mono' }],
        fields: [macField, nameField], addLabel: 'Block', addTitle: 'Block a device',
        add: async (row) => { await post('/api/wifi/macfilter/blocked', { mac: row.mac, name: row.name || '' }); },
        remove: async (row) => { await del(`/api/wifi/macfilter/blocked/${encMac(row.mac)}`); await refresh(); },
        describe: (row) => `the block for ${row.name || row.mac}`, max: view ? view.max_blocked : 100,
        headerActions: [button('From connected', quickAdd('blocked'), { small: true })],
        emptyText: 'No device is blocked.',
      });
      return null;
    });
  },
};
