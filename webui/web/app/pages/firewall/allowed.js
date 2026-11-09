// Firewall › Allowed ports: open ports on the router itself (inbound to the router's own services).
import { h } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { list } from '../../ui/list.js';
import { alert } from '../../ui/widgets.js';
import { get, put, post, del } from '../../api.js';
import { section, prefetched, PROTOCOLS, protoLabel, portText, onOff } from './_fw.js';

export default {
  id: 'firewall-allowed', path: '/firewall/allowed', group: 'firewall', order: 40, title: 'Allowed ports',
  icon: 'firewall', feature: 'firewall',
  async render(el) {
    await section(el, '/api/firewall/allowed', (box, state) => {
      box.appendChild(alert('warn', 'These rules open ports on the router itself, not on your devices. Opening ports on the mobile connection can expose the router\'s own services (for example remote login) to the Internet. Use port forwarding to reach a device on your network.'));
      const top = h('div');
      box.appendChild(top);
      form(top, {
        title: 'Allowed ports', icon: 'firewall',
        description: 'A hidden router feature: accept incoming connections to the listed ports on the chosen interfaces.',
        load: prefetched({ enabled: state.enabled }, '/api/firewall/allowed'),
        save: async (ch) => ({ enabled: (await put('/api/firewall/allowed', ch)).enabled }),
        warn: (ch) => ch.enabled ? 'The router will accept connections to the listed ports. Continue?' : null,
        fields: [{ key: 'enabled', label: 'Use the allowed-port rules', type: 'switch' }],
      });
      const ifHelp = 'Comma separated, e.g. ' + state.suggested_interfaces.map(([n, l]) => `${n} (${l})`).join(', ');
      const host = h('div');
      box.appendChild(host);
      list(host, {
        title: 'Rules', icon: 'network', addLabel: 'Add rule', addTitle: 'Allow incoming connections', editTitle: 'Edit rule',
        max: state.max_rules,
        load: async () => (await get('/api/firewall/allowed')).rules,
        emptyText: 'No rules. The router accepts no extra incoming connections.',
        columns: [
          { key: 'interfaces', label: 'Interfaces', format: (v) => (v || []).join(', '), cls: 'mono' },
          { key: 'start', label: 'Ports', format: (v, r) => r.mode === 'list' ? (r.ports || []).join(', ') : portText(r.start, r.end), cls: 'mono' },
          { key: 'protocol', label: 'Protocol', format: protoLabel },
          { key: 'enabled', label: 'Status', format: onOff },
        ],
        defaults: { enabled: true, interfaces: '', mode: 'range', start: '', end: '', ports: '', protocol: 'tcp' },
        describe: () => 'this rule',
        fields: [
          { key: 'interfaces', label: 'Interfaces', type: 'text', required: true, maxLength: 50, help: ifHelp,
            format: (v) => Array.isArray(v) ? v.join(',') : v, parse: (v) => v },
          { key: 'mode', label: 'Ports given as', type: 'radio', options: [['range', 'A range'], ['list', 'A list']] },
          { key: 'start', label: 'First port', type: 'number', min: 1, max: 65535, required: true, show: (v) => v.mode !== 'list' },
          { key: 'end', label: 'Last port', type: 'number', min: 1, max: 65535, show: (v) => v.mode !== 'list', help: 'Leave empty for one port.' },
          { key: 'ports', label: 'Ports', type: 'text', required: true, maxLength: 63, show: (v) => v.mode === 'list',
            placeholder: '22, 8080', format: (v) => Array.isArray(v) ? v.join(', ') : v,
            pattern: /^\s*\d{1,5}(\s*,\s*\d{1,5})*\s*$/, patternMsg: 'Port numbers separated by commas' },
          { key: 'protocol', label: 'Protocol', type: 'select', options: PROTOCOLS },
          { key: 'enabled', label: 'Active', type: 'switch' },
        ],
        add: (row) => post('/api/firewall/allowed/rules', pick(row)),
        update: (row, old) => put('/api/firewall/allowed/rules/' + encodeURIComponent(old.id), pick(row)),
        remove: (row) => del('/api/firewall/allowed/rules/' + encodeURIComponent(row.id)),
      });
    });
  },
};

function pick(row) {
  const out = { enabled: row.enabled !== false, mode: row.mode || 'range', protocol: row.protocol || 'tcp',
    interfaces: Array.isArray(row.interfaces) ? row.interfaces : String(row.interfaces || '') };
  if (out.mode === 'list') out.ports = Array.isArray(row.ports) ? row.ports : String(row.ports || '');
  else { out.start = row.start; if (row.end !== '' && row.end !== null && row.end !== undefined) out.end = row.end; }
  return out;
}
