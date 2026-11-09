// Dashboard — reference page: live data from the shared status store, no own polling.
import { h, clear } from '../ui/dom.js';
import { card, kv, stat, badge, signalBars, meter, alert, button, toast } from '../ui/widgets.js';
import { lineChart } from '../ui/charts.js';
import * as fmt from '../ui/format.js';
import { post, put } from '../api.js';

// Quick actions use the area endpoints; tier confirmations (HTTP 428) are handled by api.js.
function quickActions(st, ctx) {
  if (st.platform === 'openwrt') return card('Manage your router', h('div.form-actions',
    ...[['/openwrt/cellular', 'Cellular'], ['/openwrt/wireless', 'Wireless'], ['/openwrt/network', 'Interfaces'], ['/system/openwrt', 'Capabilities']]
      .map(([path, label]) => h('a.btn', { href: '#' + path }, label))));
  const c = st.connection || {}, w = st.wifi || {};
  const done = (msg) => { toast(msg); setTimeout(() => ctx.store.refreshNow(), 1500); };
  const connected = c.state === 'connected';
  return card('Quick actions', h('div.form-actions',
    connected
      ? button('Reconnect', async () => { await post('/api/cellular/reconnect', {}); done('Reconnecting…'); }, { icon: 'refresh' })
      : button('Connect', async () => { await post('/api/cellular/connect', {}); done('Connecting…'); }, { icon: 'link', disabled: c.state === 'no_sim' }),
    connected && button('Disconnect', async () => { await post('/api/cellular/disconnect', {}); done('Disconnected'); }, { icon: 'power' }),
    button(c.state === 'airplane' ? 'Airplane mode off' : 'Airplane mode', async () => {
      await put('/api/cellular/mode', { airplane: c.state !== 'airplane' },
        { confirmMessage: 'Airplane mode turns off the mobile radio. Wi-Fi and USB stay available.' });
      done('Airplane mode updated');
    }, { icon: 'antenna' }),
    button(w.enabled == null ? 'Wi-Fi status unavailable' : w.enabled ? 'Pause Wi-Fi' : 'Resume Wi-Fi', async () => {
      await post('/api/wifi/radio', { on: !w.enabled }, { confirmMessage: 'Devices connected over Wi-Fi will be disconnected until Wi-Fi is resumed.' });
      done(w.enabled ? 'Wi-Fi paused' : 'Wi-Fi resumed');
    }, { icon: 'wifi', disabled: w.enabled == null }),
    h('a.btn', { href: st.platform === 'openwrt' ? '#/system/openwrt' : '#/system/power' }, st.platform === 'openwrt' ? 'Native controls' : 'Restart…')), { icon: 'power' });
}

function signalNumber(v) {
  return (typeof v === 'number' || typeof v === 'string' && v.trim() !== '') && Number.isFinite(Number(v));
}

function q(kind, v, unit) {
  const qual = fmt.quality(kind, v);
  if ((kind === 'sinr' || kind === 'snr') && signalNumber(v) && Number(v) === 0) return h('span.q-' + qual, '0 ' + (unit || 'dB'));
  return h('span.q-' + qual, fmt.dbm(v, unit));
}

export default {
  id: 'dashboard', path: '/', group: 'dashboard', order: 0, title: 'Dashboard', icon: 'dashboard',
  async render(el, ctx) {
    const top = h('div');
    const charts = h('div.grid-2');
    const lower = h('div.grid-3');
    const actions = h('div');
    el.append(top, actions, charts, lower);
    let lastActionsKey = '';

    const sigChart = h('div'), rateChart = h('div');
    charts.append(
      card('Signal history', [sigChart, h('div.legend', h('span', h('i'), 'LTE RSRP (dBm)'), h('span', h('i.s2'), 'NR RSRP (dBm)'), h('span', h('i.s3'), 'SINR (dB)'))], { icon: 'cellular' }),
      card('Throughput', [rateChart, h('div.legend', h('span', h('i'), 'Download (Mbit/s)'), h('span', h('i.s2'), 'Upload (Mbit/s)'))], { icon: 'globe' }));

    const unsub = ctx.store.subscribe((s) => {
      const st = s.status;
      if (!st) return;
      const c = st.connection, sig = st.signal || {}, nr = st.nr || {};
      const cachedLTE = sig.sample_time_known === false && [sig.rsrp, sig.rsrq, sig.sinr].some(signalNumber);
      const cachedNR = nr.sample_time_known === false && [nr.rsrp, nr.snr].some(signalNumber);
      const showNR = nr.service_available || cachedNR;
      clear(top);
      if (cachedLTE || cachedNR) top.appendChild(alert('info',
        [cachedLTE && 'LTE', cachedNR && 'NR'].filter(Boolean).join(' / ') + ' signal values are cached; measurement age is unknown. '
        + 'These values do not establish current radio service and are excluded from fresh signal history.'));
      if (st.errors && Object.keys(st.errors).length) top.appendChild(alert('warn',
        'Some readings are unavailable: ' + Object.entries(st.errors).map(([name, error]) => `${name}: ${error.message}`).join('; ')));
      if (c.state === 'no_sim') top.appendChild(alert('warn', 'No SIM card detected. Insert a SIM to connect to a mobile network.'));
      const tone = { connected: 'ok', disconnected: 'warn', no_service: 'bad', no_sim: 'bad', airplane: 'warn' }[c.state] || 'neutral';
      const stateLabel = { connected: 'Connected', disconnected: 'Disconnected', no_service: 'No service', no_sim: 'No SIM', airplane: 'Airplane mode', unknown: 'Unknown', unavailable: 'Unavailable' }[c.state] || c.state;
      top.appendChild(h('div.grid-2',
        card(null, h('div.hero',
          h('div', { class: 'q-' + (tone === 'ok' ? 'good' : tone === 'warn' ? 'fair' : 'poor') }, signalBars(sig.bars)),
          h('div',
            h('div.hero-tech', c.tech && c.tech !== 'none' ? c.tech : '—'),
            h('div.hero-op', c.operator || 'No operator'),
            h('div.hero-state', badge(stateLabel, tone), c.roaming && badge('Roaming', 'warn'), c.data_enabled === false && badge('Mobile data off', 'warn'),
              c.data_enabled == null && badge('Mobile data status unavailable', 'neutral'),
              sig.band && sig.band !== '0' && badge('B' + sig.band, 'accent'), nr.band ? badge('n' + nr.band, 'accent') : null)))),
        card('Signal', h('div.stats',
          stat('RSRP', q('rsrp', sig.rsrp), 'LTE'), stat('RSRQ', q('rsrq', sig.rsrq, 'dB'), 'LTE'), stat('SINR', q('sinr', sig.sinr, 'dB'), 'LTE'),
          showNR && stat('NR RSRP', q('rsrp', nr.rsrp), 'n' + (nr.band || '?')),
          showNR && stat('NR SNR', q('snr', nr.snr, 'dB'), nr.bandwidth ? nr.bandwidth + ' MHz' : ''),
          stat('PCI', sig.pci || '—', sig.bandwidth_mhz ? sig.bandwidth_mhz + ' MHz' : '')), { icon: 'antenna',
          actions: h('a.btn.btn-sm', { href: st.platform === 'openwrt' ? '#/system/openwrt' : '#/cellular/signal' }, 'Details') })));

      // re-render the action buttons only when the state they depend on changes (keeps focus/busy state)
      const aKey = [c.state, st.wifi && st.wifi.enabled].join('|');
      if (aKey !== lastActionsKey) { lastActionsKey = aKey; clear(actions).appendChild(quickActions(st, ctx)); }

      const hist = s.history;
      clear(sigChart).appendChild(lineChart([{ values: hist.rsrp }, { values: hist.nr_rsrp, cls: 's2' }, { values: hist.sinr, cls: 's3' }], { height: 170 }));
      clear(rateChart).appendChild(lineChart([{ values: hist.rx_bps.map(v => v == null ? null : v / 1e6) }, { values: hist.tx_bps.map(v => v == null ? null : v / 1e6), cls: 's2' }], { height: 170, min: 0 }));

      const w = st.wan || {}, b = st.battery || {};
      const clientCount = fmt.metricNumber(st.clients && st.clients.total);
      clear(lower).append(
        card('Internet', kv([
          ['Status', badge(w.status || '—', w.status === 'Connected' ? 'ok' : 'warn')],
          ['IPv4', w.ipv4 && h('span.mono', w.ipv4)], ['IPv6', w.ipv6 && h('span.mono', w.ipv6)],
          ['DNS', w.dns && w.dns.length ? h('span.mono', w.dns.join(', ')) : ''],
          ['Session', fmt.duration(w.duration_s)],
          ['Data used', `↓ ${fmt.bytes(w.rx_bytes)}  ↑ ${fmt.bytes(w.tx_bytes)}`],
        ]), { icon: 'globe' }),
        card('Device', kv([
          b.percent !== undefined && b.percent !== null && ['Battery', h('span', meter(b.percent), ` ${b.percent}%`, b.charging ? ' · charging' : '')],
          ['Temperature', st.temps && st.temps.length ? st.temps.map(t => `${t.celsius}°C`).slice(0, 2).join(' / ') : ''],
          ['Uptime', fmt.duration(st.uptime_s)],
          ['Memory free', !st.memory || fmt.metricNumber(st.memory.available) === null && fmt.metricNumber(st.memory.total) === null
            ? 'Unavailable' : `${fmt.bytes(st.memory.available)} of ${fmt.bytes(st.memory.total)}`],
          ['Load', Array.isArray(st.load) && st.load.length && st.load.every(x => fmt.metricNumber(x) !== null) ? st.load.join(' ') : 'Unavailable'],
        ]), { icon: 'device' }),
        card('Wi-Fi & clients', kv([
          ['Wi-Fi', badge(st.wifi && st.wifi.enabled != null ? st.wifi.enabled ? 'On' : 'Off' : 'Unavailable', st.wifi && st.wifi.enabled ? 'ok' : 'neutral')],
          ['Main network', st.wifi && st.wifi.primary_ssid], ['Guest network', st.wifi && st.wifi.guest_ssid],
          ['Clients', clientCount === null ? 'Unavailable' : st.platform === 'openwrt' ? `${clientCount} DHCP leases; connection type unavailable` : `${clientCount} (Wi-Fi ${st.clients.wifi}, USB ${st.clients.usb}, Ethernet ${st.clients.ethernet})`],
          ['Messages', st.sms_unread == null ? 'Unavailable' : st.sms_unread ? h('a', { href: st.platform === 'openwrt' ? '#/system/openwrt' : '#/cellular/sms' }, `${st.sms_unread} unread`) : 'No unread'],
        ]), { icon: 'wifi', actions: h('a.btn.btn-sm', { href: st.platform === 'openwrt' ? '#/system/openwrt' : '#/wifi/clients' }, st.platform === 'openwrt' ? 'Native controls' : 'Clients') }));
    });
    return unsub;
  },
};
