import { interfaceList, h, card, badge, button, alert, toast, form, get, put, post, del, block, raw, record, enc, security, confirmMessage, pageScope, heading, modemSupports } from './_shared.js';
import { wifiCreateBody, hardwareSummary } from '../system/_native.js';
export default { id: 'native-network', path: '/openwrt/network', group: 'network', order: 5, title: 'Interfaces', feature: 'openwrt',
render(el) {
  heading(el, 'Interfaces', 'Configure upstream and local interfaces. USB management remains protected.');
  const scope = pageScope(), section = scope.section;
const networksBox = block(el);
    section(networksBox, () => get('/api/openwrt/interfaces'), d => [card('Interface runtime', interfaceList(d.runtime)), ...d.interfaces.map(iface => {
      const host = h('div');
      const key = enc(iface.name);
      form(host, { title: 'Network: ' + iface.name, icon: 'network',
        description: 'Configure the existing netifd interface. Changing protocol or device can remove access to the router.',
        load: async () => Object.assign({}, iface, { password: '', dns_text: [].concat(iface.dns || []).join(', ') }),
        save: async ch => {
          if ('dns_text' in ch) { ch.dns = ch.dns_text.split(/[\s,]+/).filter(Boolean); delete ch.dns_text; }
          if (ch.password === '') delete ch.password;
          const out = await put('/api/openwrt/interfaces/' + key, ch, { confirmMessage });
          return Object.assign({}, out, { password: '', dns_text: [].concat(out.dns || []).join(', ') });
        },
        actions: ['up', 'down'].map(action => button(action === 'up' ? 'Connect interface' : 'Disconnect interface', async () => {
          await post('/api/openwrt/interfaces/' + key + '/' + action, {}, { confirmMessage }); toast('Interface action requested');
        })),
        fields: [{ key: 'enabled', label: 'Enabled', type: 'switch' },
          { key: 'proto', label: 'Protocol', type: 'select', options: [['static', 'Static address'], ['dhcp', 'DHCP client'], ['dhcpv6', 'DHCPv6 client'], ['qmi', 'USB QMI'], ['modemmanager', 'ModemManager'], ['none', 'Unmanaged']] },
          { key: 'device', label: 'Device', help: 'Linux interface or a configured USB QMI node. Integrated QRTR uses its native modem service.', maxLength: 128 },
          { key: 'ipaddr', label: 'IPv4 address', show: x => x.proto === 'static' },
          { key: 'netmask', label: 'Subnet mask', show: x => x.proto === 'static' },
          { key: 'gateway', label: 'Gateway', show: x => x.proto === 'static' },
          { key: 'dns_text', label: 'DNS servers', help: 'Separate IP addresses with spaces or commas.' },
          { key: 'metric', label: 'Route metric', type: 'number', min: 0, max: 65535 },
          { key: 'apn', label: 'APN', show: x => ['qmi', 'modemmanager'].includes(x.proto) },
          { key: 'username', label: 'APN user name', show: x => ['qmi', 'modemmanager'].includes(x.proto) },
          { key: 'password', label: 'APN password', type: 'password', placeholder: iface.password_set ? 'Leave empty to keep saved password' : '', show: x => ['qmi', 'modemmanager'].includes(x.proto) },
          { key: 'auth', label: 'APN authentication', type: 'select', options: [['none', 'None'], ['pap', 'PAP'], ['chap', 'CHAP'], ['both', 'PAP or CHAP']], show: x => x.proto === 'qmi' },
          { key: 'pdptype', label: 'Mobile IP type', type: 'select', options: [['ip', 'IPv4'], ['ipv6', 'IPv6'], ['ipv4v6', 'IPv4 and IPv6']], show: x => x.proto === 'qmi' }] });
      return host;
    })]);


  return scope.destroy;
} };
