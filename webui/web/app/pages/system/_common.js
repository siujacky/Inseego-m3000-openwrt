// Shared helpers for the System pages: file downloads, password prompt, restart waiting screen.
import { h, clear } from '../../ui/dom.js';
import { modal, toast } from '../../ui/widgets.js';
import { post } from '../../api.js';
import * as fmt from '../../ui/format.js';

export function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

// Save a Blob as a file (object URL + temporary link).
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: filename, style: { display: 'none' } });
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 2000);
}

// POST that answers with a file.
export async function postDownload(path, body, filename, opts = {}) {
  const blob = await post(path, body, Object.assign({ blob: true }, opts));
  saveBlob(blob, filename);
  return blob;
}

// Ask for the admin password in a small dialog. Resolves the password or null.
export function askPassword({ title = 'Confirm with your password', message, okLabel = 'Continue', kind = 'primary' } = {}) {
  return new Promise((resolve) => {
    const pw = h('input', { type: 'password', autocomplete: 'current-password', placeholder: 'Admin password' });
    const ok = h('button.btn.btn-' + kind, { type: 'button', disabled: true }, okLabel);
    pw.addEventListener('input', () => { ok.disabled = !pw.value; });
    const m = modal({
      title,
      body: h('div.confirm', message && h('p', message), h('div.confirm-fields', h('label', 'Admin password', pw))),
      actions: [{ label: 'Cancel', value: null }, ok],
      onClose: (v) => resolve(v || null),
    });
    ok.addEventListener('click', () => { if (pw.value) m.close(pw.value); });
    pw.addEventListener('keydown', (e) => { if (e.key === 'Enter' && pw.value) m.close(pw.value); });
  });
}

async function ping() {
  try {
    const r = await fetch('/api/system/power/ping', { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' } });
    if (!r.ok) return null;
    return await r.json();
  } catch (_) {
    return null;
  }
}

async function sessionState() {
  try {
    const r = await fetch('/api/session', { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' } });
    if (!r.ok) return null;
    return await r.json();
  } catch (_) {
    return null;
  }
}

// Full-screen waiting screen after restart / factory reset / restore (or shut down).
// Polls the public ping endpoint and /api/session until the router is back: a new instance id, an answer after
// having been offline, or our session gone (sessions live in memory, a restart ends them). Then reloads the page.
export function waitForRestart({ instance, title = 'Restarting', message = 'The router is restarting.', expectedS = 120,
  maxS = 360, shutdown = false, tips = null } = {}) {
  const t0 = Date.now();
  let timer = null;
  let wentDown = false;
  let downCount = 0;   // consecutive failed pings (a single blip is not a restart)
  const fill = h('i.meter-fill', { style: { width: '0%' } });
  const elapsed = h('span', '0s');
  const status = h('p', { role: 'status' }, shutdown ? 'Waiting for the router to switch off…' : 'Waiting for the router to go offline…');
  const actions = h('div.form-actions');
  const box = h('div.modal', { role: 'dialog', 'aria-modal': 'true', 'aria-label': title, style: { maxWidth: '460px' } },
    h('header.modal-head', h('h3', title)),
    h('div.modal-body',
      h('p', message),
      h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px', margin: '12px 0' } },
        h('span.spinner', { style: { flex: 'none' } }), h('span.meter', { style: { width: '100%', flex: '1 1 auto' } }, fill)),
      h('p.hint', 'Elapsed: ', elapsed, shutdown ? '' : ` · usually about ${Math.round(expectedS / 60)} minutes`),
      status, tips && h('p.hint', tips), actions));
  const overlay = h('div.overlay', { style: { zIndex: 300 } }, box);
  document.body.appendChild(overlay);

  function finish(text) {
    clearTimeout(timer);
    status.textContent = text;
    fill.style.width = '100%';
    setTimeout(() => location.reload(), 1500);
  }

  function notResponding() {
    status.textContent = 'The router has not answered yet. If its screen is dark, check the power. If you changed the Wi-Fi '
      + 'or network settings, reconnect your computer to the router and open its address again.';
    clear(actions).append(
      h('button.btn', { type: 'button', onclick: () => { clear(actions); loop(); } }, 'Keep waiting'),
      h('button.btn.btn-primary', { type: 'button', onclick: () => location.reload() }, 'Reload page'));
  }

  let deadline = maxS;
  async function tick() {
    const s = (Date.now() - t0) / 1000;
    elapsed.textContent = fmt.duration(s);
    fill.style.width = Math.min(98, s / (shutdown ? 20 : expectedS) * 100) + '%';
    const r = await ping();
    const sess = shutdown ? null : await sessionState();
    if (r && sess && sess.authenticated === false && !shutdown) { finish('The router is back. Please log in again…'); return; }
    if (!r) {
      downCount += 1;
      if (!wentDown) {
        wentDown = true;
        status.textContent = shutdown ? 'The router is off. Press and hold its power button to turn it on again.'
          : 'The router is offline. Waiting for it to come back…';
      }
      if (shutdown) { fill.style.width = '100%'; return; }
    } else if (!shutdown && (r.instance !== instance || downCount >= 3)) {
      finish('The router is back. Reloading…');
      return;
    }
    if (r) downCount = 0;
    if (s > deadline) { deadline += maxS; notResponding(); return; }
    loop();
  }
  function loop() { timer = setTimeout(tick, 2500); }
  timer = setTimeout(tick, 2000);
  return { close: () => { clearTimeout(timer); overlay.remove(); } };
}

// Generate a strong password that the vendor tool accepts (no spaces / square brackets).
export function generatePassword(n = 16) {
  const sets = ['abcdefghijkmnopqrstuvwxyz', 'ABCDEFGHJKLMNPQRSTUVWXYZ', '23456789', '!#%*+-=?@^_~'];
  const all = sets.join('');
  const rnd = new Uint32Array(n);
  crypto.getRandomValues(rnd);
  const out = sets.map((s, i) => s[rnd[i] % s.length]);
  for (let i = out.length; i < n; i++) out.push(all[rnd[i] % all.length]);
  for (let i = out.length - 1; i > 0; i--) { const j = rnd[i] % (i + 1); [out[i], out[j]] = [out[j], out[i]]; }
  return out.join('');
}

// Same rules as the server (services/system.py check_new_password).
export function passwordProblem(pw, rules) {
  const min = (rules && rules.min_length) || 8, max = (rules && rules.max_length) || 32;
  if (!pw) return null;
  if (pw.length < min) return `Use at least ${min} characters`;
  if (pw.length > max) return `Use at most ${max} characters`;
  if (!/^[\x21-\x5a\x5c\x5e-\x7e]+$/.test(pw)) return 'Letters, digits and symbols only (no spaces, square brackets or accented letters)';
  const kinds = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter(rx => rx.test(pw)).length;
  if (kinds < 2) return 'Mix at least two kinds of characters';
  if (new Set(pw).size < 4) return 'Too easy to guess: use more different characters';
  return null;
}

export function strength(pw) {
  if (!pw) return '';
  const kinds = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter(rx => rx.test(pw)).length;
  const score = Math.min(4, Math.floor(pw.length / 4)) + kinds;
  return score >= 7 ? 'Strength: strong' : score >= 5 ? 'Strength: good' : 'Strength: weak';
}

export function friendly(e) {
  if (!e) return '';
  if (e.status === 503) return e.message || 'Not available on this router';
  return e.message || String(e);
}

export function notify(e) { if (e && e.code !== 'cancelled') toast(friendly(e), 'bad', 7000); }
