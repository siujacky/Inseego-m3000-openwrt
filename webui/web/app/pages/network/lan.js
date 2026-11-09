// Network › LAN & DHCP: router address, DHCP server, IPv6, names, reserved addresses, leases, MTU.
import { h } from '../../ui/dom.js';
import { button, toast, badge } from '../../ui/widgets.js';
import { get, put, post, del } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { block, safeForm, safeList, banner, ip4, ip4Msg, maskMsg, prefixOf, sameSubnet, macMsg, minutesText,
  newUrl } from './_net.js';

export default {
  id: 'network-lan', path: '/network/lan', group: 'network', order: 10, title: 'LAN & DHCP', icon: 'network',
  async render(el) {
    const note = banner(el);
    const lanBox = block(el), namesBox = block(el), resBox = block(el), leaseBox = block(el), advBox = block(el);
    let lan = {};
    let resList = null, leaseList = null;

    safeForm(lanBox, {
      title: 'Local network', icon: 'network',
      description: 'The router\'s own address on your local network and the DHCP server that gives your devices their addresses.',
      load: async () => { lan = await get('/api/lan'); return lan; },
      save: async (ch) => {
        const d = await put('/api/lan', ch);
        if (d.address_changed) {
          note.show('The router\'s address changed. Reconnect your device (or renew its address) and open the new address:',
            'warn', h('a', { href: newUrl(d.ip) }, newUrl(d.ip)));
        }
        (d.warnings || []).forEach(w => toast(w, 'info', 8000));
        lan = d;
        return d;
      },
      warn: (ch, v) => {
        const msgs = [];
        if (ch.ip || ch.subnet_mask) {
          msgs.push(lan.client_on_lan
            ? `You are connected through this network. After saving, the router moves to ${v.ip}; this page will stop responding until your device reconnects. Then open ${newUrl(v.ip)}.`
            : 'Every device on the local network has to reconnect to get an address in the new network.');
        }
        if (ch.dhcp_enabled === false) msgs.push('With the DHCP server off, devices must use a manual IP address. Devices lose their connection when their current lease ends.');
        if (ch.dhcp_start || ch.dhcp_end) msgs.push('Reserved addresses outside the new range stop working.');
        return msgs.length ? msgs.join(' ') : null;
      },
      fields: [
        { type: 'section', label: 'Router address' },
        { key: 'ip', label: 'Router IP address', required: true, validate: ip4Msg, inputmode: 'decimal',
          help: 'Normally a private address such as 192.168.1.1 or 10.0.0.1.' },
        { key: 'subnet_mask', label: 'Subnet mask', required: true, validate: maskMsg,
          help: (v) => { const p = prefixOf(v.subnet_mask); return p ? `Dotted (255.255.255.0) or prefix length (/24). Now /${p}: ${p <= 30 ? Math.pow(2, 32 - p) - 2 : 0} usable addresses.` : 'Dotted (255.255.255.0) or prefix length (/24).'; } },
        { key: 'mac', label: 'MAC address', type: 'readonly' },
        { key: 'network', label: 'Network', type: 'readonly' },
        { type: 'section', label: 'DHCP server', help: 'Hands out addresses automatically. Keep it on unless every device has a manual address.' },
        { key: 'dhcp_enabled', label: 'DHCP server', type: 'switch' },
        { key: 'dhcp_start', label: 'First address', required: true, show: v => v.dhcp_enabled, inputmode: 'decimal',
          validate: (x, v) => ip4Msg(x) || (!sameSubnet(x, v.ip, prefixOf(v.subnet_mask)) ? 'Must be in the router\'s network' : null) },
        { key: 'dhcp_end', label: 'Last address', required: true, show: v => v.dhcp_enabled, inputmode: 'decimal',
          validate: (x, v) => {
            const e = ip4Msg(x); if (e) return e;
            if (!sameSubnet(x, v.ip, prefixOf(v.subnet_mask))) return 'Must be in the router\'s network';
            const a = ip4(v.dhcp_start), b = ip4(x), r = ip4(v.ip);
            if (a !== null && b < a) return 'Must not be lower than the first address';
            if (a !== null && r !== null && r >= a && r <= b) return 'The router address must not be inside the range';
            return null;
          } },
        { key: 'dhcp_lease_min', label: 'Lease time', type: 'number', unit: 'minutes', min: 1, max: 71582788, required: true,
          show: v => v.dhcp_enabled, help: v => `How long a device keeps its address: ${minutesText(v.dhcp_lease_min) || '—'}.` },
        { key: 'dhcp_pool_size', label: 'Addresses in the range', type: 'readonly', show: v => v.dhcp_enabled,
          format: (x, v) => { const a = ip4(v.dhcp_start), b = ip4(v.dhcp_end); return a !== null && b !== null && b >= a ? String(b - a + 1) : '—'; } },
        { type: 'section', label: 'IPv6' },
        { key: 'ipv6_enabled', label: 'IPv6', type: 'switch', help: 'Give devices IPv6 addresses when the mobile network provides IPv6.' },
        { key: 'ipv6_transparent', label: 'IPv6 transparent mode', type: 'switch', show: v => v.ipv6_enabled,
          help: 'Lets incoming IPv6 connections reach your devices directly (the router does not filter them). Hidden in the original interface.' },
        { key: 'link_local', label: 'Link-local address', type: 'readonly', show: v => v.ipv6_enabled },
      ],
    });

    safeForm(namesBox, {
      title: 'Names', icon: 'globe',
      description: 'The name devices see for the router on the local network.',
      load: () => get('/api/lan/names'),
      save: async (ch) => { const d = await put('/api/lan/names', ch); note.restart(d, 'The new name'); return d; },
      fields: [
        { key: 'device_name', label: 'Device name', required: true, maxLength: 63, pattern: /^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/,
          patternMsg: 'Letters, digits and hyphens only', help: v => v.device_name ? `Reachable as ${v.device_name}.local on most computers.` : '' },
        { key: 'dns_name', label: 'DNS name', required: true, maxLength: 253,
          pattern: /^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/,
          patternMsg: 'Enter a host name such as router.home',
          help: v => v.dns_name_allowed === false && v.dns_name
            ? `Devices that use the router for DNS can open http://${v.dns_name}/. To use that name for this interface, add it to "allowed_hosts" in the interface configuration.`
            : 'A name the router answers for its own address.' },
      ],
    });

    resList = safeList(resBox, {
      title: 'Reserved addresses', icon: 'lock',
      description: 'Always give the same address to a device (by MAC address). The address must be inside the DHCP range. The router applies reservations after a restart.',
      addLabel: 'Reserve', addTitle: 'Reserve an address', editTitle: 'Change reservation', max: 10,
      emptyText: 'No reserved addresses. Use "Reserve" next to a device under "Devices with an address", or add one here.',
      load: async () => (await get('/api/lan/reservations')).map(r => Object.assign({ _existing: true }, r)),
      columns: [
        { key: 'name', label: 'Name' },
        { key: 'mac', label: 'MAC address', cls: 'mono' },
        { key: 'ip', label: 'Reserved IP', cls: 'mono' },
      ],
      defaults: { type: 0, _existing: false },
      fields: [
        { key: 'name', label: 'Name', required: true, maxLength: 32 },
        { key: 'mac', label: 'MAC address', required: true, validate: macMsg, disabled: v => !!v._existing,
          help: v => v._existing ? 'To move the reservation to another device, delete it and add a new one.' : '' },
        { key: 'ip', label: 'Reserved IP address', required: true, validate: ip4Msg,
          help: () => lan.dhcp_start ? `Between ${lan.dhcp_start} and ${lan.dhcp_end}.` : '' },
        { key: 'type', label: 'Device type code', type: 'number', min: 0, max: 10,
          help: 'Vendor device category (0-10). Leave at 0 if unsure.' },
      ],
      rowKey: r => r.mac,
      describe: r => `the reservation for ${r.name || r.mac}`,
      add: async (r) => { const d = await post('/api/lan/reservations', { name: r.name, mac: r.mac, ip: r.ip, type: Number(r.type) || 0 }); note.restart(d, 'Reserved addresses'); leaseList && leaseList.reload(); },
      update: async (r, old) => { const d = await put(`/api/lan/reservations/${encodeURIComponent(old.mac)}`, { name: r.name, ip: r.ip, type: Number(r.type) || 0 }); note.restart(d, 'Reserved addresses'); },
      remove: async (r) => { const d = await del(`/api/lan/reservations/${encodeURIComponent(r.mac)}`); note.restart(d, 'Reserved addresses'); leaseList && leaseList.reload(); },
    });

    leaseList = safeList(leaseBox, {
      title: 'Devices with an address', icon: 'users',
      description: 'Current DHCP leases.',
      emptyText: 'No DHCP leases right now.',
      refreshMs: 30000,
      load: () => get('/api/lan/leases'),
      columns: [
        { key: 'hostname', label: 'Name', format: (v) => v || h('span.muted', 'unknown') },
        { key: 'ip', label: 'IP address', cls: 'mono' },
        { key: 'mac', label: 'MAC address', cls: 'mono' },
        { key: 'connection', label: 'Connected by' },
        { key: 'expires', label: 'Lease ends', format: (v) => v ? fmt.dateTime(v) : '—' },
        { key: 'reserved', label: 'Reserved', format: (v) => v ? badge('Reserved', 'ok') : '' },
      ],
      rowActions: (r, reload) => r.reserved ? [] : [button('Reserve', async () => {
        const d = await post('/api/lan/reservations', { name: (r.hostname || 'device').slice(0, 32), mac: r.mac, ip: r.ip });
        toast('Address reserved');
        note.restart(d, 'Reserved addresses');
        await reload();
        resList && resList.reload();
      }, { small: true })],
    });

    safeForm(advBox, {
      title: 'Advanced', icon: 'chip',
      description: 'Settings the original interface did not show.',
      load: () => get('/api/lan/advanced'),
      save: async (ch) => { const d = await put('/api/lan/advanced', ch); note.restart(d, 'The subnet notation'); return d; },
      fields: [
        { key: 'mtu', label: 'LAN MTU', type: 'number', unit: 'bytes', min: 576, max: 1500,
          help: v => Number(v.mtu) < 1280 ? 'Below 1280 bytes IPv6 stops working on the local network.' : 'Largest packet on the local network. Lower it only to work around problems (1500 is normal).' },
        { key: 'mask_format', label: 'Stored subnet notation', type: 'radio',
          options: [['dotted', 'Dotted', '255.255.255.0'], ['bits', 'Prefix length', '/24']],
          disabled: v => !v.dhcp_enabled,
          help: v => v.dhcp_enabled ? 'How the vendor tools show the subnet mask. This interface accepts both forms.' : 'Turn the DHCP server on to change this (the vendor tool would otherwise clear the DHCP range).' },
      ],
    });
  },
};
