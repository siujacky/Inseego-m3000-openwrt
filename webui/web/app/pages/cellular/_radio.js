// Helpers shared by the cellular pages (signal, mode, bands, network, antenna). Not a page (leading "_").
import { h, clear } from '../../ui/dom.js';
import { alert, card, spinner, errorBox } from '../../ui/widgets.js';
import * as fmt from '../../ui/format.js';

export const dash = '—';

export function has(v) { return v !== undefined && v !== null && v !== ''; }

// number with unit, '—' when missing
export function num(v, unit = '', digits) {
  if (!has(v) || Number.isNaN(Number(v))) return dash;
  const n = Number(v);
  const s = digits !== undefined ? n.toFixed(digits) : String(n);
  return unit ? `${s} ${unit}` : s;
}

// coloured value by signal quality (good / fair / poor)
export function q(kind, v, unit) {
  if (!has(v)) return h('span.muted', dash);
  return h('span.q-' + fmt.quality(kind, v), num(v, unit));
}

export function yesNo(v) { return v === null || v === undefined ? dash : (v ? 'Yes' : 'No'); }
export function onOff(v) { return v === null || v === undefined ? dash : (v ? 'On' : 'Off'); }

export function mhz(v) { return has(v) ? `${v} MHz` : dash; }

export function kbps(v) {
  if (!has(v)) return dash;
  return fmt.rate(Number(v) * 1000);
}

export function bandList(bands, prefix = 'B') {
  if (!bands || !bands.length) return dash;
  return bands.map(b => prefix + b).join(', ');
}

// Simple read-only table that also works on phones (td data-label).
export function table(columns, rows, opts = {}) {
  if (!rows || !rows.length) return h('p.muted', opts.empty || 'Nothing to show.');
  return h('div.table-wrap', h('table.table',
    h('thead', h('tr', columns.map(c => h('th', c.label)))),
    h('tbody', rows.map(r => h('tr', columns.map(c => {
      const v = c.format ? c.format(r[c.key], r) : r[c.key];
      return h('td' + (c.mono ? '.mono' : ''), { dataset: { label: c.label } }, has(v) || v instanceof Node ? v : dash);
    }))))));
}

// Friendly handling of "not available on this unit" (HTTP 503) vs real errors.
export function problem(e, retry) {
  if (e && (e.status === 503 || e.code === 'unavailable' || e.code === 'unsupported')) {
    return alert('info', e.message || 'Not available on this device.');
  }
  return errorBox(e, retry);
}

// Load once, render into el; 503 shows a friendly note.
export async function section(el, loader, render) {
  clear(el).appendChild(spinner());
  try {
    const data = await loader();
    clear(el);
    const out = render(data);
    if (out) el.append(...(Array.isArray(out) ? out : [out]).filter(Boolean));
    return data;
  } catch (e) {
    clear(el).appendChild(problem(e, () => section(el, loader, render)));
    return undefined;
  }
}

export function notes(list) {
  if (!list || !list.length) return null;
  return h('div', list.map(n => alert('info', n)));
}

// "old → new" lines for the tier-2 confirmation dialog
export function change(label, from, to) {
  return `${label}: ${from} → ${to}`;
}

export function joinLines(lines) { return lines.filter(Boolean).join('\n'); }

// Interval that pauses while the browser tab is hidden; returns stop().
export function every(ms, fn) {
  let t = null;
  let stopped = false;
  const run = async () => {
    if (stopped) return;
    if (!document.hidden) { try { await fn(); } catch (e) { console.error(e); } }
    if (!stopped) t = setTimeout(run, ms);
  };
  t = setTimeout(run, ms);
  return () => { stopped = true; clearTimeout(t); };
}

export function infoCard(title, body, opts) { return card(title, body, opts); }

export function ago(ts) {
  if (!ts) return '';
  return fmt.ago(ts);
}

export function isExpert(ctx) {
  return !!(ctx && ((ctx.session && ctx.session.expert_mode) ||
    (ctx.store && ctx.store.state && ctx.store.state.meta && ctx.store.state.meta.settings && ctx.store.state.meta.settings.expert_mode)));
}
