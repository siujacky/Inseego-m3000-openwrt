// Tools › Audit log: every change made through this web UI (who, when, what — never secrets).
import { h, clear, debounce } from '../../ui/dom.js';
import { card, badge } from '../../ui/widgets.js';
import { get } from '../../api.js';
import { section, downloadLink, lsGet, lsSet, when } from './_helpers.js';

const TONE = [[/login|logout|session/, 'info'], [/reset|factory|reboot|delete|clear|remove/, 'bad'],
              [/at\.|expert|tier|unlock|pin|puk/, 'warn']];

function tone(action) {
  for (const [rx, t] of TONE) if (rx.test(action || '')) return t;
  return 'neutral';
}

function details(d) {
  if (!d) return '';
  return Object.entries(d).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ');
}

export default {
  id: 'tools-audit', path: '/tools/audit', group: 'tools', order: 70, title: 'Audit log',
  render(el) {
    el.appendChild(h('div.page-head', h('div', h('h1', 'Audit log'),
      h('p', 'Changes made through this web UI, newest first: logins, settings saved, radio changes, AT commands that change something. Passwords, PINs and other secrets are never recorded.'))));
    const q = h('input', { type: 'search', placeholder: 'Filter (action, address, details)', value: lsGet('audit.q', ''), 'aria-label': 'Filter' });
    const limit = h('select', { 'aria-label': 'Entries', style: { width: 'auto' } }, [100, 300, 1000, 2000].map(n => h('option', { value: String(n), selected: n === lsGet('audit.limit', 300) }, `Last ${n}`)));
    const auto = h('input', { type: 'checkbox', checked: lsGet('audit.auto', false) });
    const host = h('div');
    let timer = null;

    function load(quiet) {
      const url = `/api/tools/audit?limit=${limit.value}` + (q.value.trim() ? '&q=' + encodeURIComponent(q.value.trim()) : '');
      const render = (d) => (d.entries.length ? [h('div.table-wrap', h('table.table',
        h('thead', h('tr', h('th', 'Time'), h('th', 'From'), h('th', 'Action'), h('th', 'Details'))),
        h('tbody', d.entries.map(e => h('tr',
          h('td', { dataset: { label: 'Time' } }, when(e.t)),
          h('td.mono', { dataset: { label: 'From' } }, e.actor || ''),
          h('td', { dataset: { label: 'Action' } }, badge(e.action, tone(e.action))),
          h('td', { dataset: { label: 'Details' } }, details(e.details))))))), h('p.hint', `${d.count} entries`)]
        : h('div.empty', h('p', q.value.trim() ? 'No entries match the filter.' : 'Nothing recorded yet.')));
      if (quiet) return get(url).then((d) => { clear(host).append(...[].concat(render(d))); }).catch(() => {});
      return section(host, () => get(url), render);
    }

    function schedule() {
      if (timer) clearInterval(timer);
      lsSet('audit.auto', auto.checked);
      timer = auto.checked ? setInterval(() => { if (!document.hidden) load(true); }, 5000) : null;
    }

    el.appendChild(card('Recent changes', [
      h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '10px' } },
        h('div', { style: { flex: '1 1 240px' } }, q), limit, h('label.check', auto, h('span', 'Refresh every 5 s'))),
      host], { icon: 'clock', actions: [h('button.btn.btn-sm', { type: 'button', onclick: () => load() }, 'Refresh'),
        downloadLink('/api/tools/audit/export?format=csv', 'CSV', 'audit-log.csv'), downloadLink('/api/tools/audit/export?format=json', 'JSON', 'audit-log.json')] }));
    q.addEventListener('input', debounce(() => { lsSet('audit.q', q.value); load(); }, 300));
    limit.addEventListener('change', () => { lsSet('audit.limit', Number(limit.value)); load(); });
    auto.addEventListener('change', schedule);
    load();
    schedule();
    return () => { if (timer) clearInterval(timer); };
  },
};
