// Cellular > Network mode: technologies (incl. hidden stock options), 5G SA/NSA, 5G radio, cellular radio,
// airplane mode, mobile data, auto-connect, roaming, roam guard, 5G icon timer, connect / disconnect now.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, alert, button, toast, confirmSimple } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { notes, problem, isExpert, onOff, dash } from './_radio.js';

const BITS = { GSM: 0x01, UMTS: 0x02, CDMA: 0x04, EVDO: 0x08, LTE: 0x10, NR5G: 0x20 };
const maskOf = (modes) => (modes || []).reduce((m, n) => m | (BITS[n] || 0), 0);

let cache = null;
function getMode(force) {
  if (force || !cache || Date.now() - cache.t > 1500) cache = { t: Date.now(), p: get('/api/cellular/mode') };
  return cache.p;
}

export default {
  id: 'cellular-mode', path: '/cellular/mode', group: 'cellular', order: 20, title: 'Network mode',
  icon: 'cellular', feature: 'cellular.radio',
  async render(el, ctx) {
    const expert = isExpert(ctx);
    const top = h('div');
    const connBox = h('div');
    const techBox = h('div'), dataBox = h('div'), airBox = h('div'), rgBox = h('div'), iconBox = h('div');
    el.append(top, connBox, h('div.grid-2', techBox, dataBox), h('div.grid-2', airBox, rgBox), iconBox);

    let data;
    try { data = await getMode(true); } catch (e) { top.appendChild(problem(e, () => location.reload())); return; }
    const labels = data.labels || {};
    const presetLabel = (id, modes) => {
      const p = (data.presets || []).find(x => x.id === id);
      return p ? p.label : `Custom: ${(modes || []).map(m => (labels.tech || {})[m] || m).join(', ') || 'none'}`;
    };
    const nrLabel = (m) => (labels.nr5g_mode || {})[m] || m || dash;
    if (data.notes && data.notes.length) top.appendChild(notes(data.notes));

    // ---- connection -----------------------------------------------------------------------------
    const connKv = h('div');
    async function refreshConn() {
      try {
        const o = await get('/api/cellular/overview');
        const c = o.connection || {};
        clear(connKv).appendChild(kv([
          ['Mobile data connection', badge(c.status || 'Unknown', c.status === 'Connected' ? 'ok' : 'warn')],
          ['IPv4 / IPv6 bearer', `${c.ipv4_state || dash} / ${c.ipv6_state || dash}`],
          ['Public IPv4', c.ipv4 ? h('span.mono', c.ipv4) : dash],
          ['Connected for', c.duration_s ? fmt.duration(c.duration_s) : dash],
          ['Operator', o.operator || dash],
          ['SIM', o.sim_present === false ? badge('No SIM', 'bad') : o.sim_present ? 'Present' : dash],
        ]));
      } catch (e) { clear(connKv).appendChild(problem(e)); }
    }
    const act = (path, msg) => async () => {
      const r = await post(path, {});
      toast(msg);
      ctx.store.refreshNow && ctx.store.refreshNow();
      await refreshConn();
      return r;
    };
    connBox.appendChild(card('Connection', [h('p.card-desc', 'Start or stop the mobile data connection right now. With auto-connect on, the router reconnects by itself later.'),
      connKv], { icon: 'globe', actions: [
      button('Connect', act('/api/cellular/connect', 'Connecting…'), { small: true, kind: 'primary', icon: 'power' }),
      button('Reconnect', act('/api/cellular/reconnect', 'Reconnecting…'), { small: true, icon: 'refresh' }),
      button('Disconnect', async () => {
        if (!(await confirmSimple('Disconnect', 'All devices lose internet access until you connect again (or auto-connect does).', 'Disconnect', 'danger'))) return;
        await act('/api/cellular/disconnect', 'Disconnected')();
      }, { small: true }),
    ] }));
    refreshConn();

    // ---- technology / 5G / radios (tier 2) --------------------------------------------------------
    const info = data.info || {};
    const presets = (data.presets || []).filter(p => !p.expert || expert || p.id === data.tech_preset);
    const presetOpts = presets.map(p => [p.id, p.label, p.help]);
    if (expert || data.tech_preset === 'custom') presetOpts.push(['custom', 'Custom combination', 'Choose the technologies yourself (expert).']);
    const modeOpts = (info.supported_modes || ['UMTS', 'LTE', 'NR5G']).map(m => [m, (labels.tech || {})[m] || m]);
    const nrOpts = ['sa_nsa', 'sa', 'nsa'].map(m => [m, nrLabel(m)]);
    const effMask = (v) => v.tech_preset && v.tech_preset !== 'custom'
      ? ((data.presets || []).find(p => p.id === v.tech_preset) || {}).mask : maskOf(v.tech_modes);

    const labelForMask = (mask) => {
      const p = (data.presets || []).find(x => x.mask === mask);
      return p ? p.label : presetLabel('custom', Object.keys(BITS).filter(n => mask & BITS[n]));
    };
    const restoreBtn = info.previous && info.previous.value ? button('Restore previous', async () => {
      const pv = info.previous.value;
      const r = await post('/api/cellular/mode/restore', {}, { confirmTitle: 'Restore previous network mode?',
        confirmMessage: `Technologies: ${labelForMask(pv.tech_mask)}; 5G mode: ${nrLabel(pv.nr5g_mode)} (saved ${fmt.ago(info.previous.t)}). The connection drops for a moment.` });
      toast('Previous network mode restored');
      techForm.reload();
      return r;
    }, { small: true, icon: 'refresh' }) : null;

    const techForm = form(techBox, {
      title: 'Network technology', icon: 'cellular',
      description: 'Which mobile network generations the modem may use. Changes reconnect the modem (a few seconds without internet).',
      headerActions: restoreBtn ? [restoreBtn] : undefined,
      load: async () => { const d = await getMode(); return { tech_preset: d.tech_preset, tech_modes: d.tech_modes, nr5g_mode: d.nr5g_mode, nr5g_radio: d.nr5g_radio, radio: d.radio }; },
      save: async (ch, v) => {
        const body = Object.assign({}, ch);
        if ('tech_preset' in body && body.tech_preset === 'custom') body.tech_modes = v.tech_modes;
        if ('tech_modes' in body && v.tech_preset !== 'custom') delete body.tech_modes;
        const msg = [];
        if ('tech_preset' in body || 'tech_modes' in body) msg.push(`Technologies: ${presetLabel(data.tech_preset, data.tech_modes)} → ${presetLabel(v.tech_preset, v.tech_modes)}`);
        if ('nr5g_mode' in body) msg.push(`5G mode: ${nrLabel(data.nr5g_mode)} → ${nrLabel(v.nr5g_mode)}`);
        if ('nr5g_radio' in body) msg.push(`5G radio: ${onOff(data.nr5g_radio)} → ${onOff(v.nr5g_radio)}`);
        if ('radio' in body) msg.push(`Cellular radio: ${onOff(data.radio)} → ${onOff(v.radio)}${v.radio ? '' : ' (no mobile connection at all)'}`);
        const d = await put('/api/cellular/mode', body, { confirmTitle: 'Change radio settings?',
          confirmMessage: msg.join('; ') + '. The previous values are saved so you can restore them.' });
        data = d;
        cache = null;
        return { tech_preset: d.tech_preset, tech_modes: d.tech_modes, nr5g_mode: d.nr5g_mode, nr5g_radio: d.nr5g_radio, radio: d.radio };
      },
      fields: [
        { key: 'tech_preset', label: 'Technologies', type: 'radio', options: presetOpts, full: true },
        { key: 'tech_modes', label: 'Custom technologies', type: 'chips', options: modeOpts, show: v => v.tech_preset === 'custom',
          help: 'Without 4G LTE or 3G the modem only works where 5G standalone is available.',
          validate: v => (!v || !v.length) ? 'Select at least one technology' : null },
        { key: 'nr5g_mode', label: '5G mode', type: 'select', options: nrOpts, show: v => !!(effMask(v) & 0x20),
          help: 'SA = 5G standalone core; NSA = 5G added on top of an LTE connection. Automatic uses whatever the network offers.',
          validate: (val, v) => (effMask(v) === 0x20 && val === 'nsa') ? '5G NSA needs 4G LTE. Choose SA or automatic.' : null },
        { key: 'nr5g_radio', label: '5G radio', type: 'switch', help: 'Quick 5G on/off switch. Off removes 5G from the technologies above.' },
        { key: 'radio', label: 'Cellular radio', type: 'switch', help: 'Off = no mobile network at all (Wi-Fi and LAN keep working).' },
      ],
    });

    // ---- mobile data, auto-connect, roaming (tier 1) ----------------------------------------------
    const acOpts = Object.entries(labels.auto_connect_mode || {}).map(([k, l]) => [k, l]);
    form(dataBox, {
      title: 'Mobile data & roaming', icon: 'globe',
      description: 'Settings for the internet connection over the mobile network.',
      load: async () => { const d = await getMode(); return { data_enabled: d.data_enabled, auto_connect: d.auto_connect, auto_connect_mode: d.auto_connect_mode, roaming_domestic: d.roaming_domestic, roaming_international: d.roaming_international }; },
      save: async (ch) => {
        const d = await put('/api/cellular/mode', ch);
        cache = null;
        return { data_enabled: d.data_enabled, auto_connect: d.auto_connect, auto_connect_mode: d.auto_connect_mode, roaming_domestic: d.roaming_domestic, roaming_international: d.roaming_international };
      },
      warn: (ch) => {
        if (ch.data_enabled === false) return 'Turning mobile data off disconnects the internet for every device on this router.';
        if (ch.roaming_domestic === true || ch.roaming_international === true) return 'Data roaming on other networks can cost extra, depending on your plan.';
        return null;
      },
      fields: [
        { key: 'data_enabled', label: 'Mobile data', type: 'switch', help: 'Use the mobile network for internet access.' },
        { key: 'auto_connect', label: 'Connect automatically', type: 'switch', help: 'Reconnect by itself after start-up or a drop.' },
        { key: 'auto_connect_mode', label: 'When to connect automatically', type: 'select', options: acOpts, show: v => v.auto_connect_mode !== null,
          help: 'Finer control over automatic connection.' },
        { key: 'roaming_domestic', label: 'Domestic roaming', type: 'switch', help: 'Allow data on partner networks in your home country.' },
        { key: 'roaming_international', label: 'International roaming', type: 'switch', help: 'Allow data on networks abroad.' },
      ],
    });

    // ---- airplane mode (tier 2) -------------------------------------------------------------------
    const dis = info.airplane_disables || {};
    const disList = [dis.cellular && 'cellular', dis.wifi && 'Wi-Fi'].filter(Boolean);
    form(airBox, {
      title: 'Airplane mode', icon: 'power',
      description: disList.length ? `Airplane mode turns off: ${disList.join(' and ')}.` : 'Turns the radios off.',
      load: async () => { const d = await getMode(); return { airplane: d.airplane }; },
      save: async (ch) => {
        const msg = ch.airplane
          ? (dis.wifi ? 'Wi-Fi turns off too. Devices connected over Wi-Fi (possibly this one) lose access to the router; use USB or Ethernet, or the device screen, to switch airplane mode off again.'
            : 'The mobile connection turns off.')
          : 'The radios turn back on and the router reconnects.';
        const d = await put('/api/cellular/mode', ch, { confirmTitle: ch.airplane ? 'Turn airplane mode on?' : 'Turn airplane mode off?', confirmMessage: msg });
        cache = null;
        return { airplane: d.airplane };
      },
      fields: [{ key: 'airplane', label: 'Airplane mode', type: 'switch', onLabel: 'On (radios off)', offLabel: 'Off' }],
    });

    // ---- roam guard (tier 1) ----------------------------------------------------------------------
    if (data.roam_guard) {
      const rgKeys = Object.keys(data.roam_guard);
      const rgField = (k, label, help) => ({ key: 'rg_' + k, label, help, type: 'switch',
        readonly: /forced/.test(k) && !expert });
      form(rgBox, {
        title: 'Roaming alerts (roam guard)', icon: 'warn',
        description: 'Carrier roam-guard settings: ask before data roaming starts. "Forced" values are set by the carrier; change them only in expert mode.',
        load: async () => { const d = await getMode(); const o = {}; rgKeys.forEach(k => { o['rg_' + k] = (d.roam_guard || {})[k]; }); return o; },
        save: async (ch) => {
          const rg = {};
          Object.keys(ch).forEach(k => { rg[k.slice(3)] = ch[k]; });
          const d = await put('/api/cellular/mode', { roam_guard: rg });
          cache = null;
          const o = {};
          rgKeys.forEach(k => { o['rg_' + k] = (d.roam_guard || {})[k]; });
          return o;
        },
        fields: [
          { type: 'section', label: 'Domestic' },
          rgField('domestic_enabled', 'Roaming allowed'), rgField('domestic_guard_enabled', 'Warn before roaming'),
          rgField('domestic_forced', 'Roaming forced by carrier'), rgField('domestic_guard_forced', 'Warning forced by carrier'),
          { type: 'section', label: 'International' },
          rgField('international_enabled', 'Roaming allowed'), rgField('international_guard_enabled', 'Warn before roaming'),
          rgField('international_forced', 'Roaming forced by carrier'), rgField('international_guard_forced', 'Warning forced by carrier'),
        ],
      });
    } else {
      rgBox.appendChild(card('Roaming alerts (roam guard)', alert('info', 'Roam guard settings are not available on this device.'), { icon: 'warn' }));
    }

    // ---- 5G indicator (icon mode read-only, UW hold timer when the firmware reports it) ------------
    const icon = info.icon_mode;
    if (data.tuwb !== null && data.tuwb !== undefined) {
      form(iconBox, {
        title: '5G status icon', icon: 'about',
        description: `Icon configuration: ${icon ? icon.label : dash} (read-only).`,
        load: async () => { const d = await getMode(); return { tuwb: d.tuwb }; },
        save: async (ch) => { const d = await put('/api/cellular/mode', ch); cache = null; return { tuwb: d.tuwb }; },
        fields: [{ key: 'tuwb', label: '5G UW icon hold time', type: 'number', min: 0, max: 30, unit: 's',
          help: 'How long the "5G UW" icon stays after the high-band 5G signal is lost (0–30 s).' }],
      });
    } else {
      iconBox.appendChild(card('5G status icon', kv([
        ['Icon configuration', icon ? icon.label : dash, 'set by the carrier profile; not changeable'],
        ['5G UW icon hold time', 'Not reported by this firmware'],
      ]), { icon: 'about' }));
    }
  },
};
