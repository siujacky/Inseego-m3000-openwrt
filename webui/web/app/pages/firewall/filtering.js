// Firewall › Port filtering: outbound white-list (standard applications + custom port ranges).
import { h, clear } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { list } from '../../ui/list.js';
import { get, put, post, del } from '../../api.js';
import { section, notes, vpnNote, PROTOCOLS, protoLabel, portText, onOff } from './_fw.js';

function toForm(s) {
  return { enabled: s.enabled, standard: Object.keys(s.standard || {}).filter(k => s.standard[k]) };
}

export default {
  id: 'firewall-filtering', path: '/firewall/filtering', group: 'firewall', order: 30, title: 'Port filtering',
  icon: 'firewall', feature: 'firewall',
  async render(el) {
    await section(el, '/api/firewall/filtering', (box, state) => {
      const noteBox = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px' } });
      box.appendChild(noteBox);
      clear(noteBox).append(...notes(state));
      vpnNote(noteBox);
      let last = state;

      const top = h('div');
      box.appendChild(top);
      let first = toForm(state);
      let appsList = null;
      form(top, {
        title: 'Port filtering', icon: 'firewall',
        description: 'When port filtering is on, devices on your network can reach the Internet only with the applications you allow below. Everything else is blocked.',
        load: async () => {
          if (first) { const d = first; first = null; return d; }
          last = await get('/api/firewall/filtering');
          return toForm(last);
        },
        save: async (ch, values) => {
          const body = {};
          if ('enabled' in ch) body.enabled = ch.enabled;
          if ('standard' in ch) {
            body.standard = {};
            for (const a of state.standard_apps) body.standard[a.id] = (values.standard || []).includes(a.id);
          }
          last = await put('/api/firewall/filtering', body);
          if (appsList) appsList.reload();          // refresh the slot counter
          return toForm(last);
        },
        warn: (ch, v) => {
          if (!v.enabled) return null;
          const custom = (last.custom || []).filter(c => c.enabled).length;
          if (!(v.standard || []).length && !custom) return 'Nothing is allowed: with port filtering on, all Internet traffic from your devices will be blocked. Continue?';
          return ch.enabled ? 'Only the allowed applications will work. Devices may lose Internet access for anything else. Continue?' : null;
        },
        fields: [
          { key: 'enabled', label: 'Port filtering', type: 'switch' },
          { key: 'standard', label: 'Allowed standard applications', type: 'chips', full: true,
            options: state.standard_apps.map(a => [a.id, a.label, { title: 'Ports ' + a.ports }]),
            help: 'Email uses 10 of the router\'s ' + state.max_entries + ' filter slots (POP3, IMAP, SMTP and their secure variants).' },
        ],
      });

      const used = h('p.hint');
      const appsHost = h('div');
      box.appendChild(appsHost);
      appsList = list(appsHost, {
        title: 'Custom applications', icon: 'network', addLabel: 'Add application', addTitle: 'Allow an application',
        editTitle: 'Edit application',
        description: 'Allow other programs by their port numbers (for example games or messaging apps).',
        load: async () => {
          const s = await get('/api/firewall/filtering');
          last = s;
          clear(used).append(`${s.used_entries} of ${s.max_entries} filter slots used`);
          return s.custom.map(c => Object.assign({}, c, { end: c.end !== c.start ? c.end : '' }));
        },
        headerActions: [used],
        emptyText: 'No custom applications.',
        columns: [
          { key: 'name', label: 'Name' },
          { key: 'start', label: 'Ports', format: (v, r) => portText(r.start, r.end), cls: 'mono' },
          { key: 'protocol', label: 'Protocol', format: protoLabel },
          { key: 'enabled', label: 'Status', format: onOff },
        ],
        defaults: { enabled: true, protocol: 'tcp', name: '', start: '', end: '' },
        describe: (r) => `the application "${r.name}"`,
        fields: [
          { key: 'name', label: 'Name', type: 'text', required: true, maxLength: 24, pattern: /^[^[\]]+$/, patternMsg: 'Brackets are not allowed' },
          { key: 'protocol', label: 'Protocol', type: 'select', options: PROTOCOLS },
          { key: 'start', label: 'First port', type: 'number', min: 1, max: 65535, required: true },
          { key: 'end', label: 'Last port', type: 'number', min: 1, max: 65535, help: 'Leave empty for a single port.',
            validate: (v, vals) => v && Number(v) < Number(vals.start) ? 'Must not be lower than the first port' : null },
          { key: 'enabled', label: 'Allowed', type: 'switch' },
        ],
        add: (row) => post('/api/firewall/filtering/apps', pick(row)),
        update: (row, old) => put('/api/firewall/filtering/apps/' + encodeURIComponent(old.id), pick(row)),
        remove: (row) => del('/api/firewall/filtering/apps/' + encodeURIComponent(row.id)),
      });
    });
  },
};

function pick(row) {
  const out = {};
  for (const k of ['name', 'start', 'protocol', 'enabled']) if (row[k] !== undefined && row[k] !== '') out[k] = row[k];
  out.end = (row.end === '' || row.end === undefined) ? null : row.end;    // empty = single port
  return out;
}
