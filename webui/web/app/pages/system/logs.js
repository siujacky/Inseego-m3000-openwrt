// System › Logs: system journal, vendor event logs (mobile network / device), Internet sessions, kernel,
// changes made in this interface; download; vendor event-log settings; support bundle.
import { h, clear, debounce } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { card, badge, alert, button, toast, spinner, errorBox, empty, confirmSimple } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { askPassword, postDownload, stamp, notify } from './_common.js';

const LINES = [[200, '200 lines'], [500, '500 lines'], [2000, '2000 lines']];

function viewer(host) {
  let sources = [];
  let cur = null;
  let timer = null;
  const st = { lines: 500, q: '', auto: false };
  try { cur = sessionStorage.getItem('m3ui.logs.tab'); } catch (_) { /* storage blocked */ }
  const tabs = h('div.tabs', { role: 'tablist' });
  const desc = h('p.card-desc');
  const out = h('div');
  const filter = h('input', { type: 'search', placeholder: 'Filter (text to find)', 'aria-label': 'Filter', maxlength: 100,
    style: { maxWidth: '260px' }, oninput: debounce((e) => { st.q = e.target.value.trim(); load(); }, 350) });
  const lines = h('select', { 'aria-label': 'Number of lines', style: { width: 'auto' }, onchange: (e) => { st.lines = LINES[e.target.selectedIndex][0]; load(); } },
    LINES.map(([v, l]) => h('option', { selected: v === st.lines }, l)));
  const auto = h('label.check', { style: { alignItems: 'center' } }, h('input', { type: 'checkbox', onchange: (e) => { st.auto = e.target.checked; schedule(); } }),
    h('span', 'Auto-refresh'));
  const dl = h('a.btn.btn-sm', { href: '#', download: '' }, 'Download');
  const clearBtn = button('Clear', async () => {
    if (!(await confirmSimple('Clear this log', 'Hide the current entries in this interface? Nothing is deleted on the router; '
      + 'the original vendor interface keeps its own view.', 'Clear', 'primary'))) return;
    draw(await post(`/api/system/logs/${cur}/clear`, {}));
    toast('Cleared');
  }, { small: true });
  const unclearBtn = button('Show hidden', async () => { draw(await post(`/api/system/logs/${cur}/unclear`, {})); }, { small: true, kind: 'ghost' });
  const toolbar = h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '10px' } },
    filter, lines, button('Refresh', () => load(), { small: true, icon: 'refresh' }), auto, dl, clearBtn, unclearBtn);
  const meta = h('p.hint');
  host.appendChild(card('Logs', [tabs, desc, toolbar, out, meta], { icon: 'terminal' }));

  function src() { return sources.find(s => s.id === cur) || sources[0]; }

  function drawTabs() {
    clear(tabs).append(...sources.map(s => h('button.tab' + (s.id === cur ? '.active' : ''), {
      type: 'button', role: 'tab', 'aria-selected': String(s.id === cur),
      onclick: () => { cur = s.id; try { sessionStorage.setItem('m3ui.logs.tab', cur); } catch (_) { /* ignore */ } drawTabs(); load(true); },
    }, s.title)));
    const s = src();
    desc.textContent = s ? s.description : '';
    clearBtn.style.display = s && s.clearable ? '' : 'none';
    unclearBtn.style.display = s && s.clearable ? '' : 'none';
  }

  function sessionsTable(rows) {
    if (!rows.length) return empty('No Internet sessions recorded yet.');
    const td = (label, v, cls) => h('td' + (cls ? '.' + cls : ''), { dataset: { label } }, v === null || v === undefined || v === '' ? '—' : v);
    return h('div.table-wrap', h('table.table',
      h('thead', h('tr', ['Started', 'Duration', 'Via', 'Roaming', 'IPv4', 'IPv6', 'Downloaded', 'Uploaded'].map(x => h('th', x)))),
      h('tbody', rows.map(r => h('tr', td('Started', r.start), td('Duration', r.duration_s !== null && r.duration_s !== undefined ? fmt.duration(r.duration_s) : ''),
        td('Via', r.interface), td('Roaming', r.roaming ? badge(r.roaming, r.roaming === 'home' ? 'neutral' : 'warn') : ''),
        td('IPv4', r.ipv4, 'mono'), td('IPv6', r.ipv6, 'mono'), td('Downloaded', fmt.bytes(r.rx_bytes)), td('Uploaded', fmt.bytes(r.tx_bytes)))))));
  }

  function draw(d) {
    clear(out);
    if (d.rows) {
      out.appendChild(sessionsTable(d.rows));
      meta.textContent = `${d.count} session(s). Saved by the vendor software every few minutes.`;
    } else {
      const ents = d.entries || [];
      if (!ents.length) out.appendChild(empty(st.q ? 'No entries match the filter.' : (d.cleared_at ? 'No new entries since the log was cleared.' : 'No entries.')));
      else {
        const pre = h('pre.pre', { style: { maxHeight: '560px' } }, ents.map(e => (e.time ? e.time + '  ' : '') + e.text).join('\n'));
        out.appendChild(pre);
        requestAnimationFrame(() => { pre.scrollTop = pre.scrollHeight; });
      }
      meta.textContent = `${d.count} of ${d.total} entr${d.total === 1 ? 'y' : 'ies'}` + (d.truncated ? ' (older entries not shown)' : '')
        + (d.cleared_at ? ` · cleared ${fmt.dateTime(d.cleared_at)}` : '') + ` · updated ${new Date(d.generated * 1000).toLocaleTimeString()}`;
    }
    const qs = new URLSearchParams({ lines: String(st.lines), q: st.q });
    dl.href = `/api/system/logs/${cur}/download?${qs}`;
    dl.setAttribute('download', `${cur}-${stamp()}.${cur === 'sessions' ? 'csv' : 'txt'}`);
  }

  async function load(spin) {
    if (!cur) return;
    if (spin) clear(out).appendChild(spinner());
    try {
      const qs = new URLSearchParams({ lines: String(st.lines), q: st.q });
      draw(await get(`/api/system/logs/${cur}?${qs}`));
    } catch (e) {
      clear(out).appendChild(e.status === 503 ? alert('info', e.message) : errorBox(e, () => load(true)));
      meta.textContent = '';
    }
    schedule();
  }

  function schedule() {
    clearTimeout(timer);
    if (st.auto) timer = setTimeout(() => { if (!document.hidden) load(); else schedule(); }, 10000);
  }

  (async () => {
    try {
      sources = (await get('/api/system/logs')).sources;
    } catch (e) { clear(out).appendChild(errorBox(e)); return; }
    if (!sources.some(s => s.id === cur)) cur = sources[0].id;
    drawTabs();
    load(true);
  })();
  return () => clearTimeout(timer);
}

function settingsCard(host) {
  return form(host, {
    title: 'Vendor event log', icon: 'system',
    description: 'Settings of the event log kept by the vendor software (the "Mobile network events" and "Device events" tabs).',
    load: async () => {
      const d = await get('/api/system/log-settings');
      if (!d.event_log) throw Object.assign(new Error('The vendor log settings are not available on this router'), { status: 503 });
      return { event_log_enabled: d.event_log.enabled, event_log_days: d.event_log.life_days };
    },
    save: async (ch, vals) => {
      const d = await put('/api/system/log-settings', { event_log_enabled: vals.event_log_enabled, event_log_days: vals.event_log_days });
      return { event_log_enabled: d.event_log.enabled, event_log_days: d.event_log.life_days };
    },
    fields: [
      { key: 'event_log_enabled', label: 'Event log in the original interface', type: 'switch', onLabel: 'Shown', offLabel: 'Hidden',
        help: 'Shows or hides the log viewer of the original vendor interface.' },
      { key: 'event_log_days', label: 'Delete system events after', type: 'select', options: [[1, '1 day'], [2, '2 days'], [3, '3 days'], [4, '4 days']],
        help: 'When the log is full the oldest entries are removed earlier.' },
    ],
  });
}

function bundleCard(host) {
  host.appendChild(card('Support bundle', [
    h('p.card-desc', 'Collects the vendor\'s complete diagnostic archive (logs, configuration, modem and network state) into one '
      + 'file. Creating it takes one to three minutes.'),
    alert('warn', 'The archive is not encrypted and contains passwords, identifiers (IMEI, SIM numbers) and settings. Share it only with people you trust.'),
    h('div.form-actions', { style: { marginTop: '12px' } }, button('Create and download', async () => {
      const pw = await askPassword({ title: 'Create support bundle', message: 'Confirm with the admin password. Keep this page open until the download starts.', okLabel: 'Create' });
      if (!pw) return;
      toast('Collecting diagnostics… this can take a few minutes', 'info', 8000);
      try { await postDownload('/api/system/support-bundle', { password: pw }, `support-bundle-${stamp()}.tgz`); toast('Support bundle downloaded'); }
      catch (e) { notify(e); }
    }, { icon: 'download' })),
  ], { icon: 'download' }));
}

export default {
  id: 'system-logs', path: '/system/logs', group: 'system', order: 50, title: 'Logs', icon: 'terminal',
  async render(el) {
    const top = h('div'), grid = h('div.grid-2'), a = h('div'), b = h('div');
    grid.append(a, b);
    el.append(top, grid);
    const stop = viewer(top);
    settingsCard(a);
    bundleCard(b);
    return stop;
  },
};
