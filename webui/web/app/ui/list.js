// Editable list/table (port forwards, APN profiles, MAC lists, routes, reserved IPs, SMS …).
//
// list(container, {
//   title, description, icon,
//   load: async () => rows,                 // array of objects
//   columns: [{key, label, format(v,row) -> string|Node, cls}],
//   fields: [...form fields for add/edit...],  // omit for read-only tables
//   add: async (row) => {}, update: async (row, old) => {}, remove: async (row) => {},
//   rowKey: (row) => id, max: 20, emptyText: 'Nothing here yet',
//   rowActions: (row, reload) => [Nodes], headerActions: [Nodes], refreshMs: 0
// })
import { h, clear } from './dom.js';
import { icon } from './icons.js';
import { card, errorBox, spinner, toast, modal, confirmSimple, empty } from './widgets.js';
import { form } from './form.js';

export function list(container, cfg) {
  let rows = [];
  let timer = null;
  const body = h('div.list-body');
  const addBtn = cfg.add && cfg.fields ? h('button.btn.btn-primary.btn-sm', { type: 'button', onclick: () => edit(null) }, icon('plus'), h('span', cfg.addLabel || 'Add')) : null;
  const root = card(cfg.title, [cfg.description && h('p.card-desc', cfg.description), body],
    { icon: cfg.icon, actions: [...(cfg.headerActions || []), addBtn].filter(Boolean) });
  clear(container).appendChild(root);

  function cell(c, r) {
    const v = r[c.key];
    const out = c.format ? c.format(v, r) : (v === undefined || v === null || v === '' ? '—' : String(v));
    return h('td' + (c.cls ? '.' + c.cls : ''), { dataset: { label: c.label } }, out);
  }

  function render() {
    clear(body);
    if (addBtn) addBtn.disabled = cfg.max !== undefined && rows.length >= cfg.max;
    if (!rows.length) { body.appendChild(empty(cfg.emptyText || 'Nothing here yet.')); return; }
    const hasActions = cfg.update || cfg.remove || cfg.rowActions;
    body.appendChild(h('div.table-wrap', h('table.table',
      h('thead', h('tr', cfg.columns.map(c => h('th', c.label)), hasActions && h('th.actions', ''))),
      h('tbody', rows.map(r => h('tr', cfg.columns.map(c => cell(c, r)), hasActions && h('td.actions',
        cfg.rowActions && cfg.rowActions(r, reload),
        cfg.update && cfg.fields && h('button.icon-btn', { type: 'button', title: 'Edit', 'aria-label': 'Edit', onclick: () => edit(r) }, icon('edit')),
        cfg.remove && h('button.icon-btn.danger', { type: 'button', title: 'Delete', 'aria-label': 'Delete', onclick: () => remove(r) }, icon('trash')))))))));
    if (cfg.max) body.appendChild(h('p.hint', `${rows.length} of ${cfg.max}`));
  }

  function edit(row) {
    const host = h('div');
    const m = modal({ title: row ? (cfg.editTitle || 'Edit') : (cfg.addTitle || 'Add'), body: host, wide: cfg.wideForm });
    form(host, {
      fields: cfg.fields,
      load: async () => Object.assign({}, cfg.defaults || {}, row || {}),
      alwaysDirty: !row,
      savedMessage: row ? 'Saved' : 'Added',
      saveLabel: row ? 'Save' : 'Add',
      save: async (changes, values) => {
        if (row) await cfg.update(Object.assign({}, row, values), row); else await cfg.add(values);
        m.close(true);
        await reload();
        return values;
      },
    });
  }

  async function remove(row) {
    const label = cfg.describe ? cfg.describe(row) : 'this entry';
    if (!(await confirmSimple('Delete', `Delete ${label}?`, 'Delete', 'danger'))) return;
    try { await cfg.remove(row); toast('Deleted'); await reload(); } catch (e) { if (e.code !== 'cancelled') toast(e.message, 'bad', 6000); }
  }

  async function reload() {
    if (!rows.length) { clear(body).appendChild(spinner()); }
    try { rows = (await cfg.load()) || []; render(); }
    catch (e) { clear(body).appendChild(errorBox(e, reload)); }
  }

  reload();
  if (cfg.refreshMs) timer = setInterval(() => { if (!document.hidden) reload(); }, cfg.refreshMs);
  return { reload, destroy: () => timer && clearInterval(timer), rows: () => rows, el: root };
}
