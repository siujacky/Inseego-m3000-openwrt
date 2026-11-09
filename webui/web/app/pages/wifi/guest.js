// Wi-Fi > Guest network: on/off and band, name, security, password, isolation and guest restrictions.
import { renderNetwork } from './_network.js';

export default {
  id: 'wifi-guest', path: '/wifi/guest', group: 'wifi', order: 20, title: 'Guest network', icon: 'wifi',
  feature: 'wifi.guest',
  render(el) { return renderNetwork(el, 'guest'); },
};
