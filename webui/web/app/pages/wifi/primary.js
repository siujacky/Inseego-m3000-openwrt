// Wi-Fi > Main network: name, security (incl. WPA3 / Enhanced Open), password, visibility, isolation, WPS.
import { renderNetwork } from './_network.js';

export default {
  id: 'wifi-primary', path: '/wifi/primary', group: 'wifi', order: 10, title: 'Main network', icon: 'wifi',
  feature: 'wifi.primary',
  render(el) { return renderNetwork(el, 'primary'); },
};
