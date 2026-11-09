// Cellular > Antenna & advanced: external antenna ports, LTE-U / Wi-Fi 5 GHz coexistence, autonomous gaps,
// and read-only radio status (call throttling, world mode, test SIM, emergency mode, timers, PCO, eMBMS).
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, alert, button } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { get, put } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { problem, yesNo, onOff, num, dash } from './_radio.js';

let cache = null;
function getAnt(force) {
  if (force || !cache || Date.now() - cache.t > 1500) cache = { t: Date.now(), p: get('/api/cellular/antenna') };
  return cache.p;
}

const TRI = [['', 'Not set by this interface'], ['on', 'On'], ['off', 'Off']];
const tri = (v) => v === true ? 'on' : v === false ? 'off' : '';

export default {
  id: 'cellular-antenna', path: '/cellular/antenna', group: 'cellular', order: 70, title: 'Antenna & advanced',
  icon: 'antenna', feature: 'cellular.radio',
  async render(el, ctx) {
    const top = h('div'), antBox = h('div'), coexBox = h('div'), statusBox = h('div'), thrBox = h('div');
    el.append(top, h('div.grid-2', antBox, coexBox), h('div.grid-2', statusBox, thrBox));

    let data;
    try { data = await getAnt(true); } catch (e) { top.appendChild(problem(e, () => location.reload())); return; }

    // ---- external antenna (tier 2) ----------------------------------------------------------------
    const pick = (d) => ({ external: d.external, mhb: d.mhb, uhb: d.uhb });
    const hasPorts = data.mhb !== null && data.mhb !== undefined;
    if (data.external === null && !hasPorts) {
      antBox.appendChild(card('External antenna', alert('info', 'This device does not report an external antenna setting.'), { icon: 'antenna' }));
    } else {
      form(antBox, {
        title: 'External antenna', icon: 'antenna',
        description: 'Use the external antenna connectors instead of the built-in antennas. With nothing connected, reception on those bands gets much worse.',
        load: async () => pick(await getAnt()),
        save: async (ch) => {
          const parts = Object.keys(ch).map(k => `${{ external: 'External antenna', mhb: 'Mid/high band port', uhb: 'Ultra-high band port' }[k]}: ${onOff(ch[k])}`);
          const d = await put('/api/cellular/antenna', ch, { confirmTitle: 'Change antenna setting?',
            confirmMessage: parts.join('; ') + '. Reception changes immediately.' });
          cache = null;
          return pick(d);
        },
        fields: [
          data.external !== null && { key: 'external', label: 'External antenna', type: 'switch', onLabel: 'External', offLabel: 'Internal',
            help: data.external_source === 'rest' ? 'Main switch (vendor setting).' : 'Switches both antenna ports.' },
          hasPorts && { type: 'section', label: 'Antenna ports', help: 'Choose the antenna per frequency range (advanced).' },
          hasPorts && { key: 'mhb', label: 'Mid/high band (1.7–2.7 GHz)', type: 'switch', onLabel: 'External', offLabel: 'Internal' },
          hasPorts && { key: 'uhb', label: 'Ultra-high band (3.3–4.2 GHz, 5G C-band)', type: 'switch', onLabel: 'External', offLabel: 'Internal' },
        ],
      });
    }

    // ---- coexistence and gaps (tier 2) ------------------------------------------------------------
    const coexPick = (d) => ({ lte_u: tri(d.lte_u), autonomous_gap: d.autonomous_gap === null ? tri(null) : d.autonomous_gap });
    form(coexBox, {
      title: 'Coexistence & measurement gaps', icon: 'wifi',
      description: 'Advanced radio options. Leave them unchanged unless you know you need them.',
      load: async () => coexPick(await getAnt()),
      save: async (ch) => {
        const body = {};
        if ('lte_u' in ch && ch.lte_u !== '') body.lte_u = ch.lte_u === 'on';
        if ('autonomous_gap' in ch && ch.autonomous_gap !== '') body.autonomous_gap = ch.autonomous_gap === true || ch.autonomous_gap === 'on';
        if (!Object.keys(body).length) return coexPick(await getAnt(true));
        const d = await put('/api/cellular/antenna', body, { confirmTitle: 'Change advanced radio option?',
          confirmMessage: Object.keys(body).map(k => `${k === 'lte_u' ? 'LTE-U / Wi-Fi 5 GHz coexistence' : 'Autonomous gaps'}: ${onOff(body[k])}`).join('; ') + '.' });
        cache = null;
        return coexPick(d);
      },
      fields: [
        { key: 'lte_u', label: 'LTE-U (LTE in the 5 GHz band)', type: 'select', options: TRI,
          help: data.lte_u_set_at ? `The modem cannot report this value; last set here ${fmt.ago(data.lte_u_set_at)}.`
            : 'The modem cannot report this value. Off avoids interference with 5 GHz Wi-Fi.' },
        data.autonomous_gap === null
          ? { key: 'autonomous_gap', label: 'Autonomous gaps', type: 'select', options: TRI,
            help: `Current value not reported (${(data.status || {}).autonomous_gap_message || 'not available'}). Lets the modem pause briefly to measure other cells.` }
          : { key: 'autonomous_gap', label: 'Autonomous gaps', type: 'switch', help: 'Lets the modem pause briefly to measure other cells (faster handover).' },
      ],
    });

    // ---- read-only status --------------------------------------------------------------------------
    const s = data.status || {};
    const thr = s.call_throttle;
    clear(statusBox).appendChild(card('Radio status', kv([
      ['Call throttling', thr ? h('span', badge(thr.ipv4 ? 'IPv4 throttled' : 'IPv4 ok', thr.ipv4 ? 'warn' : 'ok'), ' ',
        badge(thr.ipv6 ? 'IPv6 throttled' : 'IPv6 ok', thr.ipv6 ? 'warn' : 'ok')) : dash, 'back-off after failed connection attempts'],
      ['World mode', yesNo(s.world_mode), 'global roaming capable configuration'],
      ['Test SIM', yesNo(s.test_sim)],
      ['Emergency mode', yesNo(s.emergency_mode)],
      ['Home network', yesNo(s.home_network)],
      ['DSS', num(s.dss), 'dynamic spectrum sharing flag as reported by the modem'],
      ['Better-system reselection', s.bsr ? `every ${s.bsr.bsr_time_s} s (max ${s.bsr.max_time_s} s), next LTE scan ${s.bsr.next_lte_scan_s} s` : dash],
      ['Last carrier PCO', s.pco ? `${s.pco.value} (PDN ${s.pco.pdn})` : dash, 'account status message from the operator'],
      ['Broadcast (eMBMS)', s.embms === 'none' ? 'No broadcast data' : (s.embms || dash)],
    ]), { icon: 'about' }));

    // ---- call throttle check -------------------------------------------------------------------------
    const code = h('input', { type: 'number', min: 0, max: 65535, value: '0', 'aria-label': 'Network cause code' });
    const out = h('div.hint', 'Enter a network cause code (0–65535) to see whether the modem currently delays calls failing with it.');
    thrBox.appendChild(card('Call throttle check', [
      h('div.input-group', code, button('Check', async () => {
        try {
          const r = await get('/api/cellular/antenna/throttle?code=' + encodeURIComponent(code.value || '0'));
          clear(out).append(`Code ${r.code}: `, badge(r.throttled ? 'throttled' : 'not throttled', r.throttled ? 'warn' : 'ok'));
        } catch (e) { clear(out).appendChild(problem(e)); }
      }, { small: true })), out], { icon: 'tools' }));
    return () => { cache = null; };
  },
};
