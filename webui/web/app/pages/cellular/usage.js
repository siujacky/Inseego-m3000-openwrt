// Cellular > Data usage: this session (live), the billing cycle against the plan, plan settings (size, day the
// cycle restarts, alert threshold), modem lifetime / per-call counters and per-interface traffic.
import { h, clear } from '../../ui/dom.js';
import { card, kv, stat, meter, badge, alert, toast, confirmSimple } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { dash, has, problem, table, smallButton } from './_sim_ui.js';

const KIND = { cellular: 'Mobile data', lan: 'Local network', wifi: 'Wi-Fi', ethernet: 'Ethernet', usb: 'USB', other: 'Other' };

function daysLeft(cycleEnd) {
  if (!cycleEnd) return null;
  const t = Date.parse(cycleEnd.replace(' ', 'T'));
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.ceil((t - Date.now()) / 86400000));
}

export default {
  id: 'cellular-usage', path: '/cellular/usage', group: 'cellular', order: 90, title: 'Data usage', icon: 'download',
  async render(el, ctx) {
    const expert = !!(ctx && ctx.session && ctx.session.expert_mode);
    const top = h('div'), stats = h('div.stats'), grid = h('div.grid-2'), planBox = h('div'), cycleBox = h('div');
    const ifBox = h('div'), modemBox = h('div');
    grid.append(planBox, cycleBox);
    el.append(top, card('Overview', stats, { icon: 'download', actions: smallButton('Refresh', () => load(), { icon: 'refresh' }) }),
      grid, h('div.grid-2', ifBox, modemBox));

    let data = null;
    let live = null;

    function renderStats() {
      const s = (live && live.wan) ? { duration_s: live.wan.duration_s, rx_bytes: live.wan.rx_bytes, tx_bytes: live.wan.tx_bytes }
        : (data && data.session) || {};
      const c = (data && data.cycle) || {};
      const p = (data && data.plan) || {};
      const used = c.available && c.valid ? c.total_bytes : null;
      const pct = has(c.percent) ? Math.min(100, c.percent) : null;
      const alertAt = p.alert_percent;
      const tone = pct === null ? null : (pct >= 100 ? 'poor' : (alertAt && pct >= alertAt ? 'fair' : 'good'));
      const left = daysLeft(c.cycle_end);
      clear(stats).append(
        stat('This session', fmt.bytes((s.rx_bytes || 0) + (s.tx_bytes || 0)), `↓ ${fmt.bytes(s.rx_bytes)} ↑ ${fmt.bytes(s.tx_bytes)} · ${fmt.duration(s.duration_s)}`),
        stat('This billing cycle', used === null ? dash : fmt.bytes(used),
          p.limit_bytes ? h('span', meter(pct || 0, { tone: tone === 'poor' ? 'bad' : tone === 'fair' ? 'warn' : 'ok' }), ` ${pct === null ? dash : pct + '%'} of ${p.limit_text}`)
            : (p.available ? 'No plan limit set' : 'Not available'), tone),
        stat('Days left in cycle', left === null ? dash : String(left), c.cycle_end ? `cycle ends ${c.cycle_end}` : ''),
        stat('Modem lifetime', data && data.lifetime && data.lifetime.available
          ? fmt.bytes(data.lifetime.rx_bytes + data.lifetime.tx_bytes) : dash,
        data && data.lifetime && data.lifetime.available ? `↓ ${fmt.bytes(data.lifetime.rx_bytes)} ↑ ${fmt.bytes(data.lifetime.tx_bytes)}` : ''));
    }

    function renderTop() {
      clear(top);
      if (!data.sim_present) top.appendChild(alert('info', 'No SIM card: the plan and billing-cycle counters are kept per SIM and appear once a SIM is inserted.'));
      const c = data.cycle || {}, p = data.plan || {};
      if (c.valid && has(c.percent) && p.alert_percent && c.percent >= p.alert_percent) {
        top.appendChild(alert(c.percent >= 100 ? 'bad' : 'warn', `${c.percent}% of the ${p.limit_text} plan is used this cycle.`));
      }
    }

    function renderPlan() {
      const p = data.plan || {};
      if (!p.available) { clear(planBox).appendChild(card('Plan', problem({ status: 503, message: p.reason || 'The data usage service is not available.' }), { icon: 'tools' })); return; }
      const limits = (data.limits || []).map(l => [l.key, l.label]);
      if (p.limit_text && !p.limit) limits.unshift([null, `Current: ${p.limit_text} (choose a size to change)`]);
      form(planBox, { title: 'Plan', icon: 'tools',
        description: 'The router counts mobile data per billing cycle and warns when the alert level is reached. The counter restarts on the chosen day.',
        load: async () => ({ limit: p.limit || null, cycle_day: p.cycle_day && p.cycle_day >= 1 ? p.cycle_day : 1, alert_percent: p.alert_percent || 80 }),
        save: async (ch) => { await put('/api/usage/plan', ch); await load(); return undefined; },
        readonly: !data.sim_present,
        fields: [
          { key: 'limit', label: 'Plan size', type: 'select', options: limits, help: 'Sizes the router supports; "No limit" only counts usage.' },
          { key: 'cycle_day', label: 'Cycle restarts on day', type: 'select', options: Array.from({ length: 31 }, (_, i) => [i + 1, String(i + 1)]),
            help: 'In months with fewer days the last day is used.' },
          { key: 'alert_percent', label: 'Alert at', type: 'number', unit: '%', min: 1, max: 100 },
        ] });
    }

    function renderCycle() {
      const c = data.cycle || {};
      const rows = c.available ? [
        ['Counter', c.valid ? badge('valid', 'ok') : badge('no data yet', 'neutral')],
        ['Cycle', c.cycle_start || c.cycle_end ? `${c.cycle_start || '…'} → ${c.cycle_end || '…'}` : dash],
        ['Downloaded', fmt.bytes(c.rx_bytes)],
        ['Uploaded', fmt.bytes(c.tx_bytes)],
        ['Total', fmt.bytes(c.total_bytes)],
        ['Last update', c.updated || dash],
        c.valid && c.level && ['Usage level', c.level === 'full' ? 'Below the alert level' : `Alert level ${c.level} reached`],
        expert && c.plan_type && ['Plan type', c.plan_type],
      ] : [['Counter', 'Not available']];
      const actions = data.reset_supported ? [smallButton('Reset counter', async () => {
        if (!(await confirmSimple('Reset the counter?', 'The stored usage of this cycle is deleted. This cannot be undone.', 'Reset', 'danger'))) return;
        await post('/api/usage/reset', {}); toast('Counter reset'); load();
      })] : null;
      clear(cycleBox).appendChild(card('Billing cycle', [kv(rows),
        !data.reset_supported ? h('p.hint', 'This firmware does not allow resetting the cycle counter.') : null], { icon: 'clock', actions }));
      if (data.ethernet) {
        cycleBox.appendChild(card('Ethernet WAN usage', kv([['Downloaded', fmt.bytes(data.ethernet.rx_bytes)], ['Uploaded', fmt.bytes(data.ethernet.tx_bytes)]]), { icon: 'ethernet' }));
      }
    }

    function renderInterfaces() {
      clear(ifBox).appendChild(card('Traffic by interface', [
        h('p.card-desc', 'Counters since the router started.'),
        table([
          { key: 'name', label: 'Interface', mono: true },
          { key: 'kind', label: 'Type', format: (v) => KIND[v] || v },
          { key: 'rx_bytes', label: 'Received', format: (v) => fmt.bytes(v) },
          { key: 'tx_bytes', label: 'Sent', format: (v) => fmt.bytes(v) },
          expert && { key: 'errors', label: 'Errors' },
        ].filter(Boolean), data.interfaces, { empty: 'No interface counters.' })], { icon: 'network' }));
    }

    function renderModem() {
      const call = data.call || {};
      clear(modemBox).appendChild(card('Modem counters', kv([
        ['Lifetime received', data.lifetime.available ? fmt.bytes(data.lifetime.rx_bytes) : dash],
        ['Lifetime sent', data.lifetime.available ? fmt.bytes(data.lifetime.tx_bytes) : dash],
        ['Current call IPv4', call.ipv4 ? `↓ ${fmt.bytes(call.ipv4.rx_bytes)} ↑ ${fmt.bytes(call.ipv4.tx_bytes)}` : dash],
        ['Current call IPv6', call.ipv6 ? `↓ ${fmt.bytes(call.ipv6.rx_bytes)} ↑ ${fmt.bytes(call.ipv6.tx_bytes)}` : dash],
        ['Session status', (data.session && data.session.status) || dash],
      ]), { icon: 'chip' }));
    }

    async function load() {
      try { data = await get('/api/usage'); } catch (e) { clear(top).appendChild(problem(e, load)); return; }
      renderTop(); renderStats(); renderPlan(); renderCycle(); renderInterfaces(); renderModem();
    }
    await load();

    const unsub = ctx && ctx.store ? ctx.store.subscribe((s) => { if (s.status) { live = s.status; if (data) renderStats(); } }) : null;
    const timer = setInterval(async () => {
      if (document.hidden || !data) return;
      try { const d = await get('/api/usage'); data = d; renderStats(); renderInterfaces(); } catch (_) { /* keep last */ }
    }, 60000);
    return () => { if (unsub) unsub(); clearInterval(timer); };
  },
};
