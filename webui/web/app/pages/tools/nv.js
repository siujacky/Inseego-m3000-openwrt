// Tools › NV & EFS viewer (expert, read-only): modem EFS files with hex/ASCII view and decoders, named NV items.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, alert, button } from '../../ui/widgets.js';
import { get } from '../../api.js';
import { section, field, setFieldError, pre, copyButton, downloadLink, b64ToBytes, hexdump, friendlyError } from './_helpers.js';
import * as fmt from '../../ui/format.js';

function decodedView(d) {
  if (!d) return null;
  const parts = [h('p', { style: { margin: '0 0 8px' } }, h('strong', d.summary || d.kind))];
  if (d.bands) parts.push(h('div.chips', d.bands.length ? d.bands.map(b => h('span.chip.on', (d.kind === 'nr_bands' ? 'n' : 'B') + b)) : h('span.muted', 'no bands')));
  if (d.text !== undefined) parts.push(pre(d.text));
  if (d.inflated_b64) {
    const bytes = b64ToBytes(d.inflated_b64);
    parts.push(h('details', h('summary', `Decompressed content (${fmt.bytes(d.inflated_size)})`), pre(hexdump(bytes, 8192))));
  }
  return h('div', parts);
}

export default {
  id: 'tools-nv', path: '/tools/nv', group: 'tools', order: 50, title: 'NV & EFS viewer', expert: true,
  render(el) {
    el.appendChild(h('div.page-head', h('div', h('h1', 'NV & EFS viewer'),
      h('p', 'Look at the modem\'s own configuration files (EFS) and NV items: band preferences, lock flags, the factory backup and RF items. Read-only: nothing on this page writes to the modem.'))));

    // -- EFS ---------------------------------------------------------------------------------------------------
    const path = h('input', { type: 'text', dataset: { key: 'path' }, placeholder: '/nv/item_files/modem/mmode/lte_bandpref', spellcheck: 'false', autocomplete: 'off',
      style: { fontFamily: 'var(--mono)' }, onkeydown: (e) => { if (e.key === 'Enter') readBtn.click(); } });
    const max = h('input', { type: 'number', dataset: { key: 'max' }, min: 1, max: 262144, value: 65536, inputmode: 'numeric' });
    const nvnum = h('input', { type: 'number', dataset: { key: 'nvnum' }, min: 0, max: 999999, placeholder: 'e.g. 60002', inputmode: 'numeric' });
    const presets = h('div.chips');
    const result = h('div');
    const efsForm = h('div');
    const readBtn = button('Read', () => readEfs(path.value.trim(), Number(max.value) || 65536), { kind: 'primary', icon: 'download' });

    async function readEfs(p, m) {
      setFieldError(efsForm, null);
      if (!p) { setFieldError(efsForm, 'path', 'Enter an EFS path'); return; }
      path.value = p;
      clear(result).appendChild(h('div.loading', h('span.spinner'), h('span', 'Reading ' + p + ' …')));
      try {
        const d = await get(`/api/expert/efs?path=${encodeURIComponent(p)}&max=${m}`);
        const bytes = b64ToBytes(d.data_b64);
        let full = false;
        const dump = pre(hexdump(bytes, 4096));
        const more = bytes.length > 4096 ? h('button.btn.btn-sm', { type: 'button', onclick: () => { full = !full; dump.textContent = hexdump(bytes, full ? bytes.length : 4096); more.textContent = full ? 'Show less' : 'Show everything'; } }, 'Show everything') : null;
        const dl = `/api/expert/efs/download?path=${encodeURIComponent(p)}&max=${m}`;
        clear(result).appendChild(card(p, [
          d.preset ? h('p.card-desc', d.preset.label + ' — ' + d.preset.help) : null,
          kv([['Size', `${d.size} bytes`], ['SHA-256', h('span.mono', d.sha256)], ['Read time', `${d.elapsed_ms} ms`]]),
          d.possibly_truncated ? alert('warn', `The file filled the ${d.max}-byte limit and may be longer; raise "Maximum bytes" to read more.`) : null,
          h('div', { style: { marginTop: '12px' } }, decodedView(d.decoded)),
          h('div', { style: { marginTop: '12px' } }, h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px', flexWrap: 'wrap' } },
            h('strong', 'Hex / ASCII'), more, copyButton(() => dump.textContent, 'Copy dump')), dump),
        ], { icon: 'chip', actions: [downloadLink(dl, 'Download', p.replace(/^\//, '').replace(/[^A-Za-z0-9_.-]+/g, '_') + '.bin')] }));
      } catch (e) {
        clear(result);
        if (e.field) { setFieldError(efsForm, e.field === 'max' ? 'max' : 'path', e.message); return; }
        const b = friendlyError(e); if (b) result.appendChild(b);
      }
    }

    efsForm.append(h('div.form-grid',
      h('div.field-full', field('EFS path', path, 'Absolute path of a modem file, e.g. one of the presets below.')),
      field('Maximum bytes', max, 'Up to 262144 (256 KB).'),
      field('NV item number', h('div.input-group', nvnum, h('button.btn', { type: 'button', onclick: () => {
        const n = String(nvnum.value || '').trim();
        if (!/^\d{1,6}$/.test(n)) { setFieldError(efsForm, 'nvnum', 'Enter an NV item number'); return; }
        readEfs('/nvm/num/' + n, 4096);
      } }, 'Read NV')), 'Reads the NV item as the EFS file /nvm/num/<number>.')),
      h('div.form-foot', readBtn, downloadLink('/api/expert/efs/backup', 'Back up all presets (ZIP)', 'efs-backup.zip')));
    el.append(card('Modem files (EFS)', [efsForm, h('div', { style: { marginTop: '14px' } }, h('div.field-label', 'Presets'), presets)], { icon: 'chip' }), result);

    section(presets, () => get('/api/expert/efs/presets'), (d) => [
      ...d.presets.map(p => h('button.chip', { type: 'button', title: p.path + '\n' + p.help, onclick: () => readEfs(p.path, p.max) }, p.label)),
      h('p.hint', { style: { flexBasis: '100%' } }, 'Never shown: ' + d.blocked.map(b => `NV ${b.nv} (${b.reason})`).join(', ') + '.'),
    ]);

    // -- named NV items ----------------------------------------------------------------------------------------
    const nvHost = h('div');
    const nvOut = h('div');
    el.append(card('Named NV items', [h('p.card-desc', 'Vendor NV items read with the vendor NV tool (by name).'), nvHost, nvOut], { icon: 'sim' }));
    section(nvHost, () => get('/api/expert/nv'), (d) => {
      const sel = h('select', { 'aria-label': 'NV item' }, d.items.map(it => h('option', { value: it.name }, `${it.label} — ${it.name} (${it.id})`)));
      const idx = h('input', { type: 'number', min: 0, max: 255, placeholder: 'optional', inputmode: 'numeric' });
      async function readOne(name, index) {
        const q = index !== '' && index !== undefined && index !== null ? `?index=${encodeURIComponent(index)}` : '';
        return get('/api/expert/nv/' + encodeURIComponent(name) + q);
      }
      const one = button('Read', async () => {
        clear(nvOut).appendChild(h('div.loading', h('span.spinner'), h('span', 'Reading…')));
        try {
          const r = await readOne(sel.value, idx.value);
          clear(nvOut).appendChild(kv([['Item', `${r.label} (${r.name}, NV ${r.id})`], ['Value', h('span.mono', r.value)], ['Meaning', r.meaning], ['Raw output', pre(r.output)]]));
        } catch (e) { clear(nvOut); const b = friendlyError(e); if (b) nvOut.appendChild(b); }
      }, { kind: 'primary' });
      const all = button('Read all', async () => {
        clear(nvOut).appendChild(h('div.loading', h('span.spinner'), h('span', 'Reading every item…')));
        const rows = [];
        for (const it of d.items) {
          try { const r = await readOne(it.name); rows.push([it.label, h('span.mono', r.value), r.meaning !== r.value ? r.meaning : '']); }
          catch (e) { rows.push([it.label, badge(e.status === 503 ? 'not available' : 'error', 'warn'), e.message]); }
        }
        clear(nvOut).appendChild(kv(rows));
      });
      return [h('div.form-grid', field('Item', sel), field('Index (array items)', idx)), h('div.form-foot', one, all)];
    });
  },
};
