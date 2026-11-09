import { leaseList, h, card, badge, button, alert, toast, form, get, put, post, del, block, raw, record, enc, security, confirmMessage, pageScope, heading, modemSupports } from './_shared.js';
import { wifiCreateBody, hardwareSummary } from '../system/_native.js';
export default { id: 'native-dhcp', path: '/openwrt/dhcp', group: 'network', order: 5, title: 'DHCP and leases', feature: 'openwrt',
render(el) {
  heading(el, 'DHCP and leases', 'Address allocation for local networks.');
  const scope = pageScope(), section = scope.section;
const dhcpBox = block(el), leasesBox = block(el);
    section(dhcpBox, () => get('/api/openwrt/dhcp'), d => d.map(entry => {
      const host = h('div');
      form(host, { title: 'DHCP: ' + entry.name, load: async () => Object.assign({}, entry, { ignore: entry.ignore === '1' }),
        save: async ch => { const out = await put('/api/openwrt/dhcp/' + enc(entry.name), ch, { confirmMessage }); return Object.assign({}, out, { ignore: out.ignore === '1' }); },
        fields: [{ key: 'ignore', label: 'Disable DHCP server', type: 'switch' },
          { key: 'start', label: 'First host offset', type: 'number', min: 1, max: 65535, help: '100 means network address plus 100.' },
          { key: 'limit', label: 'Number of leases', type: 'number', min: 1, max: 65535 },
          { key: 'leasetime', label: 'Lease duration', help: 'For example 12h or 30m.' },
          { key: 'ra', label: 'IPv6 router advertisements', type: 'select', options: ['disabled', 'server', 'relay', 'hybrid'].map(x => [x, x]) },
          { key: 'dhcpv6', label: 'DHCPv6 mode', type: 'select', options: ['disabled', 'server', 'relay', 'hybrid'].map(x => [x, x]) }] });
      return host;
    }));


section(leasesBox, () => get('/api/lan/leases'), d => card('Current leases', leaseList(d)));
  return scope.destroy;
} };
