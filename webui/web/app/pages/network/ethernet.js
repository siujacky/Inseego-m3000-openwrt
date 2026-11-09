// Network › Ethernet: port on/off, LAN or WAN role, link state.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, alert } from '../../ui/widgets.js';
import { get, put } from '../../api.js';
import { block, safeForm, banner } from './_net.js';

const ROLE = { lan: 'LAN', wan_dhcp: 'WAN (DHCP client)', wan_static: 'WAN (static IP)', wan_pppoe: 'WAN (PPPoE)', unknown: 'Unknown' };

function linkBadge(on) {
  if (on === null || on === undefined) return '—';
  return badge(on ? 'Connected' : 'No cable', on ? 'ok' : 'neutral');
}

export default {
  id: 'network-ethernet', path: '/network/ethernet', group: 'network', order: 40, title: 'Ethernet', icon: 'ethernet',
  feature: 'network.ethernet',
  async render(el) {
    const note = banner(el);
    const statusBox = block(el), formBox = block(el), eth1Box = block(el);

    const showStatus = (d) => {
      const k = d.kernel || {};
      clear(statusBox).appendChild(card('Port status', kv([
        ['Port', d.enabled === null || d.enabled === undefined ? '—' : badge(d.enabled ? 'On' : 'Off', d.enabled ? 'ok' : 'neutral')],
        ['Link', linkBadge(d.link)],
        ['Speed', k.speed_mbps ? `${k.speed_mbps} Mbit/s` : '—', k.duplex ? ` ${k.duplex} duplex` : ''],
        ['Role', ROLE[d.role] || d.role || '—'],
        ['MAC address', d.mac ? h('span.mono', d.mac) : '—'],
        ['Devices on this port', d.clients === null || d.clients === undefined ? '—' : String(d.clients)],
        ['Ports on this model', d.port_count ? String(d.port_count) : '—'],
      ]), { icon: 'ethernet' }));
      clear(eth1Box);
      if (d.eth1) {
        eth1Box.appendChild(card('Second Ethernet port', kv([
          ['Role', ROLE[d.eth1.role] || d.eth1.role], ['Link', linkBadge(d.eth1.link)],
        ]), { icon: 'ethernet' }));
      }
      if (d.role === 'wan_static' || d.role === 'wan_pppoe') {
        eth1Box.appendChild(alert('info', `The port is set to ${ROLE[d.role]} by another tool. This interface can switch it to LAN or WAN (DHCP).`));
      }
    };

    safeForm(formBox, {
      title: 'Ethernet settings', icon: 'ethernet',
      description: 'Use the port for local devices (LAN) or to get internet from another modem or router (WAN). A WAN port can act as a fallback or primary connection; see WAN & failover.',
      load: async () => { const d = await get('/api/ethernet'); showStatus(d); return d; },
      save: async (ch) => { const d = await put('/api/ethernet', ch); showStatus(d); note.restart(d, 'The new port role'); return d; },
      warn: (ch) => {
        const m = [];
        if (ch.enabled === false) m.push('Devices on the Ethernet port lose their connection.');
        if (ch.enabled === true) m.push('The Ethernet port uses more battery power.');
        if (ch.role) m.push(ch.role === 'lan' ? 'The port becomes a LAN port after the router restarts.'
          : 'After the restart the port expects an internet connection. If you manage the router through this port, you will lose access to it.');
        return m.length ? m.join(' ') : null;
      },
      fields: [
        { key: 'enabled', label: 'Ethernet port', type: 'switch' },
        { key: 'role', label: 'Port role', type: 'radio', options: [
          ['lan', 'LAN', 'Connect computers, switches or access points'],
          ['wan_dhcp', 'WAN', 'Connect a modem or router; the address is obtained by DHCP'],
        ], help: 'Changing the role needs a restart.' },
      ],
    });
  },
};
