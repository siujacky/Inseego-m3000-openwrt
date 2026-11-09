// Cellular > Bands: LTE / 5G SA / 5G NSA band selection (chips), "enable all supported" (mod-ROM feature),
// restore previous / original, band priority, carrier aggregation and the raw EFS band preference.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, alert, button, toast } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { notes, problem, section, bandList, dash } from './_radio.js';

const LISTS = [
  ['lte', '4G LTE bands', 'B', 'Bands the modem may use for 4G. Highlighted = in use right now.'],
  ['nr_sa', '5G standalone (SA) bands', 'n', '5G bands used when the network runs 5G on its own core.'],
  ['nr_nsa', '5G non-standalone (NSA) bands', 'n', '5G bands added on top of an LTE connection (most common today).'],
];

function diff(from, to, prefix) {
  const a = new Set(from || []), b = new Set(to || []);
  const add = [...b].filter(x => !a.has(x)), rem = [...a].filter(x => !b.has(x));
  const parts = [];
  if (add.length) parts.push('enable ' + add.map(x => prefix + x).join(', '));
  if (rem.length) parts.push('disable ' + rem.map(x => prefix + x).join(', '));
  return parts.join('; ') || 'no change';
}

function parseOrder(text) {
  return String(text || '').split(/[\s,;]+/).filter(Boolean).map(x => Number(String(x).replace(/^[bn]/i, '')));
}

export default {
  id: 'cellular-bands', path: '/cellular/bands', group: 'cellular', order: 30, title: 'Bands',
  icon: 'chip', feature: 'cellular.radio',
  async render(el, ctx) {
    const top = h('div'), summary = h('div'), listsBox = h('div.grid'), prioBox = h('div'), caBox = h('div'), efsBox = h('div');
    el.append(top, summary, listsBox, h('div.grid-2', prioBox, caBox), efsBox);

    let data;
    async function load() { data = await get('/api/cellular/bands'); return data; }
    try { await load(); } catch (e) { top.appendChild(problem(e, () => location.reload())); return; }
    const names = data.band_names || { lte: {}, nr: {} };
    const forms = {};

    function renderSummary() {
      const lte = data.lte || {}, sa = data.nr_sa || {}, nsa = data.nr_nsa || {};
      clear(top);
      if (data.notes && data.notes.length) top.appendChild(notes(data.notes));
      const adds = LISTS.map(([k, , p]) => ((data[k] || {}).enable_all_adds || []).map(b => p + b)).flat();
      const hasOriginal = LISTS.some(([k]) => (data[k] || {}).original);
      const actions = [
        button('Enable all supported', async () => {
          if (!adds.length) { toast('All supported bands are already enabled'); return; }
          await post('/api/cellular/bands/enable-all', {}, { confirmTitle: 'Enable all supported bands?',
            confirmMessage: `This enables ${adds.join(', ')}. Bands already enabled stay on. The modem re-registers (a few seconds offline). The current lists are saved so you can restore them.` });
          toast('All supported bands enabled');
          await refresh();
        }, { small: true, kind: 'primary', title: 'Enables every band the modem hardware supports' }),
        hasOriginal && button('Restore original', async () => {
          await post('/api/cellular/bands/restore', { list: 'all', source: 'original' }, { confirmTitle: 'Restore the original bands?',
            confirmMessage: `Restore the band lists the router had before this interface first changed them${data.original_time ? ' (' + fmt.dateTime(data.original_time) + ')' : ''}.` });
          toast('Original bands restored');
          await refresh();
        }, { small: true, icon: 'refresh' }),
      ].filter(Boolean);
      const nrActive = (nsa.active && nsa.active.length) ? nsa.active : (sa.active || []);
      const inUse = [...(lte.active || []).map(b => 'B' + b), ...nrActive.map(b => 'n' + b)];
      clear(summary).appendChild(card('Band overview', [
        kv([
          ['In use now', inUse.length ? h('span', inUse.map(x => badge(x, 'ok'))) : 'No band in use (no service)'],
          ['4G LTE', `${(lte.enabled || []).length} of ${(lte.supported || []).length} supported bands enabled`],
          ['5G SA / NSA', `${(sa.enabled || []).length} / ${(nsa.enabled || []).length} of ${(sa.supported || []).length} supported bands enabled`],
          adds.length && ['Supported but off', adds.join(', '), 'the hardware supports these bands but they are disabled'],
        ]),
      ], { icon: 'chip', actions }));
    }

    function chipOptions(key) {
      const d = data[key] || {};
      const prefix = key === 'lte' ? 'B' : 'n';
      const tbl = key === 'lte' ? names.lte : names.nr;
      const all = [...new Set([...(d.supported || []), ...(d.enabled || [])])].sort((a, b) => a - b);
      const active = new Set(d.active || []);
      const sup = new Set(d.supported || []);
      return all.map(b => [b, prefix + b, {
        cls: active.has(b) ? 'active-band' : (!sup.has(b) ? 'unsupported' : undefined),
        title: [tbl[b] ? tbl[b] + ' MHz' : '', active.has(b) ? 'in use now' : '', !sup.has(b) ? 'not reported as supported by the hardware' : ''].filter(Boolean).join(' · '),
      }]);
    }

    function listForm(host, key, title, prefix, desc) {
      const d = data[key] || {};
      let f;
      const allBtn = h('button.btn.btn-sm', { type: 'button', title: 'Select every supported band (save to apply)', onclick: () => {
        const cur = data[key] || {};
        f.set('bands', [...new Set([...(cur.supported || []), ...(f.values().bands || [])])].sort((a, b) => a - b));
      } }, 'Select all supported');
      const prevBtn = d.previous && d.previous.value ? button('Undo last change', async () => {
        await post('/api/cellular/bands/restore', { list: key, source: 'previous' }, { confirmTitle: `Restore previous ${title.toLowerCase()}?`,
          confirmMessage: `${title}: ${diff((data[key] || {}).enabled, d.previous.value, prefix)} (saved ${fmt.ago(d.previous.t)}).` });
        toast('Previous bands restored');
        await refresh();
      }, { small: true, icon: 'refresh' }) : null;
      f = form(host, {
        title, icon: 'chip', description: desc,
        headerActions: prevBtn ? [prevBtn] : undefined,
        actions: [allBtn],
        load: async () => ({ bands: ((data[key] || {}).enabled || []).slice() }),
        save: async (ch) => {
          const from = (data[key] || {}).enabled || [];
          await put('/api/cellular/bands', { [key]: ch.bands }, { confirmTitle: `Change ${title.toLowerCase()}?`,
            confirmMessage: `${title}: ${diff(from, ch.bands, prefix)}. The modem re-registers (a few seconds offline). If no enabled band is available where you are, there is no service until you change it back.` });
          toast('Bands saved');
          await refresh(key);
          return { bands: ((data[key] || {}).enabled || []).slice() };
        },
        fields: [{ key: 'bands', label: `Enabled (${(d.supported || []).length} supported)`, type: 'chips', options: chipOptions(key),
          validate: v => (!v || !v.length) ? 'Keep at least one band enabled' : null,
          help: d.not_in_supported && d.not_in_supported.length ? `Faded bands (${bandList(d.not_in_supported, prefix)}) are enabled in the modem configuration but not reported as supported by the hardware.` : undefined }],
      });
      forms[key] = f;
    }

    async function refresh(skip) {
      await load();
      renderSummary();
      for (const [k] of LISTS) if (k !== skip && forms[k]) forms[k].reload();
      if (prio) prio.reload();
    }

    renderSummary();
    for (const [key, title, prefix, desc] of LISTS) {
      const host = h('div');
      listsBox.appendChild(host);
      if (!data[key] || data[key].enabled === null) {
        host.appendChild(card(title, alert('info', 'The modem did not report this band list.'), { icon: 'chip' }));
        continue;
      }
      listForm(host, key, title, prefix, desc);
    }

    // ---- band priority ------------------------------------------------------------------------------
    const pr = data.priority || {};
    const orderField = (key, label, prefix, sup) => ({ key, label, type: 'text', placeholder: 'e.g. ' + sup.slice(0, 3).map(b => prefix + b).join(', '),
      help: `Most preferred first, separated by commas. Leave empty for no preference. Supported: ${bandList(sup, prefix)}`,
      validate: (v) => {
        const nums = parseOrder(v);
        if (nums.some(n => !Number.isInteger(n))) return 'Use band numbers like 66, 2, 13';
        const bad = nums.filter(n => sup.length && !sup.includes(n));
        return bad.length ? `Not supported: ${bad.join(', ')}` : null;
      } });
    const prio = form(prioBox, {
      title: 'Band priority', icon: 'chip',
      description: 'Optional order in which the modem prefers bands when several are available.',
      load: async () => ({ lte: ((data.priority || {}).lte || []).join(', '), nr: ((data.priority || {}).nr || []).join(', ') }),
      save: async (ch) => {
        const body = {};
        const msg = [];
        if ('lte' in ch) { body.lte = parseOrder(ch.lte); msg.push(`LTE: ${body.lte.length ? body.lte.map(b => 'B' + b).join(' > ') : 'no preference'}`); }
        if ('nr' in ch) { body.nr = parseOrder(ch.nr); msg.push(`5G: ${body.nr.length ? body.nr.map(b => 'n' + b).join(' > ') : 'no preference'}`); }
        await put('/api/cellular/bands/priority', body, { confirmTitle: 'Change band priority?', confirmMessage: msg.join('; ') + '.' });
        await load();
        return { lte: (data.priority.lte || []).join(', '), nr: (data.priority.nr || []).join(', ') };
      },
      fields: [
        orderField('lte', '4G LTE priority', 'B', (data.lte || {}).supported || []),
        orderField('nr', '5G priority', 'n', (data.nr_sa || {}).supported || []),
        pr.lte_legacy && { key: '_legacy', label: 'Legacy LTE priority (read-only)', type: 'readonly', format: () => bandList(pr.lte_legacy) },
      ],
    });

    // ---- carrier aggregation ------------------------------------------------------------------------
    const ca = data.ca || {};
    const caHost = h('div'), comboHost = h('div');
    caBox.append(h('div', { style: { display: 'flex', flexDirection: 'column', gap: '16px' } }, caHost,
      card('Carrier aggregation combinations', comboHost, { icon: 'chip' })));
    form(caHost, {
      title: 'Carrier aggregation', icon: 'chip',
      description: ca.readable ? 'Combine several LTE carriers for more speed. Normally on.'
        : `The modem does not report the current state (${ca.message || 'not available'}). You can still switch it.`,
      load: async () => ({ enabled: ca.enabled }),
      save: async (ch) => {
        const r = await put('/api/cellular/ca', ch, { confirmTitle: 'Change carrier aggregation?',
          confirmMessage: `Carrier aggregation ${ch.enabled ? 'on' : 'off'}. The connection drops for a moment.` });
        toast('Saved');
        return { enabled: r.enabled === null ? ch.enabled : r.enabled };
      },
      fields: [{ key: 'enabled', label: 'LTE carrier aggregation', type: 'switch' }],
    });
    // combination checker
    const supLte = (data.lte || {}).supported || [];
    const sel = (n) => h('select', { 'aria-label': `Band ${n}${n === 3 ? ' (optional)' : ''}` },
      [h('option', { value: '' }, '—'), ...supLte.map(b => h('option', { value: String(b) }, 'B' + b))]);
    const s1 = sel(1), s2 = sel(2), s3 = sel(3);
    const result = h('div.hint', 'Pick two or three bands, then check or change the combination.');
    const picked = () => [s1.value, s2.value, s3.value].filter(Boolean).map(Number);
    const check = async () => {
      const b = picked();
      if (b.length < 2) { toast('Choose at least two bands', 'bad'); return; }
      const r = await get('/api/cellular/ca/combo?bands=' + b.join(','));
      clear(result).append(r.enabled === null ? `Not reported (${r.message || 'unknown'})` : `${b.map(x => 'B' + x).join(' + ')}: `,
        r.enabled === null ? '' : badge(r.enabled ? 'enabled' : 'disabled', r.enabled ? 'ok' : 'warn'));
    };
    const setCombo = (en) => async () => {
      const b = picked();
      if (b.length < 2) { toast('Choose at least two bands', 'bad'); return; }
      await put('/api/cellular/ca/combo', { bands: b, enabled: en }, { confirmTitle: 'Change carrier aggregation combination?',
        confirmMessage: `${en ? 'Enable' : 'Disable'} the combination ${b.map(x => 'B' + x).join(' + ')}.` });
      toast('Saved');
      await check();
    };
    comboHost.append(h('p.card-desc', 'Check or switch a 2- or 3-band LTE combination (the third band is optional).'),
      h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '8px' } }, s1, s2, s3),
      h('div.form-actions', { style: { marginTop: '8px' } }, button('Check', check, { small: true }),
        button('Enable', setCombo(true), { small: true }), button('Disable', setCombo(false), { small: true })), result);

    // ---- raw EFS band preference (read-only, mod-ROM style decoder) -----------------------------------
    const efsBody = h('div');
    efsBox.appendChild(card('Modem band preference (raw)', [
      h('p.card-desc', 'Reads the modem\'s internal LTE band preference file and decodes it. Read-only: nothing is written.'), efsBody],
      { icon: 'terminal', actions: [button('Read', () => section(efsBody, () => get('/api/cellular/bands/efs'), (r) => {
        const en = new Set((data.lte || {}).enabled || []);
        const same = r.bands.length === en.size && r.bands.every(b => en.has(b));
        return [kv(r.files.map(f => [f.name, f.ok ? h('span', h('span.mono', f.hex), ' → ', bandList(f.bands)) : `not readable${f.message ? ': ' + f.message : ''}`])),
          alert(same ? 'ok' : 'warn', same ? 'Matches the enabled LTE band list.' : `Differs from the enabled LTE list (${bandList([...en])}).`)];
      }), { small: true })] }));
  },
};
