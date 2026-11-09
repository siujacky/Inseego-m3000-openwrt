// Firewall › Port forwarding: on/off + rules (single ports, ranges, translated ports, preset applications).
import { h, clear } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { list } from '../../ui/list.js';
import { alert } from '../../ui/widgets.js';
import { get, put, post, del } from '../../api.js';
import { section, prefetched, notes, vpnNote, PROTOCOLS, protoLabel, portText, onOff, lanHint, inLan } from './_fw.js';

export default {
  id: 'firewall-forwarding', path: '/firewall/forwarding', group: 'firewall', order: 20, title: 'Port forwarding',
  icon: 'firewall', feature: 'firewall',
  async render(el) {
    await section(el, '/api/firewall/forwarding', (box, state) => {
      const noteBox = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px' } });
      box.appendChild(noteBox);
      const showNotes = (s) => {
        clear(noteBox).append(...notes(s, [s.dmz_enabled && alert('warn', 'A DMZ host is set. Port forwarding cannot be turned on while the DMZ is on; turn the DMZ off first.')]));
        vpnNote(noteBox);
      };
      showNotes(state);

      const top = h('div');
      box.appendChild(top);
      form(top, {
        title: 'Port forwarding', icon: 'firewall',
        description: 'Forward connections that arrive on the mobile connection to a device on your network (IPv4 only). Most mobile networks share public addresses (carrier-grade NAT); forwarding then only works with a public IP from your provider.',
        load: prefetched({ enabled: state.enabled }, '/api/firewall/forwarding'),
        save: async (ch) => { const s = await put('/api/firewall/forwarding', ch); showNotes(s); return { enabled: s.enabled }; },
        warn: (ch) => ch.enabled ? 'Devices with forwarding rules become reachable from the Internet on those ports. Continue?' : null,
        fields: [{ key: 'enabled', label: 'Port forwarding', type: 'switch', disabled: () => !!state.dmz_enabled && !state.enabled }],
      });

      const used = h('p.hint');
      const presetOpts = [['', 'Custom rule']].concat(state.presets.map(p => [p.id, `${p.label} — ${p.members.map(m => m[1]).join(', ')}`]));
      const rulesHost = h('div');
      box.appendChild(rulesHost);
      list(rulesHost, {
        title: 'Rules', icon: 'network', addLabel: 'Add rule', addTitle: 'Add port forwarding rule', editTitle: 'Edit rule',
        description: `Each port of a range uses one of the router's ${state.max_entries} forwarding slots. A preset application adds one rule per port.`,
        load: async () => {
          const s = await get('/api/firewall/forwarding');
          clear(used).append(`${s.used_entries} of ${s.max_entries} forwarding slots used`);
          // single-port rules show an empty "last port" so editing the first port keeps them single
          return s.rules.map(r => Object.assign({}, r, { external_end: r.external_end !== r.external ? r.external_end : '' }));
        },
        headerActions: [used],
        emptyText: 'No port forwarding rules yet.',
        columns: [
          { key: 'name', label: 'Name' },
          { key: 'protocol', label: 'Protocol', format: protoLabel },
          { key: 'external', label: 'Internet port', format: (v, r) => portText(r.external, r.external_end), cls: 'mono' },
          { key: 'ip', label: 'Device', cls: 'mono' },
          { key: 'internal', label: 'Device port', format: (v, r) => portText(r.internal, r.internal_end), cls: 'mono' },
          { key: 'enabled', label: 'Status', format: onOff },
        ],
        defaults: { preset: '', enabled: true, protocol: 'tcp', name: '', ip: '', external: '', external_end: '', internal: '' },
        describe: (r) => `the rule "${r.name}"`,
        fields: [
          { key: 'preset', label: 'Application', type: 'select', options: presetOpts, full: true, show: (v) => !v.id,
            help: 'Pick a common server application, or "Custom rule" to enter ports yourself.' },
          { key: 'name', label: 'Name', type: 'text', required: true, maxLength: 24, show: (v) => !v.preset,
            pattern: /^[^[\]]+$/, patternMsg: 'Brackets are not allowed' },
          { key: 'ip', label: 'Device address', type: 'text', required: true, placeholder: state.lan ? state.lan.ip.replace(/\.\d+$/, '.100') : '',
            help: lanHint(state), validate: (v) => inLan(v, state) },
          { key: 'protocol', label: 'Protocol', type: 'select', options: PROTOCOLS, show: (v) => !v.preset },
          { key: 'external', label: 'Internet port (first)', type: 'number', min: 1, max: 65535, required: true, show: (v) => !v.preset,
            help: 'The port that clients on the Internet connect to.' },
          { key: 'external_end', label: 'Internet port (last, optional)', type: 'number', min: 1, max: 65535, show: (v) => !v.preset,
            help: 'For a range of ports. Leave empty for a single port.',
            validate: (v, vals) => v && Number(v) < Number(vals.external) ? 'Must not be lower than the first port' : null },
          { key: 'internal', label: 'Device port (first)', type: 'number', min: 1, max: 65535, show: (v) => !v.preset,
            help: 'Leave empty to use the same port number on the device.' },
          { key: 'enabled', label: 'Active', type: 'switch' },
        ],
        add: (row) => post('/api/firewall/forwarding/rules', clean(row)),
        update: (row, old) => put('/api/firewall/forwarding/rules/' + encodeURIComponent(old.id), clean(row)),
        remove: (row) => del('/api/firewall/forwarding/rules/' + encodeURIComponent(row.id)),
      });
    });
  },
};

function clean(row) {
  const out = {};
  for (const k of ['preset', 'name', 'ip', 'protocol', 'external', 'enabled']) {
    if (row[k] !== undefined && row[k] !== '' && row[k] !== null) out[k] = row[k];
  }
  if (out.preset) return { preset: out.preset, ip: out.ip, enabled: out.enabled !== false };
  delete out.preset;
  // empty = "single port" / "same port as on the Internet side"
  for (const k of ['external_end', 'internal']) out[k] = (row[k] === '' || row[k] === undefined) ? null : row[k];
  return out;
}
