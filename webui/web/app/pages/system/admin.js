import { applyPreferences } from '../../shell.js';
// System › Administration: admin password + security question (shared with the original interface),
// sessions, settings of this interface, language & formats, and the vendor web server's login/HTTPS info.
import { h, clear } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { list } from '../../ui/list.js';
import { card, kv, badge, alert, button, modal, toast, errorBox, spinner, confirmSimple } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { generatePassword, passwordProblem, strength, notify } from './_common.js';

const IDLE_OPTIONS = [[300, '5 minutes'], [600, '10 minutes'], [900, '15 minutes'], [1800, '30 minutes'], [3600, '1 hour'],
  [7200, '2 hours'], [14400, '4 hours'], [28800, '8 hours'], [86400, '24 hours']];

function passwordCard(host, ctx) {
  let info = null;
  const banner = h('div');
  const f = form(host, {
    title: 'Admin password', icon: 'lock',
    description: ['The same password protects this interface, the original vendor interface and settings backups. '
      + 'Changing it logs out every other session. Whether the router\'s screen may show it is set under ',
    h('a', { href: '#/device/display' }, 'Display & LED'), '.'],
    saveLabel: 'Change',
    load: async () => {
      info = await get('/api/system/admin');
      clear(banner);
      if (info.default_password_in_use) banner.appendChild(alert('warn', 'The factory default password is still in use. Choose your own password to keep the router safe.'));
      return { password: '', new_password: '', confirm_password: '', security_question: info.security_question, security_answer: '' };
    },
    save: async (changes, values) => {
      const body = { confirm: true, password: values.password };
      if (values.new_password) { body.new_password = values.new_password; body.confirm_password = values.confirm_password; }
      if (changes.security_question !== undefined) body.security_question = values.security_question;
      if (values.security_answer) body.security_answer = values.security_answer;
      if (!body.new_password && body.security_question === undefined && !body.security_answer) {
        throw Object.assign(new Error('Enter a new password or a new security answer'), { field: 'new_password' });
      }
      const ok = await confirmSimple('Change login settings',
        body.new_password ? 'Change the admin password? Other open sessions will be logged out. Use the new password next time you log in here or in the original interface.'
          : 'Save the new security question and answer?', 'Change', 'primary');
      if (!ok) throw Object.assign(new Error('Cancelled'), { code: 'cancelled' });
      const res = await put('/api/system/admin/login', body, { noConfirm: true });
      if (res.password_changed) toast('Password changed. Other sessions were logged out.');
      return undefined;   // reload the form (clears the password fields)
    },
    savedMessage: 'Login settings saved',
    fields: [
      { type: 'custom', key: '_banner', full: true, noSave: true, render: () => banner },
      { key: 'password', label: 'Current password', type: 'password', required: true, autocomplete: 'current-password', full: true,
        help: 'Needed to confirm any change.' },
      { key: 'new_password', label: 'New password', type: 'password', maxLength: 32, generate: () => generatePassword(16),
        validate: (v) => passwordProblem(v, info && info.password_rules),
        help: (vals) => vals.new_password ? strength(vals.new_password) + ' · ' + ((info && info.password_rules.text) || '')
          : 'Leave empty to keep the current password. ' + ((info && info.password_rules.text) || '') },
      { key: 'confirm_password', label: 'Repeat new password', type: 'password', maxLength: 32, show: () => true,
        validate: (v, vals) => (vals.new_password && v !== vals.new_password) ? 'The two passwords do not match' : null },
      { type: 'section', label: 'Password recovery question', help: 'Used by the original interface to recover a forgotten password. The answer reveals the password, so keep it private.' },
      { key: 'security_question', label: 'Security question', type: 'select', options: async () => {
        const d = info || await get('/api/system/admin');
        return d.questions.map(([v, l]) => [v, l]);
      } },
      { key: 'security_answer', label: 'Answer', type: 'password', maxLength: 255, autocomplete: 'off',
        help: (vals) => (info && info.security_answer_set) ? 'Leave empty to keep the current answer.' + (vals.security_question === 0 ? ' Format MM/DD/YYYY.' : '')
          : 'Required: no answer is stored yet.' },
    ],
    actions: [button('Show answer & default password', async () => {
      const s = await get('/api/system/admin/secret');
      modal({ title: 'Stored secrets', body: kv([
        ['Security answer', s.security_answer ? h('span.mono', s.security_answer) : h('span.muted', 'not set')],
        ['Factory default password', s.default_password ? h('span.mono', s.default_password) : h('span.muted', 'unknown')],
      ]), actions: [{ label: 'Close', value: null }] });
    }, { kind: 'ghost', small: true, icon: 'eye' })],
  });
  return f;
}

function sessionsCard(host) {
  const l = list(host, {
    title: 'Sessions', icon: 'users',
    description: 'Browsers that are logged in to this interface right now.',
    load: () => get('/api/system/sessions'),
    refreshMs: 20000,
    columns: [
      { key: 'ip', label: 'Address', cls: 'mono' },
      { key: 'created', label: 'Logged in', format: (v) => fmt.dateTime(v) },
      { key: 'idle_s', label: 'Last activity', format: (v) => v < 60 ? 'just now' : fmt.duration(v) + ' ago' },
      { key: 'current', label: '', format: (v) => v ? badge('This browser', 'accent') : '' },
    ],
    headerActions: [button('Log out others', async () => {
      if (!(await confirmSimple('Log out other sessions', 'End every other session of this interface?', 'Log out others', 'danger'))) return;
      await post('/api/system/sessions/logout-others', {});
      toast('Other sessions ended');
      l.reload();
    }, { small: true, icon: 'logout' })],
  });
  return l;
}

function uiCard(host, ctx) {
  return form(host, {
    title: 'This interface', icon: 'system',
    description: 'Settings of this web interface only (stored on the router).',
    load: () => get('/api/settings'),
    save: async (changes) => {
      const fresh = await put('/api/settings', changes);
      if (changes.theme) {
        try { localStorage.setItem('m3ui.theme', changes.theme); } catch (_) { /* private mode */ }
        applyPreferences();
      }
      if ('router_name' in changes || 'expert_mode' in changes || 'status_poll_s' in changes) {
        toast('Saved. Reloading the interface…');
        setTimeout(() => (ctx.refreshShell ? ctx.refreshShell() : location.reload()), 900);
      }
      return fresh;
    },
    fields: [
      { key: 'router_name', label: 'Router name', type: 'text', required: true, maxLength: 32,
        help: 'Shown in the menu, the browser tab and on the login page.' },
      { key: 'session_idle_s', label: 'Log out after inactivity', type: 'select', options: IDLE_OPTIONS,
        help: 'Sessions also end 12 hours after login.' },
      { key: 'theme', label: 'Appearance', type: 'select', options: [['system', 'Follow the device setting'], ['light', 'Light'], ['dark', 'Dark']] },
      { key: 'status_poll_s', label: 'Status refresh', type: 'number', min: 2, max: 60, unit: 'seconds',
        help: 'How often the status bar and dashboard update. Lower values use more router CPU.' },
      { key: 'expert_mode', label: 'Expert mode', type: 'switch', onLabel: 'On', offLabel: 'Off', full: true,
        help: 'Shows advanced pages and settings (raw commands, engineering data). Use with care.' },
    ],
  });
}

async function formatsCard(host) {
  let opts = null;
  return form(host, {
    title: 'Language & formats', icon: 'globe',
    description: 'Used by the device screen and the original vendor interface. This interface is in English.',
    load: async () => { const d = await get('/api/system/formats'); opts = d; return d; },
    save: (changes) => put('/api/system/formats', changes),
    fields: [
      { key: 'language', label: 'Language', type: 'select', options: async () => (opts || (opts = await get('/api/system/formats'))).languages },
      { key: 'date_format', label: 'Date', type: 'select', options: async () => (opts || (opts = await get('/api/system/formats'))).options.date_format },
      { key: 'time_format', label: 'Time', type: 'select', options: async () => (opts || (opts = await get('/api/system/formats'))).options.time_format },
      { key: 'temperature_unit', label: 'Temperature', type: 'select', options: async () => (opts || (opts = await get('/api/system/formats'))).options.temperature_unit },
      { key: 'distance_unit', label: 'Distance', type: 'select', options: async () => (opts || (opts = await get('/api/system/formats'))).options.distance_unit },
      { key: 'decimal_separator', label: 'Decimal separator', type: 'select', options: async () => (opts || (opts = await get('/api/system/formats'))).options.decimal_separator },
    ],
  });
}

async function vendorCard(host, meta) {
  const body = h('div');
  host.appendChild(card('Original vendor interface', body, { icon: 'link' }));
  body.appendChild(spinner());
  const [adm, https] = await Promise.all([get('/api/system/admin').catch(e => e), get('/api/system/https').catch(e => e)]);
  clear(body);
  if (adm instanceof Error) { body.appendChild(errorBox(adm)); return; }
  const v = adm.vendor_login || {};
  const lr = v.login_required;
  const yes = (b) => b === undefined || b === null ? '' : badge(b ? 'Required' : 'Not required', b ? 'ok' : 'warn');
  body.append(
    h('p.card-desc', 'Settings of the vendor\'s own web server. They are shown for reference; the vendor software does not '
      + 'offer a safe way to change them.'),
    kv([
      ['Wrong passwords before lock-out', v.max_retries],
      ['Automatic logout', v.auto_logout_s ? fmt.duration(v.auto_logout_s) : ''],
      lr && ['Login for advanced pages', yes(lr.advanced_pages)],
      lr && ['Login for the GPS page', yes(lr.gps_page)],
      lr && ['Login for network pages', yes(lr.network_pages)],
      ['This interface', adm.this_ui ? adm.this_ui.lockout : ''],
    ]));
  if (https instanceof Error) {
    body.appendChild(h('p.hint', 'HTTPS settings: ' + https.message));
  } else {
    const c = https.certificate || {};
    body.append(h('div.form-section', h('h3', 'HTTPS')), kv([
      ['HTTPS', badge(https.enabled ? 'On' : 'Off', https.enabled ? 'ok' : 'neutral')],
      ['Port', https.port],
      ['Certificate', c.common_name || c.certificate_name || h('span.muted', 'none')],
      c.organization_name && ['Organisation', c.organization_name],
      ['Certificate validity', c.validity_days ? c.validity_days + ' days' : ''],
    ]), h('p.hint', https.note));
  }
}

export default {
  id: 'system-admin', path: '/system/admin', group: 'system', order: 10, title: 'Administration', icon: 'lock',
  async render(el, ctx) {
    const top = h('div.grid-2'), pw = h('div'), sess = h('div');
    const mid = h('div.grid-2'), ui = h('div'), fm = h('div');
    const vendor = h('div');
    top.append(pw, sess);
    mid.append(ui, fm);
    el.append(top, mid, vendor);
    passwordCard(pw, ctx);
    const s = sessionsCard(sess);
    uiCard(ui, ctx);
    formatsCard(fm);
    vendorCard(vendor, ctx.meta).catch(notify);
    return () => s.destroy();
  },
};
