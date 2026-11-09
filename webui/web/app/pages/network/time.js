// Network › Time: router clock, mobile-network time, NTP servers, "sync now" and automatic sync.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, button, toast, alert } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { block, safeForm, hostMsg } from './_net.js';

function tzText(sec) {
  if (sec === null || sec === undefined) return '';
  const s = sec < 0 ? '-' : '+';
  const a = Math.abs(sec);
  return `UTC${s}${String(Math.floor(a / 3600)).padStart(2, '0')}:${String(Math.floor(a % 3600 / 60)).padStart(2, '0')}`;
}

export default {
  id: 'network-time', path: '/network/time', group: 'network', order: 80, title: 'Time', icon: 'clock',
  async render(el) {
    const clockBox = block(el), resultBox = block(el), ntpBox = block(el), syncBox = block(el);
    let t = null, skew = 0, timer = null;
    const nowEl = h('span.mono');

    const tick = () => {
      if (!t) return;
      const d = new Date(Date.now() + skew);
      nowEl.textContent = `${d.toLocaleString()} (your browser's time zone)`;
    };

    const showResult = (r) => {
      clear(resultBox);
      if (!r) return;
      const when = r.t ? fmt.dateTime(r.t) : '';
      resultBox.appendChild(card('Last synchronisation', [
        alert(r.ok ? 'ok' : 'bad', r.ok ? `Clock set from ${r.server} ${when}.` : `Could not reach ${r.server} (${when}).`,
          r.ok && Math.abs(r.clock_change_s || 0) >= 1 ? ` The clock moved by ${Math.round(r.clock_change_s)} s.` : ''),
        r.output ? h('pre.pre', r.output) : null,
      ], { icon: 'clock' }));
    };

    const showClock = () => {
      const nt = t.network_time || {};
      clear(clockBox).appendChild(card('Router clock', [
        !t.clock_plausible ? alert('warn', 'The router clock looks wrong (before 2023). Synchronise it so logs and certificates work.') : null,
        kv([
          ['Router time', nowEl],
          ['Router time (UTC)', h('span.mono', t.utc)],
          ['Router time zone', `${t.timezone} ${tzText(t.tz_offset_s)}`],
          ['Mobile network time', nt.available && nt.timestamp ? `${fmt.dateTime(nt.timestamp)}${nt.tz_offset_min !== null ? ` · network zone ${tzText(nt.tz_offset_min * 60)}` : ''}${nt.dst ? ' · DST' : ''}` : 'Not available (needs a mobile connection)'],
          ['Up for', fmt.duration(t.uptime_s)],
          ['Automatic sync', t.sync && (t.sync.sync_at_boot || t.sync.interval_h) ? badge('On', 'ok') : badge('Off', 'neutral')],
        ]),
        h('p.hint', 'If the clock jumps by a lot (for example from 1980 to today), you may have to log in again.'),
      ], { icon: 'clock', actions: [button('Sync now', async () => {
        const d = await post('/api/time/sync', {});
        t = d; skew = d.now * 1000 - Date.now();
        showClock(); showResult(d.result);
        toast(d.result && d.result.ok ? 'Clock synchronised' : 'Synchronisation failed', d.result && d.result.ok ? 'ok' : 'bad');
      }, { small: true, kind: 'primary', icon: 'refresh' })] }));
      tick();
    };

    const load = async () => {
      t = await get('/api/time');
      skew = t.now * 1000 - Date.now();
      showClock();
      showResult(t.sync && t.sync.last);
      return t;
    };
    await load().catch(e => clockBox.appendChild(alert('bad', e.message)));
    timer = setInterval(tick, 1000);

    const flatNtp = (d) => {
      const n = d.ntp || {};
      const s = n.servers || [];
      return { server1: s[0] || '', server2: s[1] || '', server3: s[2] || '', min_delay_h: n.min_delay_s ? Math.round(n.min_delay_s / 3600) : 168, retries: n.retries };
    };
    safeForm(ntpBox, {
      title: 'Time servers', icon: 'globe',
      description: 'Servers the router\'s connection manager uses to set the clock on its own when the internet connection is up.',
      load: async () => { const d = await get('/api/time'); if (!d.ntp) throw Object.assign(new Error('The NTP settings are not available right now'), { status: 503, code: 'unavailable' }); return flatNtp(d); },
      save: async (ch, v) => {
        const d = await put('/api/time/ntp', { servers: [v.server1, v.server2 || '', v.server3 || ''].map(s => String(s).trim()),
          min_delay_s: Number(v.min_delay_h) * 3600, retries: Number(v.retries) });
        return flatNtp(d);
      },
      fields: [
        { key: 'server1', label: 'Server 1', required: true, validate: hostMsg, placeholder: 'pool.ntp.org' },
        { key: 'server2', label: 'Server 2', validate: hostMsg, placeholder: 'optional' },
        { key: 'server3', label: 'Server 3', validate: hostMsg, placeholder: 'optional' },
        { key: 'min_delay_h', label: 'Minimum time between syncs', type: 'number', unit: 'hours', min: 1, max: 720, required: true },
        { key: 'retries', label: 'Retries', type: 'number', min: 0, max: 1000, required: true },
      ],
    });

    safeForm(syncBox, {
      title: 'Automatic sync by this interface', icon: 'refresh',
      description: 'Run a one-shot NTP synchronisation (busybox ntpd) when the router starts and/or every few hours, as the mod ROMs do.',
      load: async () => (await get('/api/time')).sync,
      save: async (ch) => (await put('/api/time/sync', ch)).sync,
      fields: [
        { key: 'sync_at_boot', label: 'Sync after start-up', type: 'switch', help: 'About a minute after the router starts.' },
        { key: 'interval_h', label: 'Repeat every', type: 'number', unit: 'hours', min: 0, max: 168, help: '0 = do not repeat.' },
        { key: 'server', label: 'Server', required: true, validate: hostMsg, placeholder: 'pool.ntp.org', help: 'Also used by "Sync now".' },
      ],
    });

    return () => { if (timer) clearInterval(timer); };
  },
};
