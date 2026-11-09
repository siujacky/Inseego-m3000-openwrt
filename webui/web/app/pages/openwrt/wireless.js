import { h, card, badge, button, alert, toast, form, get, put, post, del, block, raw, record, enc, security, confirmMessage, pageScope, heading, modemSupports } from './_shared.js';
import { wifiCreateBody, hardwareSummary } from '../system/_native.js';
export default { id: 'native-wireless', path: '/openwrt/wireless', group: 'wifi', order: 5, title: 'Radios and networks', feature: 'openwrt',
render(el) {
  heading(el, 'Radios and networks', 'Configure access points and Wi-Fi client mode. Runtime state is separate from saved configuration.');
  const scope = pageScope(), section = scope.section;
const wifiBox = block(el);
    section(wifiBox, () => get('/api/openwrt/wifi'), d => {
      const addHost = h('div');
      form(addHost, { title: 'Add an access point or Wi-Fi client', readonly: !d.radios.length,
        description: 'Create another wireless interface on an existing radio. Simultaneous AP and client mode depends on the driver.',
        load: async () => ({ name: '', device: d.radios[0] && d.radios[0].name, network: 'lan', mode: 'ap', ssid: '', security: 'wpa2', password: '' }),
        save: async (ch, values) => { await post('/api/openwrt/wifi', wifiCreateBody(values), { confirmMessage }); toast('Wireless interface created. Refresh this page to edit it.'); return { name: '', device: d.radios[0] && d.radios[0].name, network: 'lan', mode: 'ap', ssid: '', security: 'wpa2', password: '' }; },
        fields: [{ key: 'name', label: 'Configuration name', required: true, maxLength: 64, pattern: /^[A-Za-z0-9_][A-Za-z0-9_-]*$/ },
          { key: 'mode', label: 'Mode', type: 'select', options: [['ap', 'Access point'], ['sta', 'Wi-Fi client']] },
          { key: 'device', label: 'Radio', required: true, type: 'select', options: d.radios.map(x => [x.name, x.name]) },
          { key: 'network', label: 'Network interface', required: true, help: 'Use an existing network, for example lan for an access point or wan for a client.' },
          { key: 'ssid', label: 'Network name', required: true, maxLength: 32 },
          { key: 'security', label: 'Security', type: 'select', options: security },
          { key: 'password', label: 'Wi-Fi password', type: 'password', maxLength: 63, show: x => !['open', 'owe'].includes(x.security) }] });
      return [card('Wireless runtime', [
      record(d.runtime), !d.radios.length && alert('warn', 'No wireless radio is configured. The driver and PCIe device must register before an access point or client can work.')]),
    ...d.radios.map(radio => {
      const host = h('div');
      form(host, { title: 'Radio: ' + radio.name, load: async () => Object.assign({}, radio, { enabled: radio.disabled !== '1' }),
        save: async ch => { const out = await put('/api/openwrt/radios/' + enc(radio.name), ch, { confirmMessage }); return Object.assign({}, out, { enabled: out.disabled !== '1' }); },
        fields: [{ key: 'enabled', label: 'Enabled', type: 'switch' }, { key: 'channel', label: 'Channel', help: 'Use auto or a legal channel number for your region.' },
          { key: 'country', label: 'Country code', maxLength: 2, help: 'Two uppercase letters. The driver enforces local radio restrictions.' },
          { key: 'txpower', label: 'Requested transmit power', type: 'number', unit: 'dBm', min: 0, max: 30 }] });
      return host;
    }), ...d.interfaces.map(iface => {
      const host = h('div');
      form(host, { title: 'Wi-Fi: ' + iface.name, description: 'Access point and client mode use the native wireless configuration. A client needs an appropriate upstream network interface.',
        load: async () => Object.assign({}, iface, { password: '' }),
        save: ch => { if (ch.password === '') delete ch.password; return put('/api/openwrt/wifi/' + enc(iface.name), ch, { confirmMessage }); },
        actions: [button('Remove wireless interface', async () => { await del('/api/openwrt/wifi/' + enc(iface.name), {}, { confirmMessage }); toast('Wireless interface removed. Refresh this page.'); })],
        fields: [{ key: 'enabled', label: 'Enabled', type: 'switch' },
          { key: 'mode', label: 'Mode', type: 'select', options: [['ap', 'Access point'], ['sta', 'Wi-Fi client']] },
          { key: 'device', label: 'Radio', type: 'select', options: d.radios.map(x => [x.name, x.name]) },
          { key: 'network', label: 'Network interface', required: true },
          { key: 'ssid', label: 'Network name', required: true, maxLength: 32 },
          { key: 'security', label: 'Security', type: 'select', options: security },
          { key: 'password', label: 'Wi-Fi password', type: 'password', maxLength: 63, placeholder: iface.password_set ? 'Leave empty to keep saved password' : '' },
          { key: 'hidden', label: 'Hidden SSID', type: 'switch' }, { key: 'isolation', label: 'Client isolation', type: 'switch', show: x => x.mode === 'ap' }] });
      return host;
    }), addHost];
    });


  return scope.destroy;
} };
