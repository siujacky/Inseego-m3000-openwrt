// Tools › API explorer (expert): every endpoint of this UI's API; GET endpoints can be run and their JSON shown.
import { h, clear, debounce } from '../../ui/dom.js';
import { card, kv, badge, alert, button } from '../../ui/widgets.js';
import { get, api } from '../../api.js';
import { section, pre, copyButton, saveBlob, friendlyError, lsGet, lsSet } from './_helpers.js';

const METHOD_TONE = { GET: 'ok', POST: 'info', PUT: 'warn', DELETE: 'bad', PATCH: 'warn' };

export default {
  id: 'tools-api', path: '/tools/api', group: 'tools', order: 60, title: 'API explorer', expert: true,
  render(el) {
    el.appendChild(h('div.page-head', h('div', h('h1', 'API explorer'),
      h('p', 'Every endpoint this web UI offers, with its description and safety tier. Read (GET) endpoints can be run from here; changes are only made from the pages themselves.'))));
    const filter = h('input', { type: 'search', placeholder: 'Filter by path or description', value: lsGet('api.filter', ''), 'aria-label': 'Filter endpoints' });
    const method = h('select', { 'aria-label': 'Method', style: { width: 'auto' } }, ['all', 'GET', 'POST', 'PUT', 'DELETE'].map(m => h('option', { value: m }, m === 'all' ? 'All methods' : m)));
    const listHost = h('div');
    const detail = h('div');
    let routes = [];

    function renderList() {
      const q = filter.value.trim().toLowerCase();
      lsSet('api.filter', filter.value);
      const rows = routes.filter(r => (method.value === 'all' || r.method === method.value) && (!q || r.path.toLowerCase().includes(q) || (r.summary || '').toLowerCase().includes(q)));
      clear(listHost).appendChild(h('div.table-wrap', { style: { maxHeight: '600px', overflowY: 'auto' } }, h('table.table',
        h('thead', h('tr', h('th', 'Method'), h('th', 'Path'), h('th', 'Description'))),
        h('tbody', rows.map(r => h('tr', { style: { cursor: 'pointer' }, onclick: () => show(r) },
          h('td', { dataset: { label: 'Method' } }, badge(r.method, METHOD_TONE[r.method] || 'neutral')),
          h('td.mono', { dataset: { label: 'Path' } }, r.path),
          h('td', { dataset: { label: 'Description' } }, r.summary || '', r.tier ? badge('tier ' + r.tier, 'warn') : null, !r.auth ? badge('public', 'info') : null)))))),
        h('p.hint', `${rows.length} of ${routes.length} endpoints`));
    }

    function show(r) {
      const params = (r.path.match(/\{([a-z_]+)\}/gi) || []).map(p => p.slice(1, -1));
      const inputs = {};
      const out = h('div');
      const query = h('input', { type: 'text', placeholder: 'query string, e.g. limit=50', spellcheck: 'false', autocomplete: 'off' });
      const runnable = r.method === 'GET';
      const run = runnable ? button('Run', async () => {
        let path = r.path;
        for (const p of params) {
          const v = inputs[p].value.trim();
          if (!v) { inputs[p].focus(); return; }
          path = path.replace('{' + p + '}', encodeURIComponent(v));
        }
        const qs = query.value.trim().replace(/^\?/, '');
        const url = path + (qs ? '?' + qs : '');
        clear(out).appendChild(h('div.loading', h('span.spinner'), h('span', 'GET ' + url)));
        const t0 = performance.now();
        try {
          const blob = await api('GET', url, undefined, { blob: true });
          const ms = Math.round(performance.now() - t0);
          const type = blob.type || '';
          if (type.includes('json')) {
            const text = await blob.text();
            let pretty = text;
            try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch (_) { /* keep raw */ }
            clear(out).append(h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', margin: '8px 0', flexWrap: 'wrap' } },
              badge('200', 'ok'), h('span.hint', `${ms} ms · ${blob.size} bytes`), copyButton(pretty),
              h('button.btn.btn-sm', { type: 'button', onclick: () => saveBlob(new Blob([pretty], { type: 'application/json' }), path.replace(/[^a-z0-9]+/gi, '_') + '.json') }, 'Save')),
              pre(pretty));
          } else if (type.startsWith('text/')) {
            const text = await blob.text();
            clear(out).append(h('div', { style: { margin: '8px 0' } }, badge('200', 'ok'), ' ', h('span.hint', `${type} · ${ms} ms`)), pre(text.slice(0, 200000)));
          } else {
            clear(out).append(alert('info', `Binary response (${type || 'unknown type'}, ${blob.size} bytes).`),
              h('div.form-foot', h('button.btn.btn-sm', { type: 'button', onclick: () => saveBlob(blob, path.split('/').pop() || 'download') }, 'Save file')));
          }
        } catch (e) {
          clear(out).append(h('div', { style: { margin: '8px 0' } }, badge(String(e.status || 'error'), 'bad'), ' ', h('span.hint', e.code || '')),
            friendlyError(e) || pre(e.message));
        }
      }, { kind: 'primary' }) : null;
      clear(detail).appendChild(card(r.method + ' ' + r.path, [
        h('p.card-desc', r.summary || 'No description.'),
        kv([['Login required', r.auth ? 'Yes' : 'No'], ['Safety tier', r.tier ? String(r.tier) : '0 (none)']]),
        params.length ? h('div.form-grid', { style: { marginTop: '10px' } }, params.map(p => {
          inputs[p] = h('input', { type: 'text', placeholder: p, spellcheck: 'false', autocomplete: 'off' });
          return h('div.field', h('label.field-label', p), inputs[p]);
        })) : null,
        runnable ? h('div.field', { style: { marginTop: '10px' } }, h('label.field-label', 'Query'), query) : null,
        runnable ? h('div.form-foot', run) : alert('info', 'Changes are not sent from the explorer. Use the matching page, which validates input and asks for confirmation where needed.'),
        out,
      ], { icon: 'link' }));
    }

    el.append(h('div.grid-2', card('Endpoints', [h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
      h('div', { style: { flex: '1 1 240px' } }, filter), method), h('div', { style: { marginTop: '10px' } }, listHost)], { icon: 'tools' }),
      detail));
    clear(detail).appendChild(card('Details', h('p.muted', 'Choose an endpoint.'), { icon: 'about' }));
    section(listHost, () => get('/api/meta/routes'), (d) => { routes = d; renderList(); return null; });
    filter.addEventListener('input', debounce(renderList, 150));
    method.addEventListener('change', renderList);
  },
};
