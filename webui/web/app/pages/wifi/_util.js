// Wi-Fi page helpers (not a page: files starting with "_" are skipped by the page loader).
import { h, clear } from '../../ui/dom.js';
import { card, alert, errorBox, spinner, modal, toast } from '../../ui/widgets.js';

export const BAND_LABEL = { '2g': '2.4 GHz', '5g': '5 GHz', '6g': '6 GHz', both: '2.4 GHz and 5 GHz', off: 'Off' };
export const NET_LABEL = { primary: 'Main network', guest: 'Guest network' };
export const SEC_LABEL = { open: 'Open', wep: 'WEP (legacy)', wpa: 'WPA (legacy)', wpa2: 'WPA2', wpa_wpa2: 'WPA/WPA2', wpa3: 'WPA3',
  wpa2_wpa3: 'WPA2/WPA3', owe: 'Enhanced Open', owe_transition: 'Enhanced Open transition' };

// Uniform random characters from crypto.getRandomValues (rejection sampling, no modulo bias).
function pick(alphabet, n) {
  const out = [];
  const lim = Math.floor(0x100000000 / alphabet.length) * alphabet.length;
  const buf = new Uint32Array(1);
  while (out.length < n) {
    crypto.getRandomValues(buf);
    if (buf[0] < lim) out.push(alphabet[buf[0] % alphabet.length]);
  }
  return out.join('');
}

// Strong password: 16 characters without look-alikes (no 0/O, 1/l/I), always has upper, lower and a digit.
export function strongPassword(len = 16) {
  const up = 'ABCDEFGHJKLMNPQRSTUVWXYZ', lo = 'abcdefghijkmnopqrstuvwxyz', dg = '23456789';
  for (;;) {
    const s = pick(up + lo + dg, len);
    if (/[A-Z]/.test(s) && /[a-z]/.test(s) && /[0-9]/.test(s)) return s;
  }
}

// Short password like the community firmware images use (8 hex digits). Easy to type, weaker.
export function hexPassword() { return pick('0123456789abcdef', 8); }

export function isUnavailable(e) {
  return e && (e.status === 503 || e.code === 'unavailable' || e.code === 'unsupported' || e.code === 'disabled');
}

// Load data, render it, and show a friendly note instead of an error when the feature is not available.
export async function guard(el, loader, render) {
  clear(el).appendChild(spinner());
  let data;
  try {
    data = await loader();
  } catch (e) {
    clear(el);
    if (isUnavailable(e)) {
      el.appendChild(card(null, alert('info', 'This is not available on this device right now. ', h('span.muted', e.message || ''))));
    } else {
      el.appendChild(errorBox(e, () => guard(el, loader, render)));
    }
    return null;
  }
  clear(el);
  return render(data);
}

export function showSecret(title, rows) {
  const body = h('div', rows.map(([label, value]) => h('div.field',
    h('div.field-label', label),
    h('div.input-group', h('code.mono', { style: { fontSize: '15px', padding: '6px 8px' } }, value || '(empty)'),
      value && navigator.clipboard && h('button.btn.btn-sm', { type: 'button', onclick: async () => {
        try { await navigator.clipboard.writeText(value); toast('Copied'); } catch (_) { toast('Copy failed', 'bad'); }
      } }, 'Copy')))),
    h('p.hint', 'Close this window when you are done; the password is not stored in the page.'));
  modal({ title, body, actions: [{ label: 'Close', value: true }] });
}

export function macText(mac) { return h('span.mono', mac || '—'); }
export const encMac = (mac) => encodeURIComponent(mac);
