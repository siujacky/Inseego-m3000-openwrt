// Firewall › General: VPN passthrough, WAN ping, IPv6 firewall, HTTP redirect (+ read-only state).
import { h } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { put } from '../../api.js';
import { section, prefetched } from './_fw.js';

const yesNo = (v) => v === null || v === undefined ? 'Unknown' : (v ? 'On' : 'Off');
const SEVERITY = { high: 'High', medium: 'Medium', low: 'Low' };

export default {
  id: 'firewall-general', path: '/firewall/general', group: 'firewall', order: 10, title: 'General', icon: 'firewall',
  feature: 'firewall',
  async render(el) {
    await section(el, '/api/firewall/general', (box, data) => {
      const host = h('div');
      box.appendChild(host);
      form(host, {
        title: 'Firewall', icon: 'firewall',
        description: 'The router always runs a firewall for IPv4: unsolicited connections from the Internet are dropped unless you forward a port or set a DMZ host. These switches change how strict it is.',
        load: prefetched(data, '/api/firewall/general'),
        save: (changes) => put('/api/firewall/general', changes),
        warn: (ch) => {
          if (ch.ipv6_firewall === false) return 'With the IPv6 firewall off, every device that has an IPv6 address can be reached directly from the Internet. Continue?';
          if (ch.wan_ping === true) return 'The router will answer ping requests from the Internet, which makes it easier to find. Continue?';
          return null;
        },
        fields: [
          { type: 'section', label: 'Protection' },
          { key: 'ipv6_firewall', label: 'IPv6 firewall', type: 'switch',
            help: 'Blocks connections from the Internet to your devices over IPv6. Leave on unless a device must be reachable over IPv6.' },
          { key: 'wan_ping', label: 'Answer ping from the Internet', type: 'switch',
            help: 'Lets the router reply to ping (ICMP echo) on the mobile connection. Hidden in the original interface.' },
          { type: 'section', label: 'Passthrough and redirect' },
          { key: 'vpn_passthrough', label: 'VPN passthrough', type: 'switch',
            help: 'Lets VPN software on your devices (IPsec, PPTP and L2TP clients) work through the router. Not needed for the router\'s own VPN client.' },
          { key: 'http_redirect', label: 'HTTP redirect', type: 'switch',
            help: 'Router feature that can redirect web requests to the router\'s own pages (for example while there is no mobile connection). Hidden in the original interface.' },
          { type: 'section', label: 'Information', help: 'The firmware has no working setting for these values, so they are shown read-only.' },
          { key: 'dns_passthrough', label: 'DNS passthrough', type: 'readonly', format: yesNo },
          { key: 'severity', label: 'Firewall level', type: 'readonly', format: (v) => SEVERITY[v] || (v ? String(v) : 'Unknown') },
          { key: 'ipv6_transparent', label: 'IPv6 transparent mode (original interface flag)', type: 'readonly', format: yesNo,
            help: 'The original interface called the inbound IPv6 policy "transparent mode". Shown for reference; change it with the IPv6 firewall switch above.' },
          { key: 'ipv6_enabled', label: 'IPv6 on the local network', type: 'readonly', format: yesNo },
        ],
      });
    });
  },
};
