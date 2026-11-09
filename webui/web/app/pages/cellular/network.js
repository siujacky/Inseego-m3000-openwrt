// Cellular > Network selection: automatic vs manual operator selection, operator scan (up to 3 min,
// polled), lock to an operator, check the manual selection, clear results.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, alert, button, toast } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { get, put, post } from '../../api.js';
import { notes, problem, table, every, yesNo, dash } from './_radio.js';

const STATUS_TONE = { current: 'ok', available: 'info', forbidden: 'bad', unknown: 'neutral' };

export default {
  id: 'cellular-network', path: '/cellular/network', group: 'cellular', order: 40, title: 'Network selection',
  icon: 'globe', feature: 'cellular.radio',
  async render(el) {
    const top = h('div'), info = h('div'), modeBox = h('div'), scanBox = h('div');
    el.append(top, h('div.grid-2', info, modeBox), scanBox);

    let data;
    async function load() { data = await get('/api/cellular/network'); return data; }
    try { await load(); } catch (e) { top.appendChild(problem(e, () => location.reload())); return; }

    function renderInfo() {
      clear(top);
      if (data.notes && data.notes.length) top.appendChild(notes(data.notes));
      const c = data.current || {}, m = data.mns;
      clear(info).appendChild(card('Current network', kv([
        ['Operator', c.operator || dash],
        ['Network code', c.mccmnc ? `${c.mccmnc.slice(0, 3)}-${c.mccmnc.slice(3)}` : dash, 'MCC-MNC'],
        ['Technology', c.tech || dash],
        ['Registration', c.service || dash],
        ['Roaming', c.roaming_state || dash],
        ['Home network', c.home_network === null || c.home_network === undefined ? dash : yesNo(c.home_network)],
        ['Selection', data.mode === 'manual' ? badge('Manual', 'warn') : badge('Automatic', 'ok')],
        ['Manual selection allowed', m ? yesNo(m.allowed) : dash, 'by the SIM / carrier profile'],
        ['Manual network locked', m ? yesNo(m.selected) : dash],
        ['Locked to', data.last_selection && m && m.selected ? `${data.last_selection.name || data.last_selection.mccmnc} (${data.last_selection.mccmnc})` : dash],
      ]), { icon: 'globe' }));
    }
    renderInfo();

    const modeForm = form(modeBox, {
      title: 'Selection mode', icon: 'tools',
      description: 'Automatic lets the modem pick the best allowed network. Manual locks it to one operator (useful when roaming).',
      load: async () => ({ mode: data.mode }),
      save: async (ch) => {
        await put('/api/cellular/network', ch, { confirmTitle: 'Return to automatic selection?',
          confirmMessage: 'The manual network lock is removed and the modem chooses the network itself. The connection may drop briefly.' });
        await load();
        renderInfo();
        return { mode: data.mode };
      },
      fields: [{ key: 'mode', label: 'Network selection', type: 'radio', options: [
        ['automatic', 'Automatic', 'Recommended'],
        ['manual', 'Manual', 'Scan below, then select a network'],
      ] }],
    });

    // ---- scan ------------------------------------------------------------------------------------
    const scanState = h('div');
    const results = h('div');
    let stopPoll = null;
    const scanBtn = button('Scan for networks', async () => {
      await post('/api/cellular/network/scan', {}, { confirmTitle: 'Scan for networks?',
        confirmMessage: 'Scanning takes up to 3 minutes. The internet connection can drop while the modem searches.' });
      startPolling();
    }, { small: true, kind: 'primary', icon: 'refresh' });
    const clearBtn = button('Clear results', async () => { await post('/api/cellular/network/clear', {}); data.scan = { state: 'idle', networks: [] }; renderScan(data.scan); },
      { small: true });
    const checkBtn = button('Check selection', async () => {
      const r = await post('/api/cellular/network/validate', {}, { confirmTitle: 'Check the manual selection?',
        confirmMessage: 'The modem checks whether the chosen network is allowed. If it is not, it returns to automatic selection.' });
      data = r;
      renderInfo();
      modeForm.reload();
      const v = r.validation;
      toast(v ? (v.reverted ? 'Not allowed: back to automatic selection' : 'Manual selection is valid') : 'Checked', v && v.reverted ? 'bad' : 'ok');
    }, { small: true, title: 'Ask the modem whether the manual selection is allowed' });
    scanBox.appendChild(card('Available networks', [
      h('p.card-desc', 'Operators found by the last scan. Select one to lock the modem to it; switch back to automatic above when done.'),
      scanState, results], { icon: 'antenna', actions: [scanBtn, checkBtn, clearBtn] }));

    function renderScan(sc) {
      clear(scanState);
      const st = sc.state;
      if (st === 'starting' || st === 'scanning') {
        scanState.appendChild(h('div.loading', h('span.spinner'), h('span', `Scanning… ${sc.elapsed_s || 0} s (up to ${sc.budget_s || 180} s)`)));
      } else if (st === 'failed' || st === 'timeout') {
        scanState.appendChild(alert('warn', `Scan ${st === 'timeout' ? 'timed out' : 'failed'}: ${sc.error || 'no result'}`));
      } else if (st === 'complete') {
        scanState.appendChild(alert('ok', `Scan complete: ${(sc.networks || []).length} network(s) found.`));
      }
      scanBtn.disabled = st === 'starting' || st === 'scanning';
      clear(results).appendChild(table([
        { key: 'name', label: 'Operator' },
        { key: 'mccmnc', label: 'Code', format: v => `${v.slice(0, 3)}-${v.slice(3)}`, mono: true },
        { key: 'tech', label: 'Technology' },
        { key: 'status', label: 'Status', format: v => badge(v, STATUS_TONE[v] || 'neutral') },
        { key: 'roaming', label: 'Roaming', format: v => v ? badge('roaming', 'warn') : 'home' },
        { key: 'act', label: '', format: (v, r) => (r.status === 'forbidden') ? h('span.muted', 'not allowed') : button('Select', async () => {
          await post('/api/cellular/network/select', { mccmnc: r.mccmnc, act: r.act, name: r.name }, { confirmTitle: `Use ${r.name}?`,
            confirmMessage: `Lock the modem to ${r.name} (${r.mccmnc}, ${r.tech}).${r.roaming ? ' This is a roaming network: extra charges may apply.' : ''} If the network refuses the router, there is no service until you return to automatic.` });
          toast(`Selected ${r.name}`);
          await load();
          renderInfo();
          modeForm.reload();
          renderScan(data.scan && data.scan.networks && data.scan.networks.length ? data.scan : { state: 'complete', networks: sc.networks });
        }, { small: true }) },
      ], sc.networks || [], { empty: st === 'scanning' || st === 'starting' ? 'Waiting for results…' : 'No scan results yet. Use “Scan for networks”.' }));
    }

    function startPolling() {
      if (stopPoll) stopPoll();
      renderScan({ state: 'scanning', elapsed_s: 0, networks: [] });
      stopPoll = every(3000, async () => {
        const sc = await get('/api/cellular/network/scan');
        renderScan(sc);
        if (!['starting', 'scanning'].includes(sc.state)) { stopPoll(); stopPoll = null; await load(); renderInfo(); }
      });
    }

    renderScan(data.scan || { state: 'idle', networks: [] });
    if (data.scan && ['starting', 'scanning'].includes(data.scan.state)) startPolling();
    return () => { if (stopPoll) stopPoll(); };
  },
};
