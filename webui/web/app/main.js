// Offline 5G Router application: lifecycle, login and shared status.
import * as api from './api.js';
import { h, clear, $, append } from './ui/dom.js';
import { icon } from './ui/icons.js';
import { signalBars, toast, modal, closeAllModals } from './ui/widgets.js';
import * as fmt from './ui/format.js';
import * as store from './store.js';
import { loadPages, pages, show, navigate, disposePage } from './router.js';
import { applyPreferences, appearanceControls, createShell } from './shell.js';
const app = $('#app');
let session = null, shell = null, routeListener = null, unsubscribeStatus = null, generation = 0;
applyPreferences();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyPreferences);
function stop() {
  generation++; store.stopPolling(); disposePage(); closeAllModals();
  if (routeListener) window.removeEventListener('hashchange', routeListener);
  routeListener = null; unsubscribeStatus?.(); unsubscribeStatus = null;
  shell?.destroy(); shell = null;
  return generation;
}
function renderLogin(meta, message = '') {
  stop(); document.title = 'Log in · 5G Router';
  const pw = h('input#login-pw', { type: 'password', autocomplete: 'current-password', required: true, placeholder: 'Admin password' });
  const reveal = h('button.btn.password-reveal', { type: 'button', 'aria-label': 'Show password', 'aria-pressed': 'false', onclick: () => {
    const visible = pw.type === 'password'; pw.type = visible ? 'text' : 'password';
    reveal.textContent = visible ? 'Hide' : 'Show'; reveal.setAttribute('aria-label', visible ? 'Hide password' : 'Show password'); reveal.setAttribute('aria-pressed', String(visible));
  } }, 'Show');
  const err = h('div.login-error', { role: 'alert' }, message);
  const btn = h('button.btn.btn-primary.btn-block', { type: 'submit' }, 'Log in');
  const f = h('form.login-card', { onsubmit: async e => {
    e.preventDefault(); if (btn.disabled) return;
    btn.disabled = true; btn.textContent = 'Checking…'; err.textContent = '';
    try {
      const s = await api.post('/api/login', { password: pw.value }, { noAuthRedirect: true });
      session = s; api.setCsrf(s.csrf); await start();
    } catch (ex) { err.textContent = ex.code === 'bad_password' ? 'Incorrect password' : ex.message; pw.select(); }
    finally { btn.disabled = false; btn.textContent = 'Log in'; }
  } }, h('div.login-brand', h('img', { src: '/app/img/icon.svg', alt: '', width: 52, height: 52 }),
    h('div', h('p.eyebrow', 'LOCAL MANAGEMENT'), h('h1', '5G Router'))),
    h('p.login-intro', 'Your network. Your settings.'),
    h('label', { for: 'login-pw' }, 'Admin password'), h('div.password-input', pw, reveal), err, btn,
    h('p.login-hint', meta?.platform === 'openwrt' ? 'Use the admin password provisioned for this RAM session.' : 'Use your router admin password.'),
    meta?.mock && h('div.demo-banner', 'Demo data · Host preview'), appearanceControls());
  clear(app).append(h('div.login-wrap', f)); app.classList.remove('boot'); pw.focus();
}
function statusBar() {
  const bar = h('div.statusbar', { 'aria-live': 'off' });
  const unsubscribe = store.subscribe((s) => {
    const st = s.status;
    clear(bar);
    if (!st) { if (s.error) bar.appendChild(h('span.sb-chip.bad', icon('warn'), 'Router not responding')); return; }
    const c = st.connection || {};
    const stateTone = { connected: 'ok', disconnected: 'warn', no_service: 'bad', no_sim: 'bad', airplane: 'warn' }[c.state] || 'neutral';
    const stateText = { connected: c.operator || 'Connected', disconnected: 'Disconnected', no_service: 'No service', no_sim: 'No SIM', airplane: 'Airplane mode', unknown: 'Unknown', unavailable: 'Unavailable' }[c.state] || c.state;
    append(bar, [
      h('span.sb-chip.tone-' + stateTone, { title: 'Mobile connection' }, signalBars(st.signal && st.signal.bars), h('b.tech', c.tech && c.tech !== 'none' ? c.tech : '—'), h('span.sb-text', stateText), c.roaming && h('span.badge.badge-warn', 'R')),
      st.battery && st.battery.percent !== undefined && st.battery.percent !== null && h('span.sb-chip', { title: 'Battery' }, icon(st.battery.charging ? 'charging' : 'battery'), `${st.battery.percent}%`),
      h('span.sb-chip', { title: st.platform === 'openwrt' ? 'DHCP leases' : 'Connected devices' }, icon('users'),
        fmt.metricNumber(st.clients && st.clients.total) === null ? 'Unavailable' : String(fmt.metricNumber(st.clients.total))),
      st.sms_unread ? h('a.sb-chip.tone-info', { href: st.platform === 'openwrt' ? '#/openwrt/sms' : '#/cellular/sms', title: 'Unread messages' }, icon('sms'), String(st.sms_unread)) : null,
      s.lastRx !== undefined && h('span.sb-chip.sb-rate', { title: 'Current throughput (down / up)' }, '↓ ' + fmt.rate(s.lastRx), ' ↑ ' + fmt.rate(s.lastTx)),
    ]);
    if (s.error) bar.appendChild(h('span.sb-chip.bad', { title: s.error.message }, icon('warn'), 'Stale'));
  });
  return { node: bar, unsubscribe };
}

async function start() {
  const my = stop(); app.classList.add('boot');
  try { await store.loadMeta(); } catch (e) { if (my === generation) toast(e.message, 'bad'); }
  if (my !== generation || !session) return;
  const settings = store.state.meta?.settings || {};
  session = { ...session, expert_mode: settings.expert_mode, router_name: settings.router_name || session.router_name };
  if (!pages.length) await loadPages();
  if (my !== generation || !session) return;
  const status = statusBar(); unsubscribeStatus = status.unsubscribe;
  shell = createShell({ session, store, statusBar: status.node, logout: async () => {
    try { await api.post('/api/logout', {}); } catch (_) { /* local session still closes */ }
    session = null; api.setCsrf(null); renderLogin(store.state.meta);
  } });
  clear(app).append(shell.root, shell.scrim); app.classList.remove('boot');
  const ctx = { api, store, meta: store.state.meta, session, refreshShell: () => location.reload() };
  const localShell = shell;
  routeListener = () => show(localShell.content, ctx, localShell.markActive).then(() => {
    if (my === generation) localShell.content.focus({ preventScroll: true });
  });
  window.addEventListener('hashchange', routeListener);
  if (!location.hash || location.hash === '#') history.replaceState(null, '', '#/');
  routeListener(); store.startPolling(settings.status_poll_s || 4);
}
api.onAuthRequired(async () => {
  if (!session) return;
  session = null; api.setCsrf(null); const my = stop();
  let meta = null;
  try { meta = await api.get('/api/meta', { noAuthRedirect: true }); } catch (_) { /* offline */ }
  if (my === generation) renderLogin(meta, 'Session ended. Log in again.');
});
(async () => {
  let meta = null;
  try {
    meta = await api.get('/api/meta', { noAuthRedirect: true });
    const s = await api.get('/api/session', { noAuthRedirect: true });
    if (s.authenticated) { session = s; api.setCsrf(s.csrf); await start(); return; }
  } catch (e) {
    clear(app).append(h('div.login-wrap', h('div.login-card', h('h1', 'Router unreachable'), h('p', e.message),
      h('button.btn.btn-primary', { type: 'button', onclick: () => location.reload() }, 'Retry'))));
    app.classList.remove('boot'); return;
  }
  renderLogin(meta);
})();
export { navigate, modal };
