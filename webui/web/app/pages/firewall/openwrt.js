// Typed native firewall editor. The stock firewall pages retain their own APIs.
import { h, clear } from '../../ui/dom.js';
import { card, button, alert, errorBox, toast } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { get, put, post, del } from '../../api.js';

const base = '/api/openwrt/firewall';
const confirmMessage = 'Save and reload the firewall for this RAM boot? Traffic may be interrupted. USB management uses a protected fixed policy. If reload fails, the saved configuration and running rules may differ.';
const policies = ['ACCEPT', 'DROP', 'REJECT'].map(x => [x, x]);
const families = [['any', 'IPv4 and IPv6'], ['ipv4', 'IPv4'], ['ipv6', 'IPv6']];
const groups = [['forwardings', 'Zone forwarding', 'Add zone forwarding'], ['rules', 'Traffic rule', 'Add traffic rule'], ['redirects', 'Port redirect', 'Add port redirect']];
const raw = value => h('pre.pre', JSON.stringify(value, null, 2));

function fields(kind, create, data) {
  const zones = data.zones.filter(x => x.options.name !== data.protection.zone && x.options.enabled !== false).map(x => [x.options.name, x.options.name]);
  const result = create ? [{ key: 'section', label: 'Configuration identifier', required: true, maxLength: 64, pattern: /^[A-Za-z0-9_]+$/,
    help: 'Unique UCI name: letters, numbers and underscores. This identifier cannot be renamed.' }] : [];
  if (kind === 'defaults' || kind === 'zones') {
    if (kind === 'zones') result.push({ key: 'network_text', label: 'Networks', required: true,
      help: 'Existing netifd names, separated by spaces. Each network can belong to one zone. USB management cannot be reassigned.' });
    result.push(...['input', 'output', 'forward'].map(key => ({ key, label: key[0].toUpperCase() + key.slice(1) + ' policy', type: 'select', options: policies })));
    if (kind === 'defaults') result.push({ key: 'drop_invalid', label: 'Drop invalid connections', type: 'switch' },
      { key: 'synflood_protect', label: 'SYN flood protection', type: 'switch' });
    else result.push({ key: 'enabled', label: 'Zone enabled', type: 'switch' }, { key: 'family', label: 'IP family', type: 'select', options: families },
      { key: 'masq', label: 'IPv4 masquerading', type: 'switch' }, { key: 'masq6', label: 'IPv6 masquerading', type: 'switch' },
      { key: 'mtu_fix', label: 'Clamp TCP MSS to path MTU', type: 'switch' });
    return result;
  }
  result.push({ key: 'name', label: 'Label', maxLength: 64 }, { key: 'enabled', label: 'Enabled', type: 'switch' },
    { key: 'src', label: 'Source zone', required: true, type: 'select', options: zones },
    { key: 'dest', label: kind === 'rules' ? 'Destination (empty means this router)' : 'Destination zone', type: 'select',
      required: kind !== 'rules', options: kind === 'rules' ? [['', 'This router'], ...zones] : zones },
    { key: 'family', label: 'IP family', type: 'select', options: kind === 'redirects' ? families.slice(1) : families });
  if (kind === 'forwardings') return result;
  result.push({ key: 'proto', label: 'Protocol', type: 'select', options: [['tcp', 'TCP'], ['udp', 'UDP'], ['tcp udp', 'TCP and UDP'],
    ...(kind === 'rules' ? [['icmp', 'ICMP (explicit family required)'], ['ipv6-icmp', 'IPv6 ICMP'], ['all', 'All protocols']] : [])] },
  { key: 'src_ip', label: 'Source IP or CIDR', help: 'Optional single IP or CIDR; matches involving the protected USB subnet are excluded.' },
  { key: 'src_mac', label: 'Source MAC', help: 'Optional unicast MAC address.' });
  if (kind === 'rules') result.push({ key: 'src_port', label: 'Source port or range', help: 'Optional. TCP/UDP only, for example 1024-65535.' },
    { key: 'dest_ip', label: 'Destination IP or CIDR' }, { key: 'dest_port', label: 'Destination port or range', help: 'Optional. TCP/UDP only.' },
    { key: 'target', label: 'Action', type: 'select', options: policies });
  else result.push({ key: 'src_dip', label: 'External destination IP', help: 'Optional exact address; leave empty for all addresses in the source zone.' },
    { key: 'src_dport', label: 'External port or range', required: true, help: 'For example 8080 or 8080-8081.' },
    { key: 'dest_ip', label: 'Internal destination IP', required: true, help: 'Exact unicast address in the destination network.' },
    { key: 'dest_port', label: 'Internal port or range', required: true, help: 'External and internal ranges must have equal size.' },
    { key: 'reflection', label: 'NAT reflection', type: 'switch', help: 'Permit clients to use the external address when firewall4 can derive a reflection path.' });
  return result;
}

function initial(kind, row, data) {
  const zones = data.zones.filter(x => x.options.name !== data.protection.zone && x.options.enabled !== false).map(x => x.options.name);
  const source = kind === 'redirects' && zones.includes('wan') ? 'wan' : zones.includes('lan') ? 'lan' : zones[0] || '';
  const destination = kind === 'rules' ? '' : zones.find(x => x !== source) || '';
  const options = row ? row.options : { section: '', name: '', enabled: true, family: kind === 'redirects' ? 'ipv4' : 'any',
    src: source, dest: destination, proto: ['tcp'], target: 'ACCEPT', reflection: false };
  const out = Object.assign({}, options);
  if (kind === 'zones') out.network_text = (out.network || []).join(' ');
  if (kind === 'defaults' && out.synflood_protect === undefined) out.synflood_protect = out.syn_flood === '1';
  if (kind !== 'defaults') { out.enabled = out.enabled !== false; out.family = out.family || 'any'; }
  if (Array.isArray(out.proto)) out.proto = out.proto.join(' ');
  return out;
}

function payload(kind, input, create) {
  const out = {};
  // Only fields offered by this editor cross the API boundary. Readback can
  // contain preserved legacy options that this bounded workflow does not edit.
  const allowed = kind === 'defaults' ? ['input', 'output', 'forward', 'drop_invalid', 'synflood_protect']
    : kind === 'zones' ? ['network_text', 'input', 'output', 'forward', 'enabled', 'family', 'masq', 'masq6', 'mtu_fix']
      : ['name', 'src', 'dest', 'enabled', 'family', ...(kind === 'forwardings' ? [] : ['proto', 'src_ip', 'src_mac', 'dest_ip', 'dest_port',
        ...(kind === 'rules' ? ['src_port', 'target'] : ['src_dip', 'src_dport', 'reflection'])])];
  if (create) allowed.push('section');
  for (const key of allowed) if (key in input && input[key] !== undefined) out[key] = input[key];
  if ('proto' in out) out.proto = out.proto.split(/\s+/).filter(Boolean);
  if ('network_text' in out) { out.network = out.network_text.split(/[\s,]+/).filter(Boolean); delete out.network_text; }
  return out;
}

export default {
  id: 'firewall-openwrt', path: '/firewall/openwrt', group: 'firewall', order: 0,
  title: 'OpenWrt firewall', icon: 'shield', feature: 'openwrt',
  async render(el) {
    clear(el);
    const outcome = h('div'), content = h('div');
    el.append(outcome, content);
    let active = true;
    function showOutcome(result, error) {
      if (!active) return;
      const saved = result.saved === true ? 'Configuration saved and read back.' : result.saved === false ? 'Configuration was not saved.' : 'Saved state is unknown; refresh and inspect readback.';
      const applied = result.applied === true ? 'Firewall reload succeeded; packet behavior still requires a traffic test.'
        : result.applied === false ? 'Firewall reload failed or was not requested. Running rules may differ from the saved configuration.'
          : 'Firewall reload could not be confirmed. Running rules may differ from the saved configuration.';
      clear(outcome).appendChild(alert(error ? 'warn' : 'info', saved + ' ' + applied + (error ? ' ' + error.message : '')));
    }
    async function write(method, path, body) {
      try {
        const out = await method(path, body, { confirmMessage });
        if (out.saved !== true || out.applied !== true) { const e = Error('Firewall save/apply result was not verified'); e.details = out; throw e; }
        showOutcome(out);
        await load(out.readback);
        return out;
      } catch (e) {
        if (e.details && 'saved' in e.details) {
          showOutcome(e.details, e);
          if (e.details.readback) await load(e.details.readback);
        }
        throw e;
      }
    }
    function edit(kind, row, data, create = false, title) {
      const host = h('div');
      const path = base + '/' + kind + (kind === 'defaults' || create ? '' : '/' + encodeURIComponent(row.section));
      form(host, { title, description: create ? 'Creates a named UCI section, validates the staged firewall, then commits and reloads.' : 'Save validates the staged firewall before commit. Settings last for this RAM boot.',
        load: async () => initial(kind, row, data), fields: fields(kind, create, data),
        savedMessage: 'Configuration saved and firewall reload succeeded',
        save: async (changes, values) => {
          const out = await write(create ? post : put, path, payload(kind, create ? values : changes, create));
          const fresh = kind === 'defaults' ? out.readback.defaults : (out.readback[kind] || []).find(x => x.section === (create ? values.section : row.section));
          return initial(kind, create ? null : fresh || row, out.readback);
        }, actions: !create && ['forwardings', 'rules', 'redirects'].includes(kind) && row.removable !== false
          ? [button('Remove ' + title.toLowerCase(), async () => { await write(del, path, {}); toast('Configuration saved and firewall reload succeeded'); })] : [] });
      return host;
    }
    async function load(known) {
      try {
        const data = known || await get(base);
        if (!active) return;
        clear(content);
        content.appendChild(card('Firewall4 state', [alert('info', 'Firewall configuration applies to this RAM boot. UPnP and vendor ALG controls are not exposed by this workflow.'),
          h('p', data.runtime.reason || (data.runtime.available ? 'inet fw4 table detected' : 'Firewall unavailable')),
          alert('info', data.protection.reason), data.protection.problem && alert('warn', data.protection.problem),
          button('Refresh firewall configuration', () => load())]));
        const writable = data.runtime.available === true && !data.protection.problem;
        const show = (kind, row, title) => {
          if (writable && row.editable) return edit(kind, row, data, false, title);
          return card(title, [alert('warn', row.reason || 'Editing requires an available firewall runtime and intact USB management policy.'), raw(row.options),
            writable && row.removable && button('Remove ' + title.toLowerCase(), async () => {
              await write(del, base + '/' + kind + '/' + encodeURIComponent(row.section), {}); toast('Configuration saved and firewall reload succeeded');
            })]);
        };
        if (data.defaults) content.appendChild(show('defaults', data.defaults, 'Default policies'));
        for (const zone of data.zones) content.appendChild(show('zones', zone, 'Zone: ' + zone.options.name));
        for (const [kind, label, addLabel] of groups) {
          for (const row of data[kind]) content.appendChild(show(kind, row, label + ': ' + (row.options.name || row.section)));
          if (writable) content.appendChild(edit(kind, null, data, true, addLabel));
        }
        if (data.other_sections.length) content.appendChild(card('Other firewall sections', [
          alert('info', 'These sections are preserved and require ROOT administration outside this bounded editor.'), raw(data.other_sections)]));
      } catch (e) { if (active) clear(content).appendChild(errorBox(e, () => load())); }
    }
    await load();
    return () => { active = false; };
  },
};
