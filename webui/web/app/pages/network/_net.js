// Helpers shared by the Network pages (files starting with "_" are not pages).
import { h, clear } from '../../ui/dom.js';
import { card, alert } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { list } from '../../ui/list.js';

const SOFT_CODES = ['unavailable', 'unsupported', 'disabled', 'vendor_unreachable', 'unverified_setter', 'excluded'];

// Errors that mean "this unit/firmware cannot do this right now" rather than a bug.
export function isUnavailable(e) {
  return !!e && (e.status === 503 || e.status === 501 || SOFT_CODES.includes(e.code));
}

export function friendly(e) {
  if (e && e.code === 'unverified_setter') {
    return 'This setting has not been verified on this router yet, so it is switched off for safety. ' +
      'It becomes available after the setting checks are run on the device.';
  }
  return (e && e.message) || String(e);
}

// A container appended to the page (each card gets its own so form()/list() can own it).
export function block(el) { const d = h('div'); el.appendChild(d); return d; }

export function noteCard(host, title, icon, text, tone = 'info') {
  clear(host).appendChild(card(title, alert(tone, text), { icon }));
}

// form() that shows a friendly note instead of an error box when the feature is unavailable.
export function safeForm(host, cfg) {
  const load = cfg.load;
  return form(host, Object.assign({}, cfg, {
    load: async () => {
      try { return await load(); } catch (e) {
        if (!isUnavailable(e)) throw e;
        setTimeout(() => noteCard(host, cfg.title, cfg.icon, friendly(e)), 0);
        return {};
      }
    },
  }));
}

// list() that shows the reason in the empty state instead of an error box.
export function safeList(host, cfg) {
  const load = cfg.load;
  const base = cfg.emptyText || 'Nothing here yet.';
  const c = Object.assign({}, cfg);
  c.load = async () => {
    try { const rows = await load(); c.emptyText = base; return rows; } catch (e) {
      if (!isUnavailable(e)) throw e;
      c.emptyText = friendly(e);
      return [];
    }
  };
  return list(host, c);
}

// A dismissible notice at the top of the page ("restart to apply" etc.).
export function banner(el) {
  const host = h('div');
  el.insertBefore(host, el.firstChild);
  const shown = new Set();
  return {
    show(text, tone = 'warn', ...extra) {
      if (shown.has(text)) return;
      shown.add(text);
      const close = h('button.btn.btn-sm', { type: 'button', onclick: () => { shown.delete(text); a.remove(); } }, 'Dismiss');
      const a = alert(tone, text, ...extra, ' ', close);
      host.appendChild(a);
    },
    restart(d, what) {
      if (d && d.restart_required) {
        this.show(`${what || 'This change'} takes effect after the router restarts. Restart it when convenient:`, 'warn',
          ' ', h('a', { href: '#/system/power' }, 'Restart & reset'));
      }
    },
  };
}

// -- small IPv4 helpers for client-side checks (the server validates again) -------------------
export function ip4(s) {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(String(s || '').trim());
  if (!m) return null;
  const p = m.slice(1).map(Number);
  if (p.some(x => x > 255)) return null;
  return ((p[0] << 24) >>> 0) + (p[1] << 16) + (p[2] << 8) + p[3];
}

export function ip4Msg(v) { return ip4(v) === null ? 'Enter an IPv4 address such as 192.168.1.1' : null; }

export function prefixOf(mask) {
  const s = String(mask || '').trim();
  const m = /^\/?(\d{1,2})$/.exec(s);
  if (m) return Number(m[1]);
  const n = ip4(s);
  if (n === null) return null;
  let bits = 0, seenZero = false;
  for (let i = 31; i >= 0; i--) {
    if ((n >>> i) & 1) { if (seenZero) return null; bits++; } else seenZero = true;
  }
  return bits;
}

export function maskMsg(v) {
  const p = prefixOf(v);
  return p === null || p < 8 || p > 30 ? 'Enter a mask between /8 and /30 (for example 255.255.255.0 or /24)' : null;
}

export function sameSubnet(a, b, prefix) {
  const x = ip4(a), y = ip4(b);
  if (x === null || y === null || prefix === null) return false;
  const m = prefix === 0 ? 0 : (~0 << (32 - prefix)) >>> 0;
  return ((x & m) >>> 0) === ((y & m) >>> 0);
}

export function macMsg(v) {
  return /^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$/.test(String(v || '').trim()) ? null : 'Enter a MAC address like 00:11:22:33:44:55';
}

export const DOMAIN_RX = /^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;

export function hostMsg(v) {
  const s = String(v || '').trim();
  if (!s) return null;
  if (ip4(s) !== null || s.includes(':')) return null;
  return DOMAIN_RX.test(s) ? null : 'Enter a host name such as pool.ntp.org or an IP address';
}

export function minutesText(min) {
  const m = Number(min) || 0;
  if (!m) return '';
  if (m % 1440 === 0) return `${m / 1440} day${m === 1440 ? '' : 's'}`;
  if (m % 60 === 0) return `${m / 60} hour${m === 60 ? '' : 's'}`;
  return `${m} minutes`;
}

export function onOff(b) { return b === null || b === undefined ? '—' : (b ? 'On' : 'Off'); }

export function newUrl(ip) {
  const port = location.port ? ':' + location.port : '';
  return `${location.protocol}//${ip}${port}/`;
}

export function expertMode(ctx) {
  const s = ctx && ctx.store && ctx.store.state && ctx.store.state.meta && ctx.store.state.meta.settings;
  return !!(s && s.expert_mode) || !!(ctx && ctx.session && ctx.session.expert_mode);
}
