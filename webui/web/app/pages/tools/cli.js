// Tools › CLI explorer (expert): browse every vendor CLI command and run the read-only ones.
import { h, clear, debounce } from '../../ui/dom.js';
import { card, kv, badge, alert, button } from '../../ui/widgets.js';
import { get, post } from '../../api.js';
import { section, kindBadge, pre, copyButton, lsGet, lsSet, friendlyError } from './_helpers.js';

export default {
  id: 'tools-cli', path: '/tools/cli', group: 'tools', order: 30, title: 'CLI explorer', expert: true,
  render(el) {
    el.appendChild(h('div.page-head', h('div', h('h1', 'CLI explorer'),
      h('p', 'Every command of the vendor command-line tools, with its description. Read-only commands can be run here; setters and blocked commands are listed for reference only.'))));
    const pick = h('select', { 'aria-label': 'Command-line tool' });
    const filter = h('input', { type: 'search', placeholder: 'Filter commands', 'aria-label': 'Filter commands' });
    const onlyRead = h('input', { type: 'checkbox', checked: lsGet('cli.onlyRead', true) });
    const listHost = h('div');
    const detail = h('div');
    const head = h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } },
      h('div', { style: { flex: '1 1 200px' } }, pick), h('div', { style: { flex: '2 1 240px' } }, filter),
      h('label.check', onlyRead, h('span', 'Read-only only')));
    const grid = h('div.grid-2', card('Commands', [head, h('div', { style: { marginTop: '10px' } }, listHost)], { icon: 'terminal' }), detail);
    el.appendChild(grid);
    let commands = [];
    let current = lsGet('cli.last', 'modem2_cli');

    function showList() {
      const q = filter.value.trim().toLowerCase();
      const rows = commands.filter(c => (!onlyRead.checked || c.kind === 'get') && (!q || c.name.toLowerCase().includes(q) || (c.help || '').toLowerCase().includes(q)));
      clear(listHost);
      if (!rows.length) { listHost.appendChild(h('p.muted', 'No matching commands.')); return; }
      listHost.appendChild(h('div.table-wrap', { style: { maxHeight: '560px', overflowY: 'auto' } }, h('table.table',
        h('thead', h('tr', h('th', 'Command'), h('th', 'Description'), h('th', ''))),
        h('tbody', rows.map(c => h('tr', { style: { cursor: 'pointer' }, onclick: () => showDetail(c) },
          h('td.mono', { dataset: { label: 'Command' } }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); e.stopPropagation(); showDetail(c); } }, c.name)),
          h('td', { dataset: { label: 'Description' } }, c.help || ''),
          h('td', { dataset: { label: 'Kind' } }, kindBadge(c.kind),
            c.captured ? h('span', { title: 'A real output of this command was captured from the reference unit' }, ' ', badge('captured', 'info')) : null)))))));
    }

    function showDetail(c) {
      const answers = (c.prompts || []).map((p) => h('input', { type: 'text', placeholder: p, 'aria-label': p, autocomplete: 'off', spellcheck: 'false' }));
      const extra = h('textarea', { rows: 2, placeholder: 'More answers, one per line (optional)', spellcheck: 'false' });
      const out = h('div');
      const runBtn = c.kind === 'get' ? button('Run', async () => {
        const list = answers.map(i => i.value).concat(extra.value ? extra.value.split('\n') : []);
        while (list.length && list[list.length - 1] === '') list.pop();
        clear(out).appendChild(h('div.loading', h('span.spinner'), h('span', 'Running…')));
        try {
          const r = await post('/api/expert/cli/run', { cli: current, command: c.name, answers: list });
          clear(out).append(
            h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', margin: '8px 0' } },
              badge(r.ok ? 'OK' : 'Failed', r.ok ? 'ok' : 'bad'), r.rc !== null && r.rc !== undefined ? h('span.hint', `rc ${r.rc}`) : null,
              r.module ? h('span.hint', r.module) : null, r.message ? h('span.hint', r.message) : null,
              r.timed_out ? badge('timed out', 'warn') : null, r.truncated ? badge('output truncated', 'warn') : null,
              h('span.hint', `${r.elapsed_ms} ms`), copyButton(r.text)),
            pre(r.text),
            r.pairs && r.pairs.length ? h('details', { open: true, style: { marginTop: '10px' } }, h('summary', `Parsed values (${r.pairs.length})`),
              h('div.table-wrap', h('table.table', h('thead', h('tr', h('th', 'Label'), h('th', 'Value'), h('th', 'Note'))),
                h('tbody', r.pairs.map(([k, v, n]) => h('tr', h('td', { dataset: { label: 'Label' } }, k), h('td.mono', { dataset: { label: 'Value' } }, v), h('td', { dataset: { label: 'Note' } }, n || ''))))))) : null);
        } catch (e) {
          clear(out);
          const b = friendlyError(e);
          if (b) out.appendChild(b);
        }
      }, { kind: 'primary', icon: 'terminal' }) : null;
      clear(detail).appendChild(card(c.name, [
        h('p.card-desc', c.help || ''),
        kv([['Tool', h('span.mono', current)], ['Kind', kindBadge(c.kind)], c.section && ['Section', c.section],
            c.danger && ['Risk (recon)', c.danger], c.argv && ['Argument form', h('span.mono', c.argv)],
            c.spec && ['Setter spec', c.spec_source]]),
        c.kind === 'setter' ? alert('info', 'This command changes settings. It is not run from the explorer; use the page for that feature.') : null,
        c.kind === 'excluded' ? alert('warn', 'Blocked by the safety policy (it can brick or lock the device, fakes modem events, or talks to carrier back-ends).') : null,
        c.kind === 'get' && answers.length ? h('div', { style: { marginTop: '10px' } }, h('div.field-label', 'Answers to the prompts'),
          h('div', { style: { display: 'grid', gap: '6px', marginTop: '6px' } }, answers)) : null,
        c.kind === 'get' ? h('details', { style: { marginTop: '8px' } }, h('summary', 'Extra answers'), extra) : null,
        runBtn ? h('div.form-foot', runBtn) : null, out,
      ], { icon: 'terminal' }));
      if (window.matchMedia('(max-width: 900px)').matches) detail.scrollIntoView({ behavior: 'smooth' });
    }

    async function loadCli(name) {
      current = name;
      lsSet('cli.last', name);
      clear(detail).appendChild(card('Details', h('p.muted', 'Choose a command to see its details.'), { icon: 'about' }));
      await section(listHost, () => get('/api/expert/cli/' + encodeURIComponent(name)), (d) => { commands = d.commands; showList(); return null; });
    }

    section(listHost, () => get('/api/expert/cli'), (d) => {
      clear(pick);
      for (const c of d.clis) pick.appendChild(h('option', { value: c.cli, selected: c.cli === current }, `${c.cli} (${c.runnable}/${c.commands} read-only)`));
      if (!d.clis.some(c => c.cli === current)) current = d.clis.length ? d.clis[0].cli : '';
      pick.value = current;
      loadCli(current);
      return null;
    });
    pick.addEventListener('change', () => loadCli(pick.value));
    filter.addEventListener('input', debounce(showList, 150));
    onlyRead.addEventListener('change', () => { lsSet('cli.onlyRead', onlyRead.checked); showList(); });
  },
};
