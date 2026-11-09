// Shared app state: /api/status polling (paused while the tab is hidden), signal/throughput history,
// meta (features, settings) and simple pub/sub.
import { get } from './api.js';
import { metricNumber } from './ui/format.js';

const subs = new Set();
const HISTORY = 180; // samples (~12 min at 4 s)
export const state = {
  status: null, meta: null, error: null, lastOk: 0,
  history: { t: [], rsrp: [], sinr: [], rsrq: [], nr_rsrp: [], nr_snr: [], rx_bps: [], tx_bps: [] },
};
let timer = null;
let interval = 4000;
let prev = null;
let running = false, generation = 0;

export function subscribe(fn) { subs.add(fn); if (state.status) fn(state); return () => subs.delete(fn); }
function emit() { subs.forEach(fn => { try { fn(state); } catch (e) { console.error(e); } }); }

function push(key, v, allowZero = false) {
  const a = state.history[key];
  const n = typeof v === 'number' || typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN;
  a.push(!Number.isFinite(n) || n === 0 && !allowZero ? null : n);
  if (a.length > HISTORY) a.shift();
}

async function poll() {
  if (!running) return;
  const my = generation;
  timer = null;
  if (document.hidden) { schedule(); return; }
  try {
    const s = await get('/api/status');
    if (!running || my !== generation) return;
    const now = Date.now() / 1000;
    state.lastRx = null; state.lastTx = null;
    if (prev && s.wan && prev.wan) {
      const dt = Math.max(1, now - prev.t);
      for (const [key, field] of [['lastRx', 'rx_bytes'], ['lastTx', 'tx_bytes']]) {
        const current = metricNumber(s.wan[field]), previous = metricNumber(prev.wan[field]);
        if (current !== null && previous !== null && current >= previous) state[key] = (current - previous) * 8 / dt;
      }
    }
    prev = { t: now, wan: s.wan };
    state.status = s; state.error = null; state.lastOk = now;
    const h = state.history;
    h.t.push(now); if (h.t.length > HISTORY) h.t.shift();
    // Poll time is not measurement time: unknown-age cached reads leave gaps.
    const signal = s.signal && s.signal.sample_time_known !== false ? s.signal : null;
    const nr = s.nr && s.nr.sample_time_known !== false ? s.nr : null;
    push('rsrp', signal && signal.rsrp); push('sinr', signal && signal.sinr, true); push('rsrq', signal && signal.rsrq);
    push('nr_rsrp', nr && nr.rsrp); push('nr_snr', nr && nr.snr, true);
    h.rx_bps.push(state.lastRx ?? null); h.tx_bps.push(state.lastTx ?? null);
    if (h.rx_bps.length > HISTORY) { h.rx_bps.shift(); h.tx_bps.shift(); }
  } catch (e) {
    if (!running || my !== generation) return;
    state.error = e;
    state.lastRx = null; state.lastTx = null; prev = null;
  }
  emit();
  schedule();
}

function schedule() {
  if (!running) return;
  if (timer) clearTimeout(timer);
  timer = setTimeout(poll, interval);
}

let visHooked = false;
export function startPolling(seconds) {
  running = true;
  interval = Math.max(2, seconds || 4) * 1000;
  if (!timer) poll();
  if (!visHooked) {
    visHooked = true;
    document.addEventListener('visibilitychange', () => { if (running && !document.hidden) { if (timer) clearTimeout(timer); poll(); } });
  }
}

export function stopPolling() {
  running = false; generation++; if (timer) clearTimeout(timer); timer = null; prev = null;
  state.status = null; state.error = null; state.lastRx = null; state.lastTx = null;
  Object.values(state.history).forEach(a => a.length = 0);
}

export function refreshNow() { if (timer) clearTimeout(timer); return poll(); }

export async function loadMeta() {
  state.meta = await get('/api/meta/full');
  return state.meta;
}

export function feature(name) {
  if (!name) return true;
  const f = state.meta && state.meta.features;
  return !f || f[name] !== false;
}
