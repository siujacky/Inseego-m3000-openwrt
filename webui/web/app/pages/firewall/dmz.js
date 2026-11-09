// Firewall › DMZ: send every unsolicited inbound IPv4 connection to one device.
import { h, clear } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { alert } from '../../ui/widgets.js';
import { put } from '../../api.js';
import { section, prefetched, notes, lanHint, inLan } from './_fw.js';

export default {
  id: 'firewall-dmz', path: '/firewall/dmz', group: 'firewall', order: 50, title: 'DMZ', icon: 'firewall', feature: 'firewall',
  async render(el) {
    await section(el, '/api/firewall/dmz', (box, state) => {
      const noteBox = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px' } });
      box.appendChild(noteBox);
      const showNotes = (s) => clear(noteBox).append(...notes(s, [
        s.forwarding_enabled && alert('warn', 'Port forwarding is on. The DMZ cannot be turned on while port forwarding is on; turn port forwarding off first.')]));
      showNotes(state);
      const host = h('div');
      box.appendChild(host);
      form(host, {
        title: 'DMZ host', icon: 'firewall',
        description: 'The DMZ host receives every connection from the Internet that no other rule handles, so it is fully exposed. Use it only for a device that has its own firewall. IPv4 only.',
        load: prefetched({ enabled: state.enabled, ip: state.ip }, '/api/firewall/dmz'),
        save: async (ch, values) => {
          const s = await put('/api/firewall/dmz', { enabled: values.enabled, ip: values.ip || '' });
          showNotes(s);
          return { enabled: s.enabled, ip: s.ip };
        },
        warn: (ch, v) => v.enabled && (ch.enabled || ch.ip) ? `All unsolicited traffic from the Internet will go to ${v.ip}. Continue?` : null,
        fields: [
          { key: 'enabled', label: 'DMZ', type: 'switch', disabled: (v) => !!state.forwarding_enabled && !v.enabled },
          { key: 'ip', label: 'DMZ host address', type: 'text', placeholder: state.lan ? state.lan.ip.replace(/\.\d+$/, '.100') : '',
            help: lanHint(state), required: false, validate: (v, vals) => (vals.enabled && !v) ? 'Required when the DMZ is on' : (v ? inLan(v, state) : null) },
        ],
      });
    });
  },
};
