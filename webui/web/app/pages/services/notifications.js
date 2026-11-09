// Services › Notifications: stealth mode, sounds, screen wake-up, software-update notifications, every alert
// type (on/off, sound, wake screen, where shown) and the alerts that are active now.
import { h, clear } from '../../ui/dom.js';
import { card, alert, badge, button, toast, spinner, errorBox, empty } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import { smartForm, isUnavailable } from '../device/_helpers.js';

const SURFACES = [['none', 'Nowhere'], ['browser', 'Web page'], ['screen', 'Router screen'], ['all', 'Web page and screen']];
const SEVERITY_TONE = { info: 'info', warning: 'warn', alert: 'warn', critical: 'bad' };

function settingsForm(host) {
  smartForm(host, {
    title: 'Alerts', icon: 'sms',
    description: 'How the router tells you about events such as low battery, new messages or SIM problems.',
    load: () => get('/api/services/notifications'),
    save: (ch) => put('/api/services/notifications', ch),
    fields: [
      { key: 'stealth', label: 'Stealth mode', type: 'switch',
        help: 'Turns the status light and all sounds off. Critical alerts such as overheating are silent too.' },
      { key: 'audio', label: 'Alert sounds', type: 'switch', disabled: (v) => !!v.stealth, help: 'Master switch for the sounds below.' },
      { key: 'wake_on_notification', label: 'Wake the screen for alerts', type: 'switch', disabled: (v) => !!v.stealth },
      { type: 'section', label: 'Software updates' },
      { key: 'initial_update', label: 'Notify about available updates', type: 'switch' },
      { key: 'vital_update', label: 'Notify about important updates', type: 'switch' },
    ],
  });
}

function eventsCard(host) {
  const body = h('div', spinner());
  const save = h('button.btn.btn-primary', { type: 'button', disabled: true }, 'Save changes');
  const reset = h('button.btn', { type: 'button', disabled: true }, 'Discard');
  host.appendChild(card('Alert types', [
    h('p.card-desc', 'Choose per alert whether it is on, makes a sound, wakes the screen and where it is shown. '
      + 'The vendor stores the whole table at once, so all changes are saved together.'),
    body, h('div.form-foot', h('div.form-actions', save, reset)),
  ], { icon: 'sms' }));
  let data = null;
  const changes = new Map();

  const val = (e, k) => (changes.has(e.id) && k in changes.get(e.id) ? changes.get(e.id)[k] : e[k]);
  const setVal = (e, k, v) => {
    const c = Object.assign({}, changes.get(e.id) || {});
    if (v === e[k]) delete c[k]; else c[k] = v;
    if (Object.keys(c).length) changes.set(e.id, c); else changes.delete(e.id);
    save.disabled = reset.disabled = !changes.size;
  };

  function render() {
    clear(body);
    const byId = new Map(data.events.map(e => [e.id, e]));
    const sections = data.groups.map((g) => {
      const evs = g.ids.map(id => byId.get(id)).filter(Boolean);
      const allSound = evs.every(e => val(e, 'audio'));
      const groupSound = h('label.check', { title: 'Sound for every alert in this group' },
        h('input', { type: 'checkbox', checked: allSound, onchange: (ev) => { evs.forEach(e => setVal(e, 'audio', ev.target.checked)); render(); } }),
        h('span', 'Sound for all'));
      return h('div.table-wrap', { style: { marginBottom: '14px' } },
        h('table.table', { style: { tableLayout: 'fixed' } },
          h('thead', h('tr', h('th', { style: { width: '38%' } }, g.label, ' ', groupSound), h('th', { style: { width: '12%' } }, 'On'),
            h('th', { style: { width: '12%' } }, 'Sound'), h('th', { style: { width: '14%' } }, 'Wake screen'), h('th', 'Shown on'))),
          h('tbody', evs.map(e => h('tr',
            h('td', { dataset: { label: 'Alert' } }, e.label, changes.has(e.id) ? h('span.badge.badge-info', ' changed') : null),
            ['enabled', 'audio', 'wake'].map(k => h('td', { dataset: { label: { enabled: 'On', audio: 'Sound', wake: 'Wake screen' }[k] } },
              h('input', { type: 'checkbox', checked: !!val(e, k), 'aria-label': `${e.label}: ${k}`,
                onchange: (ev) => { setVal(e, k, ev.target.checked); render(); } }))),
            h('td', { dataset: { label: 'Shown on' } }, h('select', { 'aria-label': `${e.label}: shown on`,
              onchange: (ev) => { setVal(e, 'surfaces', SURFACES[ev.target.selectedIndex][0]); render(); } },
              SURFACES.map(([v, l]) => h('option', { selected: val(e, 'surfaces') === v }, l)))))))));
    });
    body.append(...sections);
  }

  async function load() {
    clear(body).appendChild(spinner());
    try { data = await get('/api/services/notifications/events'); changes.clear(); save.disabled = reset.disabled = true; render(); } catch (e) {
      clear(body).appendChild(isUnavailable(e) ? alert('info', e.message) : errorBox(e, load));
    }
  }
  save.addEventListener('click', async () => {
    const events = Array.from(changes.entries()).map(([id, c]) => Object.assign({ id }, c));
    save.disabled = true; save.classList.add('busy');
    try { data = await put('/api/services/notifications/events', { events }); changes.clear(); toast('Saved'); render(); } catch (e) {
      if (e.code !== 'cancelled') toast(e.message, 'bad', 6000);
    } finally { save.classList.remove('busy'); save.disabled = reset.disabled = !changes.size; }
  });
  reset.addEventListener('click', () => { changes.clear(); save.disabled = reset.disabled = true; render(); });
  load();
}

function currentCard(host) {
  const body = h('div', spinner());
  host.appendChild(card('Active alerts', body, { icon: 'warn' }));
  async function load() {
    try {
      const cur = await get('/api/services/notifications/current');
      clear(body);
      if (!cur.length) { body.appendChild(empty('No alerts right now.')); return; }
      body.appendChild(h('div.table-wrap', h('table.table', h('thead', h('tr', h('th', 'Alert'), h('th', 'Type'), h('th', 'Severity'), h('th.actions', ''))),
        h('tbody', cur.map(c => h('tr', h('td', { dataset: { label: 'Alert' } }, c.label), h('td', { dataset: { label: 'Type' } }, c.category || '—'),
          h('td', { dataset: { label: 'Severity' } }, badge(c.severity, SEVERITY_TONE[c.severity] || 'neutral')),
          h('td.actions', button('Dismiss', async () => { await post(`/api/services/notifications/current/${encodeURIComponent(c.id)}/ack`, {}); toast('Dismissed'); await load(); },
            { small: true }))))))));
      body.appendChild(h('p.hint', 'Some alerts come back while their cause remains (for example "No SIM card").'));
    } catch (e) { clear(body).appendChild(isUnavailable(e) ? alert('info', e.message) : errorBox(e, load)); }
  }
  load();
}

export default {
  id: 'services-notifications', path: '/services/notifications', group: 'services', order: 30, title: 'Notifications', icon: 'sms',
  feature: 'services.notifications',
  render(el) {
    const top = h('div.grid-2');
    const left = h('div'), right = h('div');
    top.append(left, right);
    const bottom = h('div');
    el.append(top, bottom);
    settingsForm(left);
    currentCard(right);
    eventsCard(bottom);
  },
};
