// Tools › AT console (expert): send AT commands to the modem with the safety policy applied server-side.
import { h, clear, debounce } from '../../ui/dom.js';
import { card, badge, alert, button, toast } from '../../ui/widgets.js';
import { get, post } from '../../api.js';
import { section, kindBadge, copyText, lsGet, lsSet } from './_helpers.js';

const HISTORY_MAX = 60;

export default {
  id: 'tools-at', path: '/tools/at', group: 'tools', order: 20, title: 'AT console', expert: true,
  render(el) {
    el.appendChild(h('div.page-head', h('div', h('h1', 'AT console'),
      h('p', 'Talk to the modem directly. Queries run freely; commands that change radio or network state ask for confirmation, other changes need your admin password, and commands that could disable the modem are blocked.'))));
    const info = h('div');
    const transcript = h('div.pre.console', { role: 'log', 'aria-live': 'polite', style: { minHeight: '260px', maxHeight: '520px' } });
    const input = h('input', { type: 'text', placeholder: 'AT command, e.g. AT+CSQ', autocomplete: 'off', spellcheck: 'false',
      'aria-label': 'AT command', style: { fontFamily: 'var(--mono)' } });
    const timeout = h('select', { 'aria-label': 'Timeout', style: { width: 'auto' } },
      [5, 10, 20, 30, 60].map(s => h('option', { value: String(s), selected: s === 10 }, `${s} s`)));
    const kindHost = h('span', { style: { minWidth: '120px' } });
    let history = lsGet('at.history', []);
    let hpos = history.length;

    function line(cls, text) {
      const d = h('div', { style: { whiteSpace: 'pre-wrap', color: cls === 'cmd' ? 'var(--accent)' : cls === 'bad' ? 'var(--bad)' : cls === 'muted' ? 'var(--text-3)' : '' } }, text);
      transcript.appendChild(d);
      transcript.scrollTop = transcript.scrollHeight;
    }

    const classify = debounce(async () => {
      const v = input.value.trim();
      clear(kindHost);
      if (!/^at/i.test(v)) return;
      try {
        const c = await post('/api/expert/at/classify', { command: v }, { noConfirm: true });
        kindHost.appendChild(kindBadge(c.classification));
        kindHost.title = c.reason || '';
      } catch (_) { /* invalid while typing */ }
    }, 350);

    async function send(cmdText) {
      const cmd = (cmdText || input.value).trim();
      if (!cmd) return;
      if (!/^at/i.test(cmd)) { toast('AT commands start with AT', 'bad'); return; }
      if (history[history.length - 1] !== cmd) { history.push(cmd); history = history.slice(-HISTORY_MAX); lsSet('at.history', history); }
      hpos = history.length;
      line('cmd', '> ' + cmd);
      const t0 = Date.now();
      try {
        const r = await post('/api/expert/at', { command: cmd, timeout: Number(timeout.value) }, { confirmTitle: 'Send AT command' });
        if (r.response) line(r.ok ? '' : 'bad', r.response);
        if (r.error) line('bad', r.error);
        if (r.hint) line('muted', r.hint);
        line('muted', `(${r.transport}, ${r.elapsed_ms != null ? r.elapsed_ms : Date.now() - t0} ms${r.final ? ', ' + r.final : ''})`);
      } catch (e) {
        if (e.code === 'cancelled') { line('muted', '(not sent)'); return; }
        line('bad', e.code === 'at_denied' ? 'Blocked: ' + e.message : (e.message || String(e)));
      }
      if (!cmdText) { input.value = ''; clear(kindHost); }
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); send(); }
      else if (e.key === 'ArrowUp') { if (hpos > 0) { hpos--; input.value = history[hpos] || ''; classify(); } e.preventDefault(); }
      else if (e.key === 'ArrowDown') { if (hpos < history.length) { hpos++; input.value = history[hpos] || ''; classify(); } e.preventDefault(); }
    });
    input.addEventListener('input', classify);

    const controls = h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', marginTop: '10px' } },
      h('div', { style: { flex: '1 1 260px' } }, input), timeout, kindHost,
      button('Send', () => send(), { kind: 'primary', icon: 'terminal' }),
      h('button.btn', { type: 'button', onclick: () => clear(transcript) }, 'Clear'),
      h('button.btn', { type: 'button', onclick: () => copyText(transcript.textContent || '') }, 'Copy'),
      h('button.btn', { type: 'button', title: 'Forget the command history stored in this browser', onclick: () => { history = []; hpos = 0; lsSet('at.history', []); toast('History cleared'); } }, 'Forget history'));
    const quick = h('div.chips', { style: { marginTop: '12px' } });
    el.append(info,
      card('Console', [transcript, controls, h('p.hint', 'Enter sends, ↑/↓ recall earlier commands (kept in this browser only). Responses are not stored.')], { icon: 'terminal' }),
      card('Quick queries', [h('p.card-desc', 'Read-only commands; they never change anything.'), quick], { icon: 'chip' }));

    section(info, () => get('/api/expert/at'), (d) => {
      clear(quick);
      for (const q of d.quick) quick.appendChild(h('button.chip', { type: 'button', title: q.command, onclick: () => send(q.command) }, q.label));
      const pol = d.policy || {};
      return [
        d.transport === 'smd' ? alert('info', 'Commands go to the modem through its AT channel (' + d.transport_note + ').')
          : alert('warn', 'Commands go through the modem service (' + d.transport_note + '). On this firmware that path answers "general error"; the console then shows why.'),
        h('details', { style: { margin: '8px 0 4px' } }, h('summary', 'What is blocked or needs confirmation'),
          h('div.grid-3', { style: { marginTop: '8px' } },
            card('Blocked', h('ul', { style: { margin: 0, paddingLeft: '18px' } }, (pol.deny || []).map(([p, why]) => h('li', h('code', p), ' — ', why)))),
            card('Confirm (radio / network)', h('ul', { style: { margin: 0, paddingLeft: '18px' } }, (pol.risky || []).map(([p, why]) => h('li', h('code', p), ' — ', why)))),
            card('Password (other changes)', [h('p.hint', 'Any command that is not a query, plus:'), h('ul', { style: { margin: 0, paddingLeft: '18px' } }, (pol.sensitive || []).map(([p, why]) => h('li', h('code', 'AT' + p), ' — ', why)))]))),
        h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, h('span.hint', 'Legend:'),
          badge('read-only', 'ok'), badge('needs confirmation', 'warn'), badge('needs password', 'bad'), badge('blocked', 'bad')),
      ];
    });
    setTimeout(() => input.focus(), 50);
  },
};
