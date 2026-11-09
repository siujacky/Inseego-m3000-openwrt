// Network › DNS: manual servers, servers in use, rebinding protection, DNS filter (allow / block list).
import { h } from '../../ui/dom.js';
import { card, kv, loadInto, button, toast, confirmSimple } from '../../ui/widgets.js';
import { get, put, del } from '../../api.js';
import { block, safeForm, banner, isUnavailable, friendly, DOMAIN_RX } from './_net.js';

function addrMsg(v) {
  const s = String(v || '').trim();
  if (!s) return null;
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(s) && s.split('.').every(x => Number(x) <= 255)) return null;
  if (s.includes(':') && /^[0-9A-Fa-f:.]+$/.test(s)) return null;
  return 'Enter an IPv4 or IPv6 address';
}

function parseDomains(text) {
  return String(text || '').split(/[\s,]+/).map(s => s.trim()).filter(Boolean);
}

export default {
  id: 'network-dns', path: '/network/dns', group: 'network', order: 20, title: 'DNS', icon: 'globe',
  async render(el) {
    const note = banner(el);
    const manualBox = block(el), activeBox = block(el), rebindBox = block(el), filterBox = block(el);

    const activeBody = h('div');
    activeBox.appendChild(card('DNS servers in use', activeBody, { icon: 'globe',
      actions: [button('Refresh', () => showActive(), { small: true, icon: 'refresh' })] }));
    const showActive = () => loadInto(activeBody, () => get('/api/dns/active'), (d) => kv([
      ['Internet connection (IPv4)', d.v4 && d.v4.length ? h('span.mono', d.v4.join(', ')) : 'None (not connected)'],
      ['Internet connection (IPv6)', d.v6 && d.v6.length ? h('span.mono', d.v6.join(', ')) : 'None'],
      ['Offered by the mobile network', d.mobile_v4 && d.mobile_v4.length ? h('span.mono', d.mobile_v4.join(', ')) : '—'],
    ]));

    safeForm(manualBox, {
      title: 'Manual DNS', icon: 'globe',
      description: 'Use your own DNS servers instead of the ones the mobile network provides. A wrong server breaks name lookups for every device.',
      load: () => get('/api/dns/manual'),
      save: async (ch, v) => {
        const body = Object.assign({}, ch);
        if ('enabled' in ch || 'primary' in ch || 'secondary' in ch) Object.assign(body, { enabled: v.enabled, primary: v.primary || '', secondary: v.secondary || '' });
        const d = await put('/api/dns/manual', body);
        showActive();
        return d;
      },
      fields: [
        { key: 'enabled', label: 'Manual DNS', type: 'switch' },
        { key: 'primary', label: 'DNS server 1', required: true, show: v => v.enabled, validate: addrMsg, placeholder: '1.1.1.1',
          help: 'IPv4 or IPv6. The original interface only accepted IPv4; IPv6 is passed to the vendor service as is.' },
        { key: 'secondary', label: 'DNS server 2', show: v => v.enabled, placeholder: 'optional',
          validate: (x, v) => addrMsg(x) || (x && x === v.primary ? 'Must differ from server 1' : null) },
      ],
    });

    safeForm(rebindBox, {
      title: 'DNS rebinding protection', icon: 'lock',
      description: 'Blocks DNS answers that point public names at addresses in your local network, which some web attacks use to reach routers and other local devices.',
      load: () => get('/api/dns/rebinding'),
      save: async (ch) => { const d = await put('/api/dns/rebinding', ch); note.restart(d, 'DNS rebinding protection'); return d; },
      fields: [
        { key: 'enabled', label: 'Rebinding protection', type: 'switch',
          help: 'Turn off only if a local service needs public names that resolve to local addresses. Takes effect after a restart.' },
      ],
    });

    const filterForm = safeForm(filterBox, {
      title: 'DNS filter', icon: 'firewall',
      description: 'Block domains for every device, or allow only a list of domains. Applies to devices that use the router for DNS. The DNS service restarts briefly when you save.',
      load: async () => {
        const d = await get('/api/dns/filter');
        return Object.assign({}, d, { domains_text: (d.domains || []).join('\n') });
      },
      save: async (ch, v) => {
        const d = await put('/api/dns/filter', { enabled: v.enabled, mode: v.mode, domains: parseDomains(v.domains_text) });
        return Object.assign({}, d, { domains_text: (d.domains || []).join('\n') });
      },
      warn: (ch, v) => v.enabled && v.mode === 'allow'
        ? 'With an allow list, every domain that is not listed stops working, including the ones apps and system updates need.'
        : null,
      fields: [
        { key: 'enabled', label: 'DNS filter', type: 'switch' },
        { key: 'mode', label: 'List type', type: 'radio', options: [
          ['block', 'Block list', 'The listed domains and their sub-domains do not resolve'],
          ['allow', 'Allow list', 'Only the listed domains resolve; everything else is blocked'],
        ] },
        { key: 'domains_text', label: 'Domains', type: 'textarea', rows: 6, full: true, placeholder: 'ads.example.com\ntracker.example.net',
          help: v => `One domain per line (sub-domains are included automatically). ${parseDomains(v.domains_text).length} of ${v.max_domains || 50}.`,
          validate: (x, v) => {
            const list = parseDomains(x);
            if (v.enabled && !list.length) return 'Add at least one domain';
            const bad = list.map(d => d.replace(/^\*\./, '').replace(/\.$/, '')).find(d => !DOMAIN_RX.test(d) || /^[\d.]+$/.test(d));
            if (bad) return `Not a domain name: ${bad}`;
            if (list.length > (v.max_domains || 50)) return `At most ${v.max_domains || 50} domains`;
            if (list.join(',').length > 500) return 'The list is too long (500 characters at most)';
            return null;
          } },
        { key: 'last_operation', label: 'Last change', type: 'readonly', format: (x) => x === 'added' ? 'List saved' : x === 'deleted' ? 'List removed' : (x || '—') },
      ],
      actions: [button('Remove filter', async () => {
        if (!(await confirmSimple('Remove the DNS filter', 'Remove the list and stop filtering?', 'Remove', 'danger'))) return;
        try { await del('/api/dns/filter'); toast('DNS filter removed'); filterForm.reload(); } catch (e) {
          toast(isUnavailable(e) ? friendly(e) : e.message, 'bad', 6000);
        }
      }, { kind: 'ghost' })],
    });

    showActive();
  },
};
