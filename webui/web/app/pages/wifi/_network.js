// Shared page body for the main and guest Wi-Fi networks.
import { h, clear } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { alert, badge, button } from '../../ui/widgets.js';
import { get, put } from '../../api.js';
import { guard, strongPassword, hexPassword, showSecret, BAND_LABEL } from './_util.js';

const NO_WPS = ['open', 'owe', 'owe_transition', 'wpa3'];

function prep(d) {
  // password is write-only: the field starts empty and an empty value means "keep the saved one"
  return Object.assign({}, d, { password: '' });
}

export function renderNetwork(el, net) {
  const isGuest = net === 'guest';
  const base = `/api/wifi/${net}`;
  const what = isGuest ? 'guest network' : 'main network';
  return guard(el, () => get(base), (first) => {
    const notes = h('div');
    const host = h('div');
    el.append(notes, host);
    let current = first;
    let pending = first;
    const opt = (s) => (current.security_options || []).find(o => o.value === s) || {};
    const needsPw = (s) => !!opt(s).needs_password;

    function renderNotes(d) {
      clear(notes);
      const bands = d.active_bands || [];
      const bandsKnown = Array.isArray(d.active_bands);
      const chips = h('div.chips', { style: { marginBottom: '12px' } },
        bands.length ? bands.map(b => badge('On ' + BAND_LABEL[b], 'ok')) : badge(bandsKnown ? 'Not active on any band' : 'Active bands unavailable', bandsKnown ? 'warn' : 'neutral'),
        d.hidden && badge('Name hidden', 'neutral'), d.wps && badge('WPS on', 'info'),
        d.split_ssid && badge('Separate 5 GHz name', 'accent'));
      notes.appendChild(chips);
      if (bandsKnown && !bands.length && !isGuest) {
        notes.appendChild(alert('warn', 'The main network is not using any band right now. ',
          h('a', { href: d.platform === 'openwrt' ? '#/system/openwrt' : '#/wifi/advanced' }, d.platform === 'openwrt' ? 'Inspect native wireless controls.' : 'Choose the bands on the Radio page.')));
      }
      if (!d.advanced_available) {
        notes.appendChild(alert('info', 'Showing the basic settings only: WPA3, Enhanced Open, separate band names'
          + (isGuest ? ' and guest restrictions' : '') + ' need direct access to the Wi-Fi profiles, which is not available right now.'));
      }
      if (!isGuest && d.mac_filter_on) {
        notes.appendChild(alert('info', 'The MAC filter allow-list is on, so WPS is not available. ',
          h('a', { href: '#/wifi/macfilter' }, 'MAC filter settings')));
      }
    }
    renderNotes(first);

    let handle = null;
    const nameBtn = h('button.btn', { type: 'button', title: 'Suggest a name ending in the last 4 digits of the Wi-Fi MAC address',
      onclick: () => { if (handle && current.ssid_suggestion) handle.set('ssid', current.ssid_suggestion); } }, 'Name from MAC');
    const shortBtn = h('button.btn', { type: 'button', title: '8 hex digits, easy to type but weaker',
      onclick: () => { if (handle) handle.set('password', hexPassword()); } }, 'Short password');
    const revealBtn = button('Show saved password', async () => {
      const s = await get(base + '/secret');
      const rows = [['Password', s.password]];
      if (s.password_5g) rows.push(['5 GHz password', s.password_5g]);
      showSecret(isGuest ? 'Guest network password' : 'Main network password', rows);
    });

    const fields = [
      { type: 'section', label: 'Network' },
      isGuest && { key: 'enabled', label: 'Guest network', type: 'switch', onLabel: 'On', offLabel: 'Off',
        help: 'A separate network for visitors. It takes over the chosen band from the main network.' },
      isGuest && { key: 'band', label: 'Band', type: 'radio', show: (v) => v.enabled,
        options: [['2g', '2.4 GHz', 'Best range'], ['5g', '5 GHz', 'Faster, shorter range'], ['both', 'Both bands', 'The main network is switched off while the guest network uses both']] },
      { key: 'ssid', label: 'Network name (SSID)', type: 'text', required: true, maxLength: 32,
        validate: (v) => (v !== v.trim() ? 'The name may not start or end with a space'
          : /[^\x20-\x7e]/.test(v) ? 'Use letters, digits, spaces and common symbols' : (v === current.other_ssid ? 'The main and guest networks need different names' : null)),
        help: 'Up to 32 characters. "Name from MAC" suggests a unique name.' },
      current.advanced_available && current.split_ssid_available !== false && { key: 'split_ssid', label: 'Separate name for 5 GHz', type: 'switch',
        help: 'Lets devices choose the band themselves. Both bands keep the same password and security.' },
      current.advanced_available && current.split_ssid_available !== false && { key: 'ssid_5g', label: '5 GHz network name', type: 'text', maxLength: 32,
        required: true, show: (v) => v.split_ssid },
      { type: 'section', label: 'Security' },
      { key: 'security', label: 'Security', type: 'select',
        options: [...(current.security === null ? [[null, current.security_label]] : []),
          ...(current.security_options || []).map(o => [o.value, o.label])],
        required: current.security === null,
        help: (v) => (v.security === 'open' ? 'Anyone nearby can join and see unencrypted traffic.'
          : v.security === 'owe' ? 'Encrypted without a password. Older devices cannot join.'
            : v.security === 'owe_transition' ? 'Older devices join an open network, newer ones use encryption automatically.'
              : v.security === 'wpa3' ? 'Strongest option. Some older devices cannot join; WPS is not available.'
                : v.security === 'wpa_wpa2' ? 'Only for very old devices; WPA2/WPA3 mixed is safer.' : 'Works with almost every device.') },
      { key: 'password', label: 'Password', type: 'password', maxLength: 63, show: (v) => needsPw(v.security),
        placeholder: current.password_set ? 'Leave empty to keep the saved password' : 'Enter a password',
        generate: () => strongPassword(16),
        validate: (v, vals) => {
          if (!v) return (needsPw(vals.security) && !current.password_set) || (needsPw(vals.security) && !needsPw(current.security))
            ? 'Enter a password for this security mode' : null;
          if (v.length < 8 || v.length > 63) return 'Use 8 to 63 characters';
          if (/[^\x20-\x7e]/.test(v)) return 'Use printable ASCII characters only';
          return null;
        },
        help: '8 to 63 characters. Generate creates a random 16-character password.' },
      { type: 'section', label: 'Options' },
      { key: 'hidden', label: 'Hide network name', type: 'switch',
        help: 'Devices must type the name to join. WPS is switched off while the name is hidden.' },
      { key: 'isolation', label: 'Client isolation', type: 'switch',
        help: 'Devices on this network cannot reach each other (they can still use the internet).' },
      { key: 'wps', label: 'WPS', type: 'switch',
        disabled: (v) => v.hidden || NO_WPS.includes(v.security) || (!isGuest && current.mac_filter_on),
        help: 'Lets devices join with the WPS button or a PIN (start it on the WPS page). Needs WPA2 and a visible name.' },
      isGuest && current.isolate_lan !== null && { type: 'section', label: 'Guest restrictions' },
      isGuest && current.isolate_lan !== null && { key: 'isolate_lan', label: 'Keep guests off the main network', type: 'switch',
        help: 'Blocks traffic between the guest network and the main network, USB and Ethernet devices.' },
      isGuest && current.block_admin !== null && { key: 'block_admin', label: 'Block access to router settings', type: 'switch',
        help: 'Guests cannot open this settings page.' },
      isGuest && current.block_file_sharing !== null && { key: 'block_file_sharing', label: 'Block file sharing', type: 'switch',
        help: 'Guests cannot reach USB storage shared by the router.' },
      (current.show_ssid_on_screen !== null || current.show_password_on_screen !== null) && { type: 'section', label: 'Device screen' },
      current.show_ssid_on_screen !== null && { key: 'show_ssid_on_screen', label: 'Show name on the device screen', type: 'switch' },
      current.show_password_on_screen !== null && { key: 'show_password_on_screen', label: 'Show password on the device screen', type: 'switch' },
    ];

    handle = form(host, {
      title: isGuest ? 'Guest network' : 'Main network', icon: 'wifi',
      description: isGuest
        ? 'Settings for the visitor network. Devices on it can be kept away from your own devices and the router settings.'
        : 'Name, password and security of your Wi-Fi network. The same settings are used on 2.4 GHz and 5 GHz unless you give 5 GHz its own name.',
      load: async () => {
        if (pending) { const d = pending; pending = null; return prep(d); }
        current = await get(base); renderNotes(current); return prep(current);
      },
      save: async (ch) => {
        const body = Object.assign({}, ch);
        if (body.password === '') delete body.password;
        const r = await put(base, body);
        current = r; renderNotes(r);
        return prep(r);
      },
      warn: (ch) => {
        const msgs = [];
        if (['ssid', 'ssid_5g', 'split_ssid', 'security', 'password', 'hidden'].some(k => k in ch)) {
          msgs.push(`Devices on the ${what} will disconnect and must reconnect${('password' in ch || 'security' in ch) ? ' with the new settings' : ''}.`);
        }
        if (isGuest && ('enabled' in ch || 'band' in ch)) {
          msgs.push('Changing the guest band moves the main network off that band; its devices reconnect on the other band.');
        }
        if (msgs.length) msgs.push('If you are connected over this Wi-Fi, this page stops responding until you reconnect.');
        return msgs.length ? msgs.join(' ') : null;
      },
      fields,
      actions: [nameBtn, shortBtn, current.password_set && revealBtn].filter(Boolean),
    });
    return null;
  });
}
