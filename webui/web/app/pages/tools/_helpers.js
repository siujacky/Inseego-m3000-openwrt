// Shared helpers for the Tools pages (files starting with "_" are not pages).
import { h, clear, nextId } from '../../ui/dom.js';
import { alert, errorBox, spinner, toast, badge } from '../../ui/widgets.js';
import { icon } from '../../ui/icons.js';

// -- friendly loading / errors -------------------------------------------------------------------------------
// 503 "unavailable" (no SIM, service not ready, not supported) and 403 "expert_required" become calm notes.
export function friendlyError(err, retry) {
  const e = err || {};
  if (e.status === 503 || e.code === 'unavailable' || e.code === 'unsupported') {
    return alert('info', e.message || 'Not available on this unit right now.',
      retry ? h('div', { style: { marginTop: '6px' } }, h('button.btn.btn-sm', { type: 'button', onclick: retry }, 'Try again')) : null);
  }
  if (e.code === 'expert_required') {
    return alert('warn', 'Expert tools are turned off. Turn on expert mode in the UI settings to use this page.');
  }
  if (e.code === 'cancelled') return null;
  return errorBox(e, retry);
}

export async function section(el, loader, render) {
  clear(el).appendChild(spinner());
  try {
    const data = await loader();
    clear(el);
    const out = render(data);
    if (out) el.append(...(Array.isArray(out) ? out.filter(Boolean) : [out]));
    return data;
  } catch (e) {
    clear(el);
    const box = friendlyError(e, () => section(el, loader, render));
    if (box) el.appendChild(box);
    return undefined;
  }
}

// -- tabs (keeps ?tab= in the hash without re-rendering the page) ----------------------------------------------
export function tabs(container, items, opts = {}) {
  const head = h('div.tabs', { role: 'tablist' });
  const body = h('div');
  const rendered = {};
  let current = null;
  const buttons = {};
  function select(id, push = true) {
    if (!items.some(t => t.id === id)) id = items[0].id;
    current = id;
    for (const t of items) {
      buttons[t.id].classList.toggle('active', t.id === id);
      buttons[t.id].setAttribute('aria-selected', String(t.id === id));
      if (rendered[t.id]) rendered[t.id].hidden = t.id !== id;
    }
    if (!rendered[id]) {
      const pane = h('div', { role: 'tabpanel' });
      rendered[id] = pane;
      body.appendChild(pane);
      items.find(t => t.id === id).render(pane);
    }
    if (push && opts.hashBase) {
      try { history.replaceState(null, '', '#' + opts.hashBase + '?tab=' + encodeURIComponent(id)); } catch (_) { /* ignore */ }
    }
    if (opts.onChange) opts.onChange(id);
  }
  for (const t of items) {
    buttons[t.id] = h('button.tab', { type: 'button', role: 'tab', onclick: () => select(t.id) }, t.title);
    head.appendChild(buttons[t.id]);
  }
  container.append(head, body);
  select(opts.initial || items[0].id, false);
  return { select, current: () => current, pane: (id) => rendered[id] };
}

// -- small form controls ------------------------------------------------------------------------------------------
export function field(label, control, help) {
  const target = control && (control.matches && control.matches('input,select,textarea') ? control
    : control.querySelector && control.querySelector('input:not([type=checkbox]),select,textarea'));
  let forId;
  if (target) { if (!target.id) target.id = nextId('tf'); forId = target.id; }
  return h('div.field', h('label.field-label', { for: forId }, label), h('div.field-control', control), help && h('div.field-help', help));
}

export function setFieldError(root, key, message) {
  root.querySelectorAll('.field.has-error').forEach(f => { f.classList.remove('has-error'); const e = f.querySelector('.field-error'); if (e) e.remove(); });
  if (!key) return;
  const inp = root.querySelector(`[data-key="${key}"]`);
  const f = inp && inp.closest('.field');
  if (f) { f.classList.add('has-error'); f.appendChild(h('div.field-error', message)); inp.focus(); }
}

// -- output helpers -------------------------------------------------------------------------------------------------
export function pre(text, cls = '') {
  return h('pre.pre' + (cls ? '.' + cls : ''), text == null || text === '' ? '(no output)' : String(text));
}

export function copyText(text) {
  const done = () => toast('Copied');
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text).then(done, () => fallback());
  }
  return fallback();
  function fallback() {
    const ta = h('textarea', { style: { position: 'fixed', left: '-1000px', top: '0' } });
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (_) { toast('Copy failed', 'bad'); }
    ta.remove();
    return Promise.resolve();
  }
}

export function copyButton(getText, label = 'Copy') {
  return h('button.btn.btn-sm', { type: 'button', onclick: () => copyText(typeof getText === 'function' ? getText() : String(getText)) }, label);
}

export function downloadLink(href, label = 'Download', name) {
  return h('a.btn.btn-sm', { href, download: name || '', rel: 'noopener' }, icon('download'), h('span', label));
}

export function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
}

// -- bytes ------------------------------------------------------------------------------------------------------
export function b64ToBytes(b64) {
  const bin = atob(b64 || '');
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function hexdump(bytes, max = 4096) {
  const n = Math.min(bytes.length, max);
  const lines = [];
  for (let off = 0; off < n; off += 16) {
    const row = bytes.subarray(off, Math.min(off + 16, n));
    const hex = Array.from(row, b => b.toString(16).padStart(2, '0'));
    const left = hex.slice(0, 8).join(' '), right = hex.slice(8).join(' ');
    const ascii = Array.from(row, b => (b >= 32 && b < 127 ? String.fromCharCode(b) : '.')).join('');
    lines.push(off.toString(16).padStart(8, '0') + '  ' + (left + (right ? '  ' + right : '')).padEnd(49) + ' |' + ascii + '|');
  }
  if (bytes.length > n) lines.push(`… ${bytes.length - n} more bytes`);
  return lines.join('\n') || '(empty file)';
}

// -- misc -------------------------------------------------------------------------------------------------------
export function lsGet(key, dflt) {
  try { const v = localStorage.getItem('m3ui.tools.' + key); return v == null ? dflt : JSON.parse(v); } catch (_) { return dflt; }
}

export function lsSet(key, value) {
  try { localStorage.setItem('m3ui.tools.' + key, JSON.stringify(value)); } catch (_) { /* private mode */ }
}

export function kindBadge(kind) {
  const map = { get: ['read-only', 'ok'], setter: ['setter', 'warn'], excluded: ['blocked', 'bad'],
                ok: ['read-only', 'ok'], risky: ['needs confirmation', 'warn'], write: ['needs password', 'bad'], deny: ['blocked', 'bad'] };
  const [text, tone] = map[kind] || [kind || '?', 'neutral'];
  return badge(text, tone);
}

export function when(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

export function num(v, unit) {
  if (v === null || v === undefined || v === '') return '';
  return unit ? `${v} ${unit}` : String(v);
}
