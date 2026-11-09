// Wi-Fi > Radio & advanced: Wi-Fi on/off, which network each band carries, 802.11 modes, channels, widths,
// client limit, device-screen control, Wi-Fi while USB tethering, country, temporary radio pause, chip info.
import { h, clear } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { card, kv, badge, alert, button, toast } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import { guard, BAND_LABEL } from './_util.js';

const RADIO_KEYS = ['band_2g', 'band_5g', 'mode_2g', 'mode_5g', 'channel_2g', 'channel_5g', 'bandwidth_2g', 'bandwidth_5g'];
const STATE_OPTS = [['primary', 'Main network'], ['guest', 'Guest network'], ['off', 'Off']];

function yn(v) { return v === null || v === undefined ? h('span.muted', 'unknown') : badge(v ? 'On' : 'Off', v ? 'ok' : 'neutral'); }

export default {
  id: 'wifi-advanced', path: '/wifi/advanced', group: 'wifi', order: 30, title: 'Radio & advanced', icon: 'antenna',
  feature: 'wifi.advanced',
  render(el) {
    const top = h('div'), grid = h('div.grid-2'), info = h('div');
    el.append(top, grid, info);
    guard(top, () => get('/api/wifi/advanced'), (first) => radioForm(top, first));
    countryCard(grid);
    pauseCard(grid);
    infoCard(info);
  },
};

function radioForm(el, first) {
  let pending = first, cur = first;
  const width = (band, mode) => {
    const o = (cur['modes_' + band] || []).find(m => m.value === mode);
    return o ? o.max_width : 80;
  };
  const bwOpts = (band) => (band === '2g' ? [[20, '20 MHz'], [40, '40 MHz']] : [[20, '20 MHz'], [40, '40 MHz'], [80, '80 MHz']]);
  const bwValidate = (band) => (v, vals) => (v && v > width(band, vals['mode_' + band])
    ? `This 802.11 mode supports at most ${width(band, vals['mode_' + band])} MHz` : null);
  const chOpts = (band) => [[0, 'Automatic (recommended)'], ...(cur['channels_' + band] || []).map(c => [c, 'Channel ' + c])];
  const modeOpts = (band) => (cur['modes_' + band] || []).map(o => [o.value, o.label]);
  let handle = null;
  const restoreBtn = button('Restore previous', async () => {
    const r = await post('/api/wifi/advanced/restore', {}, { confirmMessage: 'Turn Wi-Fi off again as before?' });
    cur = r; pending = r; toast('Previous radio settings restored'); handle && handle.reload();
  }, { title: 'Undo the last radio change', disabled: !first.restore_available });

  handle = form(el, {
    title: 'Radio settings', icon: 'antenna',
    description: 'Changes here restart the Wi-Fi radio: connected devices drop for a few seconds and reconnect. '
      + 'The channel list follows the Wi-Fi country.',
    load: async () => {
      if (pending) { const d = pending; pending = null; return d; }
      cur = await get('/api/wifi/advanced'); return cur;
    },
    save: async (ch) => {
      const r = await put('/api/wifi/advanced', ch, { confirmTitle: 'Turn Wi-Fi off?',
        confirmMessage: 'All Wi-Fi networks stop. If you are connected over Wi-Fi you lose access to this page; use USB or Ethernet to turn it back on.' });
      cur = r; restoreBtn.disabled = !r.restore_available; return r;
    },
    warn: (ch, v) => {
      if (v.band_2g === 'off' && v.band_5g === 'off') return null;
      if (RADIO_KEYS.some(k => k in ch)) {
        const moved = (ch.band_2g || ch.band_5g) ? ' Devices on a band that changes network must join the other network.' : '';
        return 'Wi-Fi devices disconnect for a few seconds while the radio restarts.' + moved;
      }
      return null;
    },
    fields: [
      { type: 'section', label: 'Wi-Fi' },
      { key: 'enabled', label: 'Wi-Fi', type: 'switch', show: () => cur.enabled !== null,
        help: 'Turns every Wi-Fi network off or on. USB and Ethernet keep working. Turning it off asks for confirmation.' },
      { key: 'band_2g', label: '2.4 GHz band carries', type: 'radio', options: STATE_OPTS,
        validate: (v, vals) => (v === 'off' && vals.band_5g === 'off' ? 'Keep at least one band on, or switch Wi-Fi off instead' : null) },
      { key: 'band_5g', label: '5 GHz band carries', type: 'radio', options: STATE_OPTS },
      { type: 'section', label: '2.4 GHz', help: 'Longer range, slower. Channels 1, 6 and 11 do not overlap.' },
      { key: 'mode_2g', label: '802.11 mode', type: 'select', options: modeOpts('2g'),
        help: 'Legacy modes slow the whole band down; use them only for very old devices.' },
      { key: 'channel_2g', label: 'Channel', type: 'select', options: chOpts('2g') },
      { key: 'bandwidth_2g', label: 'Channel width', type: 'select', options: bwOpts('2g'), show: () => cur.bandwidth_2g_available,
        validate: bwValidate('2g'), help: '40 MHz is faster but interferes more with neighbours.' },
      { type: 'section', label: '5 GHz', help: 'Faster, shorter range. Radar (DFS) channels are not used.' },
      { key: 'mode_5g', label: '802.11 mode', type: 'select', options: modeOpts('5g') },
      { key: 'channel_5g', label: 'Channel', type: 'select', options: chOpts('5g') },
      { key: 'bandwidth_5g', label: 'Channel width', type: 'select', options: bwOpts('5g'), validate: bwValidate('5g'),
        show: () => cur.bandwidth_5g !== null },
      { type: 'section', label: 'Clients and control' },
      { key: 'max_clients', label: 'Maximum Wi-Fi clients', type: 'number', min: 1, max: first.max_clients_limit,
        unit: `of ${first.max_clients_limit}`, help: 'New devices are refused once this many are connected.' },
      { key: 'allow_screen_changes', label: 'Allow Wi-Fi changes on the device screen', type: 'switch',
        show: () => cur.allow_screen_changes !== null, help: 'When off, the touch screen can show but not change Wi-Fi settings.' },
      { key: 'wifi_while_tethered', label: 'Keep Wi-Fi on while USB tethering', type: 'switch',
        show: () => cur.wifi_while_tethered !== null, help: 'When off, Wi-Fi switches off while a computer uses the router over USB.' },
    ],
    actions: [restoreBtn],
  });
  if (!first.advanced_available) {
    el.insertBefore(alert('info', 'Some options (other 802.11 modes, 2.4 GHz width) need direct access to the Wi-Fi profiles, which is not available right now.'), el.firstChild);
  }
}

function countryCard(el) {
  const host = h('div');
  el.appendChild(host);
  guard(host, () => get('/api/wifi/country'), (first) => {
    const channels = first.channels || {};
    let pending = first;
    form(host, {
      title: 'Wi-Fi country', icon: 'globe',
      description: 'Sets the regulatory domain: allowed channels and transmit power. Choose the country you use the router in. '
        + 'Changing it reloads the Wi-Fi driver with different calibration data and restarts Wi-Fi.',
      load: async () => {
        if (pending) { const x = pending; pending = null; return x; }
        return get('/api/wifi/country');
      },
      save: (ch) => put('/api/wifi/country', ch, { confirmTitle: 'Change the Wi-Fi country?', confirmPhrase: 'COUNTRY',
        confirmMessage: 'Using a country setting that does not match your location may be illegal. Wi-Fi restarts and all devices disconnect.' }),
      fields: [
        { key: 'country', label: 'Country', type: 'select', options: first.options.map(o => [o.value, o.label]) },
        { key: 'channels_note', type: 'readonly', label: 'Channels allowed', full: true,
          format: (_v, vals) => {
            const x = channels[vals.country] || {};
            return `2.4 GHz: ${(x['2g'] || []).join(', ') || '—'}   ·   5 GHz: ${(x['5g'] || []).join(', ') || '—'}`;
          } },
      ],
      saveLabel: 'Change country',
    });
    return null;
  });
}

function pauseCard(el) {
  const state = h('div');
  const body = h('div', h('p.card-desc', 'Switches the Wi-Fi radio off right now without changing any saved setting, '
    + 'as the battery saver does in deep sleep. Resume, a power-state change or a restart brings it back.'), state);
  el.appendChild(card('Pause Wi-Fi radio', body, { icon: 'power' }));
  let paused = false;
  const paint = () => {
    clear(state).append(h('div.form-actions',
      button('Pause now', async () => {
        const r = await post('/api/wifi/radio', { on: false }, { confirmTitle: 'Pause the Wi-Fi radio?',
          confirmMessage: 'All Wi-Fi devices disconnect. If you use this page over Wi-Fi you will lose it until the radio resumes (USB or Ethernet still work).' });
        paused = r.radio_paused; toast('Wi-Fi radio paused'); paint();
      }, { kind: 'danger', disabled: paused }),
      button('Resume', async () => { const r = await post('/api/wifi/radio', { on: true }); paused = r.radio_paused; toast('Wi-Fi radio resumed'); paint(); },
        { disabled: !paused })),
    h('p.hint', paused ? 'Paused from this page.' : 'Radio running (as far as this page knows).'));
  };
  get('/api/wifi/advanced').then(d => { paused = !!d.radio_paused; paint(); }).catch(() => paint());
}

function infoCard(el) {
  const host = h('div');
  el.appendChild(card('Wi-Fi chip', host, { icon: 'chip' }));
  guard(host, () => get('/api/wifi/info'), (d) => {
    const c = d.caps || {}, s = d.states || {};
    host.appendChild(h('div.grid-2',
      kv([
        ['Firmware', d.firmware && h('span.mono', d.firmware)],
        ...(d.interfaces || []).map(i => [`${i.name} (${BAND_LABEL[i.band]})`, h('span', h('span.mono', i.mac || '—'),
          i.profile ? h('span.hint', ` profile ${i.profile}`) : null)]),
        ['WPS supported', c.wps === null || c.wps === undefined ? '' : (c.wps ? 'Yes' : 'No')],
        ['Max clients', c.system_max_clients || c.max_clients],
        ['Radios', c.interfaces],
        ['Countries', (c.countries || []).join(', ')],
        ['802.11 modes', (c.modes || []).length ? `${c.modes.length} supported` : ''],
      ]),
      kv([
        ['Wi-Fi service', d.daemon_running ? badge('Running', 'ok') : badge('Not running', 'bad')],
        ['Wi-Fi feature', yn(s.feature)], ['Access point', yn(s.access_point)],
        ['Client (station) mode', yn(s.station)], ['Connection manager: Wi-Fi', yn(s.manager_wifi)],
        ['Connection manager: access point', yn(s.manager_ap)], ['Saved Wi-Fi state', yn(s.settings_enabled)],
      ])));
    return null;
  });
}
