// Helpers for the SIM, APN, SMS and data-usage pages (not a page: the file name starts with "_").
import { h, clear } from '../../ui/dom.js';
import { alert, errorBox, spinner, modal, confirmSimple, toast, button } from '../../ui/widgets.js';
import { get } from '../../api.js';

export const dash = '—';
export const has = (v) => v !== undefined && v !== null && v !== '';

// 503 "unavailable/unsupported" -> a calm note; anything else -> error box with retry.
export function problem(e, retry) {
  if (e && (e.status === 503 || e.code === 'unavailable' || e.code === 'unsupported')) {
    return alert('info', e.message || 'Not available on this device.');
  }
  return errorBox(e, retry);
}

// Load into a box: spinner, then render(data) or a friendly problem.
export async function section(el, loader, render) {
  clear(el).appendChild(spinner());
  try {
    const data = await loader();
    clear(el);
    const out = render(data);
    if (out) el.append(...[].concat(out).filter(Boolean));
    return data;
  } catch (e) {
    clear(el).appendChild(problem(e, () => section(el, loader, render)));
    return undefined;
  }
}

// Read-only table that stays readable on phones (td data-label).
export function table(columns, rows, opts = {}) {
  if (!rows || !rows.length) return h('p.muted', opts.empty || 'Nothing to show.');
  return h('div.table-wrap', h('table.table',
    h('thead', h('tr', columns.map(c => h('th' + (c.actions ? '.actions' : ''), c.label)))),
    h('tbody', rows.map(r => h('tr', { onclick: opts.onRow ? () => opts.onRow(r) : undefined,
      style: opts.onRow ? { cursor: 'pointer' } : undefined },
    columns.map(c => {
      const v = c.format ? c.format(r[c.key], r) : r[c.key];
      return h('td' + (c.mono ? '.mono' : '') + (c.actions ? '.actions' : ''), { dataset: { label: c.label } },
        has(v) || v instanceof Node || Array.isArray(v) ? v : dash);
    }))))));
}

// Masked identifier with a "Show" button that fetches the full value once (audited server side).
export function secret(masked, fetchValue) {
  if (!masked) return h('span.muted', dash);
  const val = h('span.mono', masked);
  let shown = false, full = null;
  const btn = h('button.btn.btn-sm.btn-ghost', { type: 'button', onclick: async () => {
    try {
      if (full === null) full = await fetchValue();
      shown = !shown;
      val.textContent = shown ? (full || masked) : masked;
      btn.textContent = shown ? 'Hide' : 'Show';
    } catch (e) { toast(e.message || String(e), 'bad'); }
  } }, 'Show');
  return h('span', val, ' ', btn);
}

// "3 attempts left" with tone.
export function attempts(n, low = 1) {
  if (n === null || n === undefined) return h('span.badge.badge-neutral', 'unknown');
  const tone = n <= 0 ? 'bad' : n <= low ? 'warn' : 'ok';
  return h('span.badge.badge-' + tone, `${n} ${n === 1 ? 'attempt' : 'attempts'} left`);
}

// Run a request; when the server answers 409 last_attempts, explain and retry with last_attempt=true.
export async function withLastAttempt(send, body) {
  try {
    return await send(body);
  } catch (e) {
    if (e && e.code === 'last_attempts') {
      const ok = await confirmSimple('Few attempts left', e.message, 'Try anyway', 'danger');
      if (!ok) { const c = new Error('Cancelled'); c.code = 'cancelled'; throw c; }
      return send(Object.assign({}, body, { last_attempt: true }));
    }
    throw e;
  }
}

// Shows a stored secret (APN password …) in a small dialog.
export async function revealSecret(url, title = 'Saved password') {
  const r = await get(url);
  const v = r.password || '';
  modal({ title, body: v ? h('p', h('code', v)) : h('p.muted', 'No password is stored.'),
    actions: [{ label: 'Close', value: true }] });
}

export function onOff(v) { return v === null || v === undefined ? dash : (v ? 'On' : 'Off'); }
export function yesNo(v) { return v === null || v === undefined ? dash : (v ? 'Yes' : 'No'); }

// ---- SMS character accounting (GSM 03.38 / UCS-2), mirrors services/sms.py --------------------------
const GSM7 = new Set(Array.from('@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !"#¤%&\'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà'));
const GSM7_EXT = new Set(Array.from('^{}\\[~]|€\f'));
export function smsCount(text) {
  const chars = Array.from(text || '');
  const gsm = chars.every(c => GSM7.has(c) || GSM7_EXT.has(c));
  const units = gsm ? chars.reduce((n, c) => n + (GSM7_EXT.has(c) ? 2 : 1), 0)
    : chars.reduce((n, c) => n + (c.codePointAt(0) > 0xFFFF ? 2 : 1), 0);
  const single = gsm ? 160 : 70, multi = gsm ? 153 : 67;
  const parts = units <= single ? 1 : Math.ceil(units / multi);
  const per = parts === 1 ? single : multi;
  const plain = chars.every(c => c === '\n' || (c.charCodeAt(0) >= 32 && c.charCodeAt(0) < 127));
  return { encoding: gsm ? 'GSM-7' : 'UCS-2', units, parts, left: per * parts - units, plain };
}

export function smallButton(label, fn, opts = {}) {
  return button(label, fn, Object.assign({ small: true }, opts));
}

export function localTime(ts, fallback) {
  if (ts) {
    const d = new Date(ts * 1000);
    const today = new Date();
    return d.toDateString() === today.toDateString()
      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  return fallback || dash;
}
