// Network › IP passthrough: give the router's public (mobile) address to one device. Tier 2.
import { h, clear } from '../../ui/dom.js';
import { card, alert, button, toast, kv, badge } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import { block, safeForm, banner, macMsg, ip4Msg, maskMsg, expertMode } from './_net.js';

const IFACE = { usb: 'USB', ethernet: 'Ethernet port', ethernet2: 'Second Ethernet port' };

export default {
  id: 'network-ippt', path: '/network/ippt', group: 'network', order: 30, title: 'IP passthrough', icon: 'link',
  feature: 'network.ippt',
  async render(el, ctx) {
    const note = banner(el);
    const intro = block(el), infoBox = block(el), formBox = block(el), expertBox = block(el);
    intro.appendChild(alert('warn', 'IP passthrough hands the router\'s public mobile address to one device (bridge mode). ' +
      'While it is on, other devices lose internet access, port forwarding and DMZ stop working, Wi-Fi may be switched off, ' +
      'and the router restarts to apply the change. Keep a way to reach the router (for example a USB cable) before you turn it on.'));

    let cur = {};
    const showInfo = (d) => {
      const s = d.stock || {};
      clear(infoBox).appendChild(card('Status', kv([
        ['Passthrough', badge(d.enabled ? 'On' : 'Off', d.enabled ? 'warn' : 'neutral')],
        ['Interface', IFACE[d.interface] || d.interface || '—'],
        ['Client', d.client ? h('span.mono', d.client) : '—'],
        ['Ethernet port', d.ethernet_enabled === null || d.ethernet_enabled === undefined ? '—' : (d.ethernet_enabled ? 'On' : 'Off'),
          d.ethernet_wan_active ? ' (used as WAN: passthrough is blocked)' : ''],
        ['Original interface', s.ippt_editable === false ? 'showed this setting read-only' : (s.ippt_shown === false ? 'hid this setting' : 'showed this setting'),
          s.gateway_shown === false ? '; gateway options were hidden' : ''],
      ]), { icon: 'link' }));
    };

    const formApi = safeForm(formBox, {
      title: 'Passthrough settings', icon: 'link',
      load: async () => {
        cur = await get('/api/lan/ippt');
        showInfo(cur);
        const g = cur.gateway || {};
        return Object.assign({}, cur, { gateway_mode: g.mode || 'default', gateway_address: g.address || '', gateway_netmask: g.netmask || '' });
      },
      save: async (ch, v) => {
        const body = {};
        if ('enabled' in ch || 'interface' in ch || 'client' in ch) Object.assign(body, { enabled: v.enabled, interface: v.interface, client: v.client || '' });
        if ('feature_mode' in ch) body.feature_mode = v.feature_mode;
        if ('gateway_mode' in ch || 'gateway_address' in ch || 'gateway_netmask' in ch) {
          Object.assign(body, { gateway_mode: v.gateway_mode });
          if (v.gateway_mode === 'manual') Object.assign(body, { gateway_address: v.gateway_address, gateway_netmask: v.gateway_netmask });
        }
        const from = `${cur.enabled ? 'on' : 'off'} (${IFACE[cur.interface] || cur.interface || '?'} ${cur.client || ''})`;
        const to = `${v.enabled ? 'on' : 'off'} (${IFACE[v.interface] || v.interface} ${v.client || ''})`;
        const d = await put('/api/lan/ippt', body, { confirmTitle: 'Change IP passthrough',
          confirmMessage: `IP passthrough: ${from} → ${to}. Other devices may lose internet access and the router restarts to apply it.` });
        note.restart(d, 'IP passthrough');
        cur = d; showInfo(d);
        const g = d.gateway || {};
        return Object.assign({}, d, { gateway_mode: g.mode || 'default', gateway_address: g.address || '', gateway_netmask: g.netmask || '' });
      },
      fields: [
        { key: 'enabled', label: 'IP passthrough', type: 'switch', disabled: v => !!v.ethernet_wan_active,
          help: v => v.ethernet_wan_active ? 'Not possible while the Ethernet port is used as a WAN.' : '' },
        { key: 'interface', label: 'Device connected by', type: 'radio',
          options: () => Promise.resolve([['usb', 'USB', 'The computer on the USB cable'], ['ethernet', 'Ethernet port', 'The device on the Ethernet port']]
            .concat(cur && cur.second_ethernet ? [['ethernet2', 'Second Ethernet port', '']] : [])) },
        { key: 'client', label: 'Computer host name', show: v => v.interface === 'usb', maxLength: 63,
          pattern: /^[A-Za-z0-9_.-]{1,63}$/, patternMsg: 'Letters, digits, - _ . only',
          validate: (x, v) => v.enabled && !x ? 'Enter the host name of the USB computer' : null,
          help: 'The name the USB computer reports (its host name).' },
        { key: 'client', label: 'Device MAC address', show: v => v.interface !== 'usb',
          validate: (x, v) => (v.enabled || x) ? macMsg(x) : null, help: 'The MAC address of the device on the Ethernet port.' },
        { type: 'section', label: 'Advanced', help: 'Options the original interface hid.' },
        { key: 'feature_mode', label: 'Enhanced passthrough mode', type: 'switch',
          help: 'Vendor "IPPT feature mode". Needs a restart. Leave off unless you know your setup needs it.' },
        { key: 'gateway_mode', label: 'Gateway given to the device', type: 'select', options: [
          ['default', 'Default (gateway .1, mask 255.255.255.0)'], ['modem', 'From the mobile network'], ['manual', 'Set manually']] },
        { key: 'gateway_address', label: 'Gateway', show: v => v.gateway_mode === 'manual', required: true, validate: ip4Msg },
        { key: 'gateway_netmask', label: 'Netmask', show: v => v.gateway_mode === 'manual', required: true, validate: maskMsg },
      ],
      actions: [button('Restore previous', async () => {
        const d = await post('/api/lan/ippt/restore', {}, { confirmTitle: 'Restore IP passthrough',
          confirmMessage: 'Put back the passthrough settings from before the last change? The router restarts to apply them.' });
        note.restart(d, 'IP passthrough');
        toast('Previous settings restored');
        formApi.reload();
      }, { kind: 'ghost' })],
    });

    if (expertMode(ctx)) {
      expertBox.appendChild(card('USB network function', [
        h('p.card-desc', 'Vendor switch used for passthrough over USB: turns the USB network adapter (RNDIS) off or on. ' +
          'Turning it off cuts the USB network connection to the computer.'),
        h('div.form-actions',
          button('Turn RNDIS off', async () => { await post('/api/lan/ippt/usb-rndis', { disabled: true }, { confirmMessage: 'Turn the USB network adapter (RNDIS) off? A computer connected over USB loses its network connection.' }); toast('Done'); }),
          button('Turn RNDIS on', async () => { await post('/api/lan/ippt/usb-rndis', { disabled: false }); toast('Done'); })),
      ], { icon: 'usb' }));
    }
  },
};
