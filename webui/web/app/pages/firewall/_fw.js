// Shared helpers for the firewall pages (not a page: files starting with "_" are skipped).
import { h, clear } from '../../ui/dom.js';
import { alert, spinner, button } from '../../ui/widgets.js';
import { get } from '../../api.js';

export const PROTOCOLS = [['tcp', 'TCP'], ['udp', 'UDP'], ['both', 'TCP and UDP']];
export const protoLabel = (p) => ({ tcp: 'TCP', udp: 'UDP', both: 'TCP + UDP' }[p] || p || '—');
export const portText = (a, b) => (a === undefined || a === null || a === '') ? '—' : (b && b !== a ? `${a}–${b}` : String(a));
export const onOff = (v) => h('span.badge.badge-' + (v ? 'ok' : 'neutral'), v ? 'On' : 'Off');

export function isUnavailable(e) {
  return !!e && (e.status === 503 || ['unavailable', 'unsupported', 'disabled', 'vendor_unreachable'].includes(e.code));
}

// Friendly box for 503 / vendor errors, with a retry button.
export function problemBox(e, retry) {
  const msg = (e && e.message) || String(e);
  const text = isUnavailable(e) ? 'This setting cannot be read right now. ' : 'The router reported a problem. ';
  return alert(isUnavailable(e) ? 'warn' : 'bad', text, h('span', msg), ' ', retry && button('Retry', retry, { small: true }));
}

// Load `url` once; render(box, data) on success, a friendly note otherwise.  Returns a
// `load` function for form()/list() that hands out the prefetched data the first time.
export function prefetched(data, url) {
  let first = data;
  return async () => {
    if (first) { const d = first; first = null; return d; }
    return get(url);
  };
}

export async function section(el, url, render) {
  const box = h('div.fw-section', { style: { display: 'flex', flexDirection: 'column', gap: '16px' } });
  el.appendChild(box);
  const run = async () => {
    clear(box).appendChild(spinner());
    try {
      const data = await get(url);
      clear(box);
      render(box, data);
    } catch (e) {
      clear(box).appendChild(problemBox(e, run));
    }
  };
  await run();
  return box;
}

// Notes shown above the firewall forms (IP passthrough, VPN, DMZ/port-forward conflicts).
export function notes(state, extra = []) {
  const out = [];
  if (state && state.ippt_active) {
    out.push(alert('warn', 'IP passthrough is on: the connected device gets the public address directly, so these rules do not apply.'));
  }
  for (const x of extra) if (x) out.push(x);
  return out;
}

// VPN connection note ("traffic goes through the tunnel, port forwarding/filtering do not apply").
export async function vpnNote(box) {
  try {
    const s = await get('/api/vpn/status');
    if (s && s.connected) {
      box.prepend(alert('info', 'A VPN connection is active. All traffic from your devices goes through the tunnel, so port forwarding and port filtering are not effective while it is connected.'));
    }
  } catch (_) { /* VPN not available: nothing to say */ }
}

export function lanHint(state) {
  return state && state.lan ? `An address in ${state.lan.network} (the router is ${state.lan.ip})` : 'An address in your local network';
}

export function inLan(ip, state) {
  if (!state || !state.lan || !/^\d+\.\d+\.\d+\.\d+$/.test(ip || '')) return null;
  const toInt = (s) => s.split('.').reduce((a, o) => ((a << 8) >>> 0) + (Number(o) & 255), 0) >>> 0;
  const [net, bits] = state.lan.network.split('/');
  const mask = bits === '0' ? 0 : (0xffffffff << (32 - Number(bits))) >>> 0;
  if (((toInt(ip) & mask) >>> 0) !== ((toInt(net) & mask) >>> 0)) return `Must be inside ${state.lan.network}`;
  if (ip === state.lan.ip) return "This is the router's own address";
  return null;
}
