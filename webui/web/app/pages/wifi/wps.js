// Wi-Fi > WPS: start a push-button or PIN session on one network/band, stop it, list devices that joined with WPS.
import { h, clear } from '../../ui/dom.js';
import { card, alert, badge, button, toast, kv } from '../../ui/widgets.js';
import { get, post } from '../../api.js';
import { guard, BAND_LABEL, NET_LABEL, SEC_LABEL } from './_util.js';

export default {
  id: 'wifi-wps', path: '/wifi/wps', group: 'wifi', order: 40, title: 'WPS', icon: 'link', feature: 'wifi.wps',
  render(el) {
    const body = h('div');
    el.appendChild(body);
    const timers = new Set();
    const load = () => guard(body, () => get('/api/wifi/wps'), (d) => paint(body, d, timers));
    load();
    return () => timers.forEach(t => clearInterval(t));
  },
};

function paint(el, d, timers) {
  el.appendChild(card('About WPS', h('p.card-desc',
    'WPS lets a device join without typing the password: press "Push button" here, then the WPS button on the device '
    + 'within two minutes. With "PIN", type the 4- or 8-digit PIN the device shows. WPS needs WPA2 (or WPA2/WPA3 mixed) '
    + 'and a visible network name.'), { icon: 'link' }));
  if (d.available === false) {
    el.appendChild(alert('info', 'This Wi-Fi chip does not support WPS.'));
    return null;
  }
  if (d.mac_filter_on) {
    el.appendChild(alert('info', 'The MAC filter allow-list is on, so WPS is blocked on the main network. ',
      h('a', { href: '#/wifi/macfilter' }, 'MAC filter')));
  }
  const grid = h('div.grid-2');
  el.appendChild(grid);
  const active = (d.entries || []).filter(e => e.active);
  if (!active.length) grid.appendChild(card(null, alert('warn', 'No Wi-Fi network is active right now.')));
  let refreshList = () => {};            // bound below, once the device list exists
  for (const e of active) grid.appendChild(entryCard(e, d, timers, () => refreshList()));
  const list = h('div');
  const paintList = (x) => {
    clear(list);
    if (!x.clients_available) list.appendChild(h('p.muted', 'The list is not available right now.'));
    else if (!x.clients.length) list.appendChild(h('p.muted', 'No device has joined with WPS since the last restart.'));
    else list.appendChild(h('div.table-wrap', h('table.table', h('thead', h('tr', h('th', 'MAC address'))),
      h('tbody', x.clients.map(c => h('tr', h('td.mono', { dataset: { label: 'MAC address' } }, c.mac)))))));
  };
  refreshList = async () => { try { paintList(await get('/api/wifi/wps')); } catch (_) { /* keep the old list */ } };
  el.appendChild(card('Devices that joined with WPS', list, { icon: 'users',
    actions: button('Refresh', refreshList, { small: true, icon: 'refresh' }) }));
  paintList(d);
  return null;
}

function entryCard(e, d, timers, onTick) {
  const status = h('div');
  const pin = h('input', { type: 'text', inputmode: 'numeric', maxlength: 8, placeholder: 'PIN from the device', autocomplete: 'off',
    style: { maxWidth: '180px' } });
  const blocked = !e.wps_enabled || (e.network === 'primary' && d.mac_filter_on);
  let timer = null;
  const countdown = (method) => {
    if (timer) { clearInterval(timer); timers.delete(timer); }
    let left = 120;
    const tick = () => {
      clear(status).appendChild(left > 0
        ? alert('ok', `WPS ${method === 'pin' ? 'PIN' : 'push button'} session running: ${left} s left.`)
        : alert('info', 'The WPS session has ended. Check the list below for new devices.'));
      if (left % 10 === 0 || left <= 0) onTick();
      if (left-- <= 0) { clearInterval(timer); timers.delete(timer); timer = null; }
    };
    tick();
    timer = setInterval(tick, 1000);
    timers.add(timer);
  };
  const start = (method) => async () => {
    const body = { network: e.network, band: e.band, method };
    if (method === 'pin') {
      const v = pin.value.trim();
      if (!/^\d{4}$|^\d{8}$/.test(v)) { toast('Enter the 4- or 8-digit PIN shown by the device', 'bad'); pin.focus(); return; }
      body.pin = v;
    }
    await post('/api/wifi/wps/start', body);
    toast('WPS started');
    countdown(method);
  };
  const stop = async () => {
    await post('/api/wifi/wps/stop', { network: e.network, band: e.band });
    if (timer) { clearInterval(timer); timers.delete(timer); timer = null; }
    clear(status).appendChild(alert('info', 'WPS stopped.'));
  };
  const body = h('div',
    kv([['Network name', e.ssid], ['Security', SEC_LABEL[e.security] || e.security], ['WPS', e.wps_enabled ? badge('Enabled', 'ok') : badge('Off', 'neutral')]], { compact: true }),
    blocked ? alert('info', e.wps_enabled ? 'WPS is blocked while the MAC filter allow-list is on.' : 'WPS is off for this network. ',
      !e.wps_enabled && h('a', { href: '#/wifi/' + e.network }, 'Turn it on in the network settings.'))
      : h('div', { style: { display: 'grid', gap: '10px', marginTop: '10px' } },
        h('div.form-actions', button('Push button', start('pbc'), { kind: 'primary', icon: 'link' }), button('Stop', stop)),
        h('div.input-group', pin, button('Start with PIN', start('pin')))),
    status);
  return card(`${NET_LABEL[e.network]} · ${BAND_LABEL[e.band]}`, body, { icon: 'wifi' });
}
