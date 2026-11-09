// Cellular > Messages: inbox / sent / outbox, read and mark as read, compose with a GSM-7 / UCS-2 counter,
// delete one, several or all, and the address book.
import { h, clear } from '../../ui/dom.js';
import { card, badge, alert, toast, modal, confirmSimple, empty } from '../../ui/widgets.js';
import { list } from '../../ui/list.js';
import { get, post, put, del } from '../../api.js';
import { dash, problem, smsCount, smallButton, localTime } from './_sim_ui.js';

const BOXES = [['inbox', 'Inbox'], ['sent', 'Sent'], ['outbox', 'Outbox'], ['contacts', 'Contacts']];

export default {
  id: 'cellular-sms', path: '/cellular/sms', group: 'cellular', order: 80, title: 'Messages', icon: 'sms', feature: 'sms',
  async render(el, ctx) {
    let tab = (ctx && ctx.query && ctx.query.box) || 'inbox';
    let contacts = [];
    let status = { available: true, unread: 0, max_text: 459 };
    let lastUnread = null;
    const note = h('div'), tabs = h('div.tabs', { role: 'tablist' }), body = h('div');
    const counts = h('span.card-sub');
    const root = card('Messages', [note, tabs, body], { icon: 'sms', actions: [
      smallButton('New message', () => compose(), { kind: 'primary', icon: 'plus' }),
      smallButton('Refresh', () => show(tab), { icon: 'refresh' })] });
    el.append(root);

    async function loadContacts() {
      try { contacts = (await get('/api/sms/contacts')).contacts || []; } catch (_) { contacts = []; }
    }

    async function loadStatus() {
      try { status = await get('/api/sms/status'); } catch (_) { /* keep defaults */ }
      clear(note);
      try {
        const sim = await get('/api/sim/status');
        if (!sim.present) note.appendChild(alert('info', 'No SIM card: messages cannot be sent or received until a SIM is inserted.'));
        else if (sim.state === 'pin_locked' || sim.state === 'puk_locked') {
          note.appendChild(alert('warn', 'The SIM card is locked. ', h('a', { href: '#/cellular/sim' }, 'Unlock it'), ' to use messages.'));
        }
      } catch (_) { /* SIM page will explain */ }
    }

    function renderTabs() {
      clear(tabs).append(...BOXES.map(([k, label]) => h('button.tab' + (k === tab ? '.active' : ''), {
        type: 'button', role: 'tab', 'aria-selected': String(k === tab), onclick: () => show(k) },
      label, k === 'inbox' && status.unread ? h('span', ' ', badge(String(status.unread), 'info')) : '')));
    }

    function who(m) {
      return m.contact ? h('span', h('b', m.contact), h('small.hint', ` ${m.address}`)) : h('span.mono', m.address || dash);
    }

    function openMessage(m) {
      const actions = [];
      if (m.box === 'inbox' && m.address) actions.push({ label: 'Reply', kind: 'primary', onclick: (close) => { close(true); compose(m.address); } });
      actions.push({ label: 'Delete', kind: 'danger', onclick: async (close) => { close(true); await removeIds(m.box, [m.id], 'this message'); } });
      actions.push({ label: 'Close', value: true });
      modal({ title: m.box === 'inbox' ? 'Message' : 'Sent message', actions, body: h('div',
        h('p', h('span.muted', m.box === 'inbox' ? 'From ' : 'To '), who(m)),
        h('p.muted', localTime(m.time, m.time_text)),
        h('div.pre', m.text || '(empty)')) });
      if (m.unread) {
        post(`/api/sms/messages/${m.box}/${m.id}/read`, { read: true }).then(() => { m.unread = false; show(tab, true); }).catch(() => {});
      }
    }

    async function removeIds(box, ids, label) {
      if (!(await confirmSimple('Delete', `Delete ${label}? This cannot be undone.`, 'Delete', 'danger'))) return;
      try {
        if (ids.length === 1) await del(`/api/sms/messages/${box}/${ids[0]}`);
        else await post('/api/sms/messages/delete', { box, ids });
        toast('Deleted');
      } catch (e) { if (e.code !== 'cancelled') toast(e.message, 'bad', 6000); }
      show(tab);
    }

    async function showBox(box, quiet) {
      if (!quiet) clear(body).appendChild(h('div.loading', h('span.spinner'), h('span', 'Loading…')));
      let r;
      try { r = await get('/api/sms/messages?box=' + box); } catch (e) { clear(body).appendChild(problem(e, () => show(box))); return; }
      const rows = r.messages || [];
      const selected = new Set();
      const delSel = smallButton('Delete selected', async () => removeIds(box, Array.from(selected), `${selected.size} message(s)`),
        { disabled: true });
      const delAll = smallButton('Delete all', async () => {
        if (!(await confirmSimple('Delete all', `Delete all ${rows.length} messages in ${box}? This cannot be undone.`, 'Delete all', 'danger'))) return;
        try { await post('/api/sms/messages/delete', { box, all: true }); toast('All deleted'); } catch (e) { if (e.code !== 'cancelled') toast(e.message, 'bad', 6000); }
        show(tab);
      }, { disabled: !rows.length });
      const markAll = box === 'inbox' && r.unread ? smallButton('Mark all read', async () => {
        for (const m of rows.filter(x => x.unread)) await post(`/api/sms/messages/inbox/${m.id}/read`, { read: true });
        toast('Marked as read'); show(tab);
      }) : null;
      const sync = () => { delSel.disabled = !selected.size; };
      clear(body);
      counts.textContent = `${r.count} message${r.count === 1 ? '' : 's'}${box === 'inbox' && r.unread ? `, ${r.unread} unread` : ''}`;
      body.append(h('div.form-actions', counts, markAll, delSel, delAll));
      if (!rows.length) { body.appendChild(empty(box === 'inbox' ? 'No messages received.' : 'No messages here.')); return; }
      body.appendChild(h('div.table-wrap', h('table.table',
        h('thead', h('tr', h('th', ''), h('th', box === 'inbox' ? 'From' : 'To'), h('th', 'Message'), h('th', 'Time'), h('th.actions', ''))),
        h('tbody', rows.map(m => h('tr', { style: { fontWeight: m.unread ? '700' : '' } },
          h('td', { dataset: { label: 'Select' } }, h('label.check', h('input', { type: 'checkbox', 'aria-label': 'Select message', onchange: (e) => {
            e.target.checked ? selected.add(m.id) : selected.delete(m.id); sync(); } }))),
          h('td', { dataset: { label: box === 'inbox' ? 'From' : 'To' } }, who(m), m.unread ? h('span', ' ', badge('new', 'info')) : ''),
          h('td', { dataset: { label: 'Message' }, style: { cursor: 'pointer' }, onclick: () => openMessage(m) },
            (m.text || '').length > 90 ? m.text.slice(0, 90) + '…' : (m.text || h('span.muted', '(empty)'))),
          h('td', { dataset: { label: 'Time' } }, localTime(m.time, m.time_text)),
          h('td.actions', h('button.btn.btn-sm.btn-ghost', { type: 'button', onclick: () => openMessage(m) }, 'Open'),
            box === 'inbox' && m.address ? h('button.btn.btn-sm.btn-ghost', { type: 'button', onclick: () => compose(m.address) }, 'Reply') : null,
            h('button.icon-btn.danger', { type: 'button', title: 'Delete', 'aria-label': 'Delete', onclick: () => removeIds(box, [m.id], 'this message') }, '×'))))))));
    }

    function showContacts() {
      clear(body);
      counts.textContent = '';
      const host = h('div');
      body.appendChild(host);
      list(host, {
        title: 'Address book', icon: 'users', max: 100, addLabel: 'Add contact', addTitle: 'New contact', editTitle: 'Edit contact',
        description: 'Names stored on the router; received messages from these numbers show the name.',
        load: async () => { const r = await get('/api/sms/contacts'); contacts = r.contacts; return r.contacts; },
        columns: [{ key: 'name', label: 'Name' }, { key: 'address', label: 'Phone number', cls: 'mono' }],
        fields: [
          { key: 'name', label: 'Name', type: 'text', required: true, maxLength: 40 },
          { key: 'address', label: 'Phone number', type: 'text', required: true, maxLength: 24, inputmode: 'tel',
            pattern: /^\+?[0-9 -]{2,24}$/, patternMsg: 'Digits, optionally starting with +' },
        ],
        add: (row) => post('/api/sms/contacts', { name: row.name, address: row.address }),
        update: (row, old) => put('/api/sms/contacts/' + old.id, { name: row.name, address: row.address }),
        remove: (row) => del('/api/sms/contacts/' + row.id),
        describe: (row) => `"${row.name}"`,
        rowActions: (row) => [h('button.btn.btn-sm.btn-ghost', { type: 'button', onclick: () => compose(row.address) }, 'Message')],
      });
    }

    async function show(k, quiet) {
      tab = k;
      if (!quiet) await loadStatus();
      renderTabs();
      if (k === 'contacts') showContacts(); else await showBox(k, quiet);
    }

    function compose(to) {
      const toInput = h('input', { type: 'text', inputmode: 'tel', autocomplete: 'off', placeholder: '+15550100', value: to || '', list: 'm3ui-sms-contacts' });
      const dl = h('datalist#m3ui-sms-contacts', contacts.map(c => h('option', { value: c.address }, c.name)));
      const text = h('textarea', { rows: 6, maxlength: status.max_text || 459, placeholder: 'Message', spellcheck: 'true' });
      const counter = h('div.field-help');
      const errTo = h('div.field-error'), errText = h('div.field-error');
      const sendBtn = h('button.btn.btn-primary', { type: 'button' }, 'Send');
      function update() {
        const c = smsCount(text.value);
        const max = status.max_text || 459;
        clear(counter).append(`${Array.from(text.value).length} / ${max} characters · ${c.encoding} · ${c.parts} part${c.parts === 1 ? '' : 's'} · ${c.left} left in this part`);
        if (c.parts > 1) counter.append(h('div.hint', 'Long messages are sent in several parts and joined again by the receiving phone (each part may be charged).'));
        if (!c.plain) counter.append(h('div', { style: { color: 'var(--bad)' } }, 'This router can only send plain text (letters, digits, common punctuation); remove accented letters, emoji and other symbols.'));
        sendBtn.disabled = !text.value.trim() || !toInput.value.trim() || !c.plain;
      }
      text.addEventListener('input', update); toInput.addEventListener('input', update);
      const bodyEl = h('div.form-grid',
        h('div.field.field-full', h('label.field-label', 'To'), h('div.field-control', toInput, dl), errTo),
        h('div.field.field-full', h('label.field-label', 'Message'), h('div.field-control', text), counter, errText));
      const m = modal({ title: 'New message', body: bodyEl, actions: [{ label: 'Cancel', value: false }, sendBtn] });
      sendBtn.addEventListener('click', async () => {
        errTo.textContent = ''; errText.textContent = '';
        sendBtn.disabled = true; sendBtn.classList.add('busy');
        try {
          const r = await post('/api/sms/send', { to: toInput.value.trim(), text: text.value });
          toast(r.parts > 1 ? `Sent (${r.parts} parts)` : 'Sent');
          m.close(true);
          show('sent');
        } catch (e) {
          if (e.field === 'to') errTo.textContent = e.message;
          else if (e.field === 'text') errText.textContent = e.message;
          else if (e.code !== 'cancelled') toast(e.message || String(e), 'bad', 6000);
        } finally { sendBtn.classList.remove('busy'); update(); }
      });
      update();
      setTimeout(() => (to ? text : toInput).focus(), 40);
    }

    await loadContacts();
    await show(tab);
    if (ctx && ctx.query && ctx.query.to) compose(ctx.query.to);

    // live: reload the inbox when the unread counter (status bar polling) changes
    const unsub = ctx && ctx.store ? ctx.store.subscribe((s) => {
      const n = s.status ? s.status.sms_unread : null;
      if (lastUnread !== null && n !== lastUnread && tab === 'inbox') { status.unread = n; show('inbox', true); }
      lastUnread = n;
    }) : null;
    return () => { if (unsub) unsub(); };
  },
};
