// Shared helpers for the VPN page (not a page).
import { h } from '../../ui/dom.js';
import { badge } from '../../ui/widgets.js';
import * as fmt from '../../ui/format.js';

const STATE = {
  connected: ['Connected', 'ok'], connecting: ['Connecting', 'info'], disconnected: ['Disconnected', 'neutral'],
  not_connected: ['Not connected', 'neutral'], failed: ['Connection failed', 'bad'], stale: ['No recent handshake', 'warn'],
  unknown: ['Unknown', 'neutral'],
};

export function stateBadge(stats, configured = true) {
  if (!configured) return badge('Not set up', 'neutral');
  if (!stats) return badge('Unknown', 'neutral');
  const [label, tone] = STATE[stats.state] || STATE.unknown;
  return badge(label, tone);
}

export function statsRows(st) {
  if (!st) return [];
  return [
    ['Status', stateBadge(st), st.failed_text || ''],
    st.state === 'connected' && ['Tunnel address', h('span.mono', st.ip + (st.subnet ? ' / ' + st.subnet : ''))],
    st.state === 'connected' && ['Traffic', `↓ ${fmt.bytes(st.rx_bytes)}  ↑ ${fmt.bytes(st.tx_bytes)}`],
    st.state === 'connected' && ['Connected for', fmt.duration(st.uptime_s)],
  ];
}

// Browser File objects -> [{name, data: base64}] for POST /api/vpn/openvpn
export function readFiles(files) {
  return Promise.all(Array.from(files || []).map(f => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve({ name: f.name, data: String(r.result).replace(/^data:[^,]*,/, '') });
    r.onerror = () => reject(new Error('Could not read ' + f.name));
    r.readAsDataURL(f);
  })));
}

// WireGuard private key (Curve25519, clamped) generated in the browser.
export function randomWgKey() {
  const b = new Uint8Array(32);
  crypto.getRandomValues(b);
  b[0] &= 248; b[31] &= 127; b[31] |= 64;
  let s = '';
  for (const x of b) s += String.fromCharCode(x);
  return btoa(s);
}

export const WG_KEY = /^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=$/;
