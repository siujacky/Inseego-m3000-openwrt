// Services › GPS: engine on/off, last position (coordinates only, no map tiles are loaded), start at boot,
// location privacy, A-GPS mode, NMEA over Wi-Fi and XTRA assistance data.
import { h, clear, append } from '../../ui/dom.js';
import { card, kv, badge, alert, button, modal, toast, spinner, errorBox } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { smartForm, isUnavailable, extLink } from '../device/_helpers.js';

const MODE_LABELS = { xtra: 'Standalone with XTRA assistance data', msb: 'Network assisted, position on the router (MS-Based)',
  msa: 'Network assisted, position from the network (MS-Assisted)' };
const PRIVACY_TEXT = 'When GPS is on, the router works out where it is. The position is shown on this page, can be read by '
  + 'devices on your network when NMEA over Wi-Fi is on, and may be used by the mobile network (for example for emergency calls). '
  + 'Turn GPS off at any time to stop.';

function acceptPrivacy() {
  return new Promise((resolve) => {
    modal({ title: 'Location privacy', body: h('div', h('p', PRIVACY_TEXT), h('p.hint', 'Turning GPS on accepts this notice.')),
      actions: [{ label: 'Cancel', value: false }, { label: 'Accept and turn on', kind: 'primary', value: true }],
      onClose: (v) => resolve(!!v) });
  });
}

function statusTone(s) {
  const v = String(s || '').toLowerCase();
  return v === 'fixed' ? 'ok' : v === 'searching' ? 'warn' : 'neutral';
}

function fixView(f) {
  if (!f.available) {
    return [alert('info', f.message || 'No position yet.')];
  }
  const x = f.fix;
  const ll = `${x.latitude.toFixed(6)}, ${x.longitude.toFixed(6)}`;
  const osm = `https://www.openstreetmap.org/?mlat=${x.latitude.toFixed(6)}&mlon=${x.longitude.toFixed(6)}#map=16/${x.latitude.toFixed(5)}/${x.longitude.toFixed(5)}`;
  const copy = button('Copy', async () => {
    try { await navigator.clipboard.writeText(ll); toast('Coordinates copied'); } catch (_) { toast('Copy is not available here', 'bad'); }
  }, { small: true });
  return [
    h('div', { style: { marginBottom: '10px' } },
      h('div.mono', { style: { fontSize: '20px', fontWeight: '700', overflowWrap: 'anywhere' } }, ll),
      h('div.hero-op', x.accuracy_m !== null ? `accurate to about ${Math.round(x.accuracy_m)} m` : '')),
    kv([
      ['Altitude', x.altitude_m === null ? '' : `${Math.round(x.altitude_m)} m`],
      ['Speed', x.speed_mps === null ? '' : `${(x.speed_mps * 3.6).toFixed(1)} km/h`],
      ['Heading', x.heading_deg === null ? '' : `${Math.round(x.heading_deg)}°`],
      ['Satellites', x.satellites],
      ['Precision (HDOP / VDOP)', [x.hdop, x.vdop].filter(v => v !== null).join(' / ')],
      ['Time of fix', x.time ? fmt.dateTime(x.time) : (x.timestamp || '')],
    ], { compact: true }),
    h('div.form-actions', copy, extLink(osm, 'Open in OpenStreetMap'), extLink(x.geo_uri, 'Open in a maps app')),
    h('p.hint', 'The links open an external website or app in a new window; nothing is sent from this page.'),
  ];
}

export default {
  id: 'services-gps', path: '/services/gps', group: 'services', order: 10, title: 'GPS', icon: 'gps', feature: 'gps',
  render(el) {
    const statusBody = h('div', spinner());
    const fixBody = h('div', spinner());
    const settings = h('div');
    el.append(h('div.grid-2', card('GPS', statusBody, { icon: 'gps' }), card('Position', fixBody, { icon: 'globe' })), settings);
    let form = null;
    let running = false;

    async function refreshFix() {
      try { const f = await get('/api/gps/fix'); append(clear(fixBody), fixView(f)); } catch (e) { clear(fixBody).appendChild(errorBox(e, refreshFix)); }
    }

    async function refresh() {
      let g;
      try { g = await get('/api/gps'); } catch (e) {
        clear(statusBody).appendChild(isUnavailable(e) ? alert('info', e.message) : errorBox(e, refresh));
        clear(fixBody);
        return;
      }
      running = g.running;
      const start = button('Turn on', async () => {
        let accept = false;
        if (!g.privacy_accepted) { accept = await acceptPrivacy(); if (!accept) return; }
        await post('/api/gps/start', { accept_privacy: accept || undefined });
        toast('GPS is on'); await refresh(); if (form) form.reload();
      }, { kind: 'primary', icon: 'power' });
      const stop = button('Turn off', async () => { await post('/api/gps/stop', {}); toast('GPS is off'); await refresh(); if (form) form.reload(); },
        { icon: 'power' });
      const xtra = button('Download assistance data', async () => { await post('/api/gps/xtra', {}); toast('XTRA download requested'); },
        { small: true, title: 'Fetches satellite orbit data from the Internet so the first fix is faster (needs mobile data)' });
      append(clear(statusBody), [
        h('div.hero-state', badge(g.running ? 'On' : 'Off', g.running ? 'ok' : 'neutral'), ' ',
          badge(g.status || '—', statusTone(g.status))),
        kv([
          ['Start at boot', g.start_at_boot === null ? '' : (g.start_at_boot ? 'Yes' : 'No')],
          ['Privacy notice', g.privacy_accepted ? 'Accepted' : 'Not accepted'],
          ['Mode', MODE_LABELS[g.mode] || g.mode || ''],
          ['NMEA over Wi-Fi', g.nmea_enabled ? `On, port ${g.nmea_port}${g.lan_ip ? ` (tcp://${g.lan_ip}:${g.nmea_port})` : ''}` : 'Off'],
          ['GPS service', g.service_running ? 'Running' : 'Not running'],
        ], { compact: true }),
        h('div.form-actions', g.running ? stop : start, xtra)]);
      await refreshFix();
    }

    smartForm(settings, {
      title: 'GPS settings', icon: 'system',
      load: () => get('/api/gps'),
      save: (ch) => put('/api/gps', ch),
      onReady: (f) => { form = f; },
      warn: (ch) => (ch.nmea_enabled === true ? 'Every device on your network will be able to read the router\'s position from this port. Continue?' : null),
      fields: [
        { key: 'start_at_boot', label: 'Turn GPS on when the router starts', type: 'switch' },
        { key: 'privacy_accepted', label: 'Location privacy notice accepted', type: 'switch',
          help: 'Needed for GPS to work. Turning it off also turns GPS off.' },
        { key: 'mode', label: 'Positioning mode', type: 'select',
          options: [['xtra', MODE_LABELS.xtra], ['msb', MODE_LABELS.msb], ['msa', MODE_LABELS.msa]],
          help: 'Network-assisted modes need a mobile connection; XTRA works anywhere after the assistance data was downloaded.' },
        { type: 'section', label: 'NMEA over Wi-Fi', help: 'Serves the position as NMEA sentences on a TCP port, for navigation programs on a laptop.' },
        { key: 'nmea_enabled', label: 'NMEA server', type: 'switch' },
        { key: 'nmea_port', label: 'Port', type: 'number', min: 1024, max: 65535, show: (v) => !!v.nmea_enabled,
          help: (v) => (v.lan_ip && v.nmea_port ? `Connect to tcp://${v.lan_ip}:${v.nmea_port}` : '') },
      ],
    });

    refresh();
    const timer = setInterval(() => { if (!document.hidden && running) refreshFix(); }, 10000);
    return () => clearInterval(timer);
  },
};
