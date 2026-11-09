// Reusable widgets: cards, key/value tables, badges, signal bars, meters, toasts, modals, confirmations.
import { h, clear } from './dom.js';
import { icon } from './icons.js';

export function card(title, body, opts = {}) {
  return h('section.card' + (opts.cls ? '.' + opts.cls : ''),
    (title || opts.actions) && h('header.card-head',
      title && h('h2.card-title', opts.icon && icon(opts.icon), title),
      opts.subtitle && h('span.card-sub', opts.subtitle),
      opts.actions && h('div.card-actions', opts.actions)),
    h('div.card-body', body));
}

export function kv(rows, opts = {}) {
  return h('dl.kv' + (opts.compact ? '.kv-compact' : ''),
    rows.filter(Boolean).map(([k, v, hint]) => [
      h('dt', k),
      h('dd', v === undefined || v === null || v === '' ? h('span.muted', '—') : v, hint && h('span.hint', hint)),
    ]));
}

export function badge(text, tone = 'neutral') {
  return h('span.badge.badge-' + tone, text);
}

export function signalBars(n, max = 5) {
  n = Math.max(0, Math.min(max, Number(n) || 0));
  return h('span.bars', { title: `${n}/${max}`, 'aria-label': `Signal ${n} of ${max}` },
    Array.from({ length: max }, (_, i) => h('i' + (i < n ? '.on' : ''), { style: { height: `${30 + i * 70 / (max - 1)}%` } })));
}

export function meter(percent, opts = {}) {
  const p = Math.max(0, Math.min(100, Number(percent) || 0));
  const tone = opts.tone || (p < 15 ? 'bad' : p < 35 ? 'warn' : 'ok');
  return h('span.meter', { title: `${p}%` }, h('i.meter-fill.tone-' + tone, { style: { width: p + '%' } }));
}

export function stat(label, value, sub, tone) {
  return h('div.stat' + (tone ? '.tone-' + tone : ''), h('div.stat-label', label), h('div.stat-value', value),
    sub && h('div.stat-sub', sub));
}

export function spinner(label = 'Loading…') {
  return h('div.loading', h('span.spinner'), h('span', label));
}

export function empty(text, action) {
  return h('div.empty', h('p', text), action);
}

export function errorBox(err, retry) {
  const msg = err && err.message ? err.message : String(err);
  return h('div.alert.alert-bad', { role: 'alert' }, icon('warn'), h('div', h('strong', 'Something went wrong. '), msg),
    retry && h('button.btn.btn-sm', { type: 'button', onclick: retry }, 'Retry'));
}

export function alert(tone, text, ...extra) {
  return h('div.alert.alert-' + tone, { role: tone === 'bad' ? 'alert' : 'status' },
    icon(tone === 'ok' ? 'check' : tone === 'info' ? 'about' : 'warn'), h('div', text, ...extra));
}

export function button(label, onclick, opts = {}) {
  const b = h('button.btn' + (opts.kind ? '.btn-' + opts.kind : '') + (opts.small ? '.btn-sm' : ''),
    { type: opts.type || 'button', disabled: opts.disabled, title: opts.title, onclick: async (ev) => {
      if (b.classList.contains('busy')) return;
      b.classList.add('busy'); b.disabled = true;
      try { await onclick(ev); } catch (e) { if (e && e.code !== 'cancelled') toast(e.message || String(e), 'bad'); }
      finally { b.classList.remove('busy'); b.disabled = !!opts.disabled; }
    } }, opts.icon && icon(opts.icon), h('span', label));
  return b;
}

// -- toasts ----------------------------------------------------------------------------
let toastHost;
export function toast(text, tone = 'ok', ms = 3800) {
  if (!toastHost) { toastHost = h('div.toasts', { 'aria-live': 'polite' }); document.body.appendChild(toastHost); }
  const t = h('div.toast.toast-' + tone, icon(tone === 'ok' ? 'check' : tone === 'bad' ? 'warn' : 'about'), h('span', text));
  toastHost.appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, ms);
}

// -- modal -----------------------------------------------------------------------------
const activeModals = new Set();
export function closeAllModals() { [...activeModals].forEach(close => close(null)); }
export function modal({ title, body, actions = [], wide = false, onClose }) {
  const prev = document.activeElement;
  let closed = false;
  const close = (v) => { if (closed) return; closed = true; activeModals.delete(close); overlay.remove(); document.removeEventListener('keydown', onKey); if (prev && prev.focus) prev.focus(); onClose && onClose(v); };
  const onKey = (e) => { if (e.key === 'Escape') close(null); };
  const dlg = h('div.modal' + (wide ? '.modal-wide' : ''), { role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('header.modal-head', h('h3', title), h('button.icon-btn', { type: 'button', 'aria-label': 'Close', onclick: () => close(null) }, icon('close'))),
    h('div.modal-body', body),
    actions.length ? h('footer.modal-foot', actions.map(a => a instanceof Node ? a : h('button.btn' + (a.kind ? '.btn-' + a.kind : ''),
      { type: 'button', onclick: () => a.onclick ? a.onclick(close) : close(a.value) }, a.label))) : null);
  const overlay = h('div.overlay', { onmousedown: (e) => { if (e.target === overlay) close(null); } }, dlg);
  activeModals.add(close);
  document.body.appendChild(overlay);
  document.addEventListener('keydown', onKey);
  setTimeout(() => { const f = dlg.querySelector('input,select,textarea,button.btn-primary,button.btn-danger'); (f || dlg).focus(); }, 30);
  return { close, el: dlg };
}

// Tier confirmation (design §8). tier 2: confirm button. tier 3: type phrase + admin password.
export function confirmTier({ tier = 2, title, message, phrase }) {
  return new Promise((resolve) => {
    const need = tier >= 3 ? (phrase || 'CONFIRM') : null;
    const pw = h('input', { type: 'password', autocomplete: 'current-password', placeholder: 'Admin password' });
    const ph = h('input', { type: 'text', autocomplete: 'off', placeholder: need || '' });
    const ok = h('button.btn.btn-' + (tier >= 3 ? 'danger' : 'primary'), { type: 'button' }, tier >= 3 ? 'I understand, continue' : 'Apply');
    const sync = () => { ok.disabled = tier >= 3 && (ph.value.trim() !== need || !pw.value); };
    ph.addEventListener('input', sync); pw.addEventListener('input', sync); sync();
    const body = h('div.confirm',
      String(message || 'This change affects the radio connection. Continue?').split(/\r?\n/).map(line => h('p', line)),
      tier >= 3 && h('div.confirm-fields',
        h('label', 'Type ', h('code', need), ' to confirm', ph),
        h('label', 'Admin password', pw)));
    const m = modal({ title: title || (tier >= 3 ? 'Dangerous action' : 'Confirm change'), body,
      actions: [{ label: 'Cancel', value: null }, ok], onClose: (v) => resolve(v || null) });
    ok.addEventListener('click', () => { if (!ok.disabled) m.close({ password: tier >= 3 ? pw.value : undefined }); });
    const enter = (e) => { if (e.key === 'Enter' && !ok.disabled) ok.click(); };
    pw.addEventListener('keydown', enter); ph.addEventListener('keydown', enter);
  });
}

export function confirmSimple(title, message, label = 'Continue', kind = 'primary') {
  return new Promise((resolve) => {
    modal({ title, body: h('p', message), actions: [{ label: 'Cancel', value: false }, { label, kind, value: true }],
      onClose: (v) => resolve(!!v) });
  });
}

// Async section: shows spinner, then render(data) or an error with retry.
export async function loadInto(el, loader, render) {
  clear(el).appendChild(spinner());
  try {
    const data = await loader();
    clear(el);
    const out = render(data);
    if (out) el.appendChild(out);
    return data;
  } catch (e) {
    clear(el).appendChild(errorBox(e, () => loadInto(el, loader, render)));
    return undefined;
  }
}
