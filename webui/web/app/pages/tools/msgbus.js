// Tools › Message bus (expert): the vendor daemons' shared state channels, read-only, with auto-refresh.
import { h, clear, debounce } from '../../ui/dom.js';
import { card, kv, badge, button } from '../../ui/widgets.js';
import { get } from '../../api.js';
import { section, pre, lsGet, lsSet, friendlyError } from './_helpers.js';

function valueView(v) {
  if (!v) return '';
  if (!v.ok) return badge(v.message || 'no data', 'warn');
  if (v.type === 'binary') return h('span.mono', `${v.length} bytes: ${v.hex}${v.value ? `  "${v.value}"` : ''}`);
  if (v.type === 'string') return h('span.mono', v.value === '' ? '""' : v.value);
  return h('span.mono', String(v.value));
}

export default {
  id: 'tools-msgbus', path: '/tools/msgbus', group: 'tools', order: 40, title: 'Message bus', expert: true,
  render(el) {
    el.appendChild(h('div.page-head', h('div', h('h1', 'Message bus'),
      h('p', 'The vendor services publish their state on named channels (connection state, Wi-Fi, USB, updates …). Values are read-only here.'))));
    const filter = h('input', { type: 'search', placeholder: 'Filter channels', 'aria-label': 'Filter channels' });
    const listHost = h('div');
    const watchHost = h('div');
    const detailHost = h('div');
    const every = h('select', { 'aria-label': 'Refresh interval', style: { width: 'auto' } },
      [[0, 'Off'], [2, 'Every 2 s'], [5, 'Every 5 s'], [10, 'Every 10 s']].map(([s, l]) => h('option', { value: String(s), selected: s === lsGet('msgbus.every', 5) }, l)));
    let names = [];
    let watched = lsGet('msgbus.watch', ['modem2.conn_state', 'modem2.registration_status', 'wifi.2ghz.enabled']);
    let values = {};
    let timer = null;
    let busy = false;

    function renderList() {
      const q = filter.value.trim().toLowerCase();
      const rows = names.filter(n => !q || n.toLowerCase().includes(q));
      clear(listHost).appendChild(h('div.table-wrap', { style: { maxHeight: '520px', overflowY: 'auto' } }, h('table.table',
        h('tbody', rows.map(n => h('tr',
          h('td', { dataset: { label: 'Watch' }, style: { width: '1%' } }, h('input', { type: 'checkbox', 'aria-label': 'Watch ' + n, checked: watched.includes(n), style: { width: '16px', height: '16px', margin: 0, accentColor: 'var(--accent)' }, onchange: (e) => {
            watched = e.target.checked ? watched.concat([n]) : watched.filter(x => x !== n);
            lsSet('msgbus.watch', watched); refresh();
          } })),
          h('td.mono', { dataset: { label: 'Channel' } }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); show(n); } }, n))))))));
    }

    function renderWatch() {
      clear(watchHost);
      if (!watched.length) { watchHost.appendChild(h('p.muted', 'Tick channels in the list to watch them here.')); return; }
      watchHost.appendChild(h('div.table-wrap', h('table.table',
        h('thead', h('tr', h('th', 'Channel'), h('th', 'Type'), h('th', 'Value'), h('th', 'Updated'))),
        h('tbody', watched.map(n => { const v = values[n]; return h('tr',
          h('td.mono', { dataset: { label: 'Channel' } }, n), h('td', { dataset: { label: 'Type' } }, v ? v.type || '' : '…'),
          h('td', { dataset: { label: 'Value' } }, valueView(v)), h('td', { dataset: { label: 'Updated' } }, v ? v.updated || '' : '')); })))));
    }

    async function refresh() {
      if (busy || !watched.length) { renderWatch(); return; }
      busy = true;
      try {
        const d = await get('/api/expert/msgbus/values?channels=' + encodeURIComponent(watched.join(',')));
        values = Object.assign(values, d.values);
        renderWatch();
      } catch (e) {
        clear(watchHost); const b = friendlyError(e, refresh); if (b) watchHost.appendChild(b);
      } finally { busy = false; }
    }

    function schedule() {
      if (timer) clearInterval(timer);
      const s = Number(every.value);
      lsSet('msgbus.every', s);
      timer = s ? setInterval(() => { if (!document.hidden) refresh(); }, s * 1000) : null;
    }

    function show(n) {
      section(detailHost, () => get('/api/expert/msgbus/value?channel=' + encodeURIComponent(n)), (v) => card(n, [
        kv([['Type', v.type || ''], ['Updated', v.updated], ['Value', valueView(v)], v.length !== undefined && ['Length', `${v.length} bytes`],
            ['Result', v.ok ? badge('OK', 'ok') : badge(v.message || 'error', 'warn')]]),
        v.type === 'binary' && v.hex ? h('details', { style: { marginTop: '8px' } }, h('summary', 'Hex'), pre(v.hex.replace(/((?:\S\S ){16})/g, '$1\n'))) : null,
      ], { icon: 'chip', actions: button('Refresh', () => show(n), { small: true, icon: 'refresh' }) }));
    }

    const snapHost = h('div');
    const snapshot = button('Read all channels', async () => {
      clear(snapHost).appendChild(h('div.loading', h('span.spinner'), h('span', 'Reading every channel…')));
      try {
        const d = await get('/api/expert/msgbus/values?channels=all');
        clear(snapHost).appendChild(h('div.table-wrap', h('table.table',
          h('thead', h('tr', h('th', 'Channel'), h('th', 'Type'), h('th', 'Value'))),
          h('tbody', Object.entries(d.values).map(([n, v]) => h('tr', h('td.mono', { dataset: { label: 'Channel' } }, n),
            h('td', { dataset: { label: 'Type' } }, v.type || ''), h('td', { dataset: { label: 'Value' } }, valueView(v))))))),
          h('p.hint', `Read in ${d.elapsed_ms} ms`));
      } catch (e) { clear(snapHost); const b = friendlyError(e); if (b) snapHost.appendChild(b); }
    }, { small: true });

    el.append(h('div.grid-2',
      card('Channels', [filter, h('div', { style: { marginTop: '10px' } }, listHost)], { icon: 'network' }),
      h('div', { style: { display: 'flex', flexDirection: 'column', gap: '16px' } },
        card('Watched', [watchHost], { icon: 'clock', actions: [every, button('Refresh', refresh, { small: true, icon: 'refresh' })] }),
        detailHost)),
      card('All channels at once', [h('p.card-desc', 'One read of every channel (takes a few seconds on the device).'), snapHost], { actions: snapshot }));

    section(listHost, () => get('/api/expert/msgbus/channels'), (d) => {
      names = d.channels;
      watched = watched.filter(n => names.includes(n));
      renderList();
      refresh();
      return null;
    });
    filter.addEventListener('input', debounce(renderList, 120));
    every.addEventListener('change', schedule);
    schedule();
    return () => { if (timer) clearInterval(timer); };
  },
};
