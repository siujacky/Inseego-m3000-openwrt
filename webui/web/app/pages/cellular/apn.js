// Cellular > APN: the internet APN in use, saved APN profiles per SIM (add / edit / use / delete / restore),
// presets from the bundled APN database, the carrier suggestion for the inserted SIM, APN failback, and
// (expert) modem data profiles, per-technology profiles and PDN parameters.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, alert, toast, modal, confirmSimple } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { list } from '../../ui/list.js';
import { get, put, post, del } from '../../api.js';
import { dash, has, problem, section, table, revealSecret, smallButton, yesNo } from './_sim_ui.js';

const AUTH = [['none', 'None'], ['pap', 'PAP'], ['chap', 'CHAP'], ['pap_chap', 'PAP or CHAP']];
const AUTH3 = AUTH.slice(0, 3);
const PDP = [['ipv4v6', 'IPv4 and IPv6'], ['ipv4', 'IPv4 only'], ['ipv6', 'IPv6 only']];
const LABEL = (opts, v) => { const o = opts.find(x => x[0] === v); return o ? o[1] : (v || dash); };
const APN_PATTERN = /^[A-Za-z0-9]([A-Za-z0-9._-]{0,61}[A-Za-z0-9])?$/;
const APN_FIELD = { key: 'apn', label: 'APN', type: 'text', required: true, maxLength: 62, pattern: APN_PATTERN,
  patternMsg: 'Letters, digits, dots and hyphens only', placeholder: 'internet' };
const CHANGE_MSG = 'The mobile data connection restarts with the new settings. Devices lose internet for a few seconds; '
  + 'a wrong APN leaves the router without internet until it is corrected.';

// Modal search over /api/apn/presets.
function pickPreset(onPick) {
  const q = h('input', { type: 'search', placeholder: 'Operator, APN or MCC-MNC', autocomplete: 'off', spellcheck: 'false' });
  const mine = h('input', { type: 'checkbox' });
  const results = h('div');
  let timer = null;
  let m = null;
  async function search() {
    const params = new URLSearchParams();
    if (q.value.trim()) params.set('q', q.value.trim());
    if (mine.checked) params.set('sim', '1');
    params.set('limit', '200');
    try {
      const r = await get('/api/apn/presets?' + params.toString());
      clear(results).appendChild(table([
        { key: 'operator', label: 'Operator', format: (v, p) => h('span', v, p.gid1_name ? h('small.hint', ` · ${p.gid1_name}`) : '') },
        { key: 'mccmnc', label: 'MCC-MNC', format: (v, p) => `${p.mcc}-${p.mnc}`, mono: true },
        { key: 'apn', label: 'APN', mono: true, format: (v, p) => h('span', v, p.gid1_match ? h('span', ' ', badge('your SIM', 'ok')) : '') },
        { key: 'x', label: '', actions: true, format: (v, p) => smallButton('Use', async () => { onPick(p); m && m.close(true); }, { kind: 'primary' }) },
      ], r.presets, { empty: mine.checked ? 'No preset matches the inserted SIM.' : 'No preset matches.' }));
      if (r.count > r.presets.length) results.appendChild(h('p.hint', `${r.count} matches; refine the search to see more.`));
    } catch (e) { clear(results).appendChild(problem(e)); }
  }
  q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 250); });
  mine.addEventListener('change', search);
  m = modal({ title: 'APN presets', wide: true, actions: [{ label: 'Close', value: false }], body: h('div',
    h('p.card-desc', 'Presets come from the APN databases of the stock firmware and of community firmware builds. Check with your carrier if in doubt.'),
    h('div.field', q), h('label.check', mine, h('span', 'Only my SIM card’s network')), results) });
  search();
}

function presetButton(set) {
  return { key: '_preset', type: 'custom', noSave: true, full: true,
    render: (values, setv) => h('div', smallButton('Choose from presets', async () => pickPreset((p) => {
      setv('apn', p.apn);
      if ('name' in values && !values.name) setv('name', p.operator.slice(0, 31));
      setv('auth', p.auth || 'none');
      setv('username', p.username || '');
      if (p.password) setv('password', p.password);
    }), { icon: 'globe' })) };
}

function profileFields(withPresets) {
  return [
    withPresets && presetButton(),
    { key: 'name', label: 'Profile name', type: 'text', required: true, maxLength: 31 },
    APN_FIELD,
    { key: 'auth', label: 'Authentication', type: 'select', options: AUTH },
    { key: 'username', label: 'User name', type: 'text', maxLength: 32, show: (v) => v.auth && v.auth !== 'none',
      validate: (x, v) => v.auth !== 'none' && !x ? 'Required for PAP/CHAP' : null },
    { key: 'password', label: 'Password', type: 'password', maxLength: 32, show: (v) => v.auth && v.auth !== 'none',
      placeholder: 'Leave empty to keep the saved one' },
    { key: 'pdp', label: 'IP type', type: 'select', options: PDP },
  ];
}

export default {
  id: 'cellular-apn', path: '/cellular/apn', group: 'cellular', order: 60, title: 'APN', icon: 'globe',
  async render(el, ctx) {
    const expert = !!(ctx && ctx.session && ctx.session.expert_mode);
    const top = h('div'), activeBox = h('div'), suggestBox = h('div'), listBox = h('div'), failBox = h('div');
    const modemBox = h('div'), techBox = h('div'), pdnBox = h('div');
    el.append(top, h('div.grid-2', activeBox, suggestBox), listBox, failBox);
    if (expert) el.append(modemBox, techBox, pdnBox);

    // ---- APN in use ---------------------------------------------------------------------------
    let activeForm = null;
    async function loadActive() {
      const a = await get('/api/apn/active');
      return { apn: a.apn, auth: a.auth === 'pap_chap' ? 'chap' : a.auth, username: a.username, password: '',
        _previous: a.previous, _password_set: a.password_set };
    }
    activeForm = form(activeBox, {
      title: 'Internet APN in use', icon: 'globe',
      description: 'The access point name the modem uses for the internet connection. Your carrier tells you which one to use.',
      load: loadActive,
      save: async (ch) => {
        const body = {};
        for (const k of ['apn', 'auth', 'username', 'password']) if (k in ch && !(k === 'password' && ch.password === '')) body[k] = ch[k];
        if (!Object.keys(body).length) return undefined;
        await put('/api/apn/active', body, { confirmTitle: 'Change the internet APN?', confirmMessage: CHANGE_MSG });
        return loadActive();
      },
      headerActions: [smallButton('Show password', () => revealSecret('/api/apn/active/secret'))],
      actions: [smallButton('Restore previous', async () => {
        const vals = activeForm.values();
        if (!vals._previous) { toast('No earlier APN recorded', 'bad'); return; }
        await post('/api/apn/active/restore', {}, { confirmTitle: 'Restore the previous APN?',
          confirmMessage: `Go back to "${vals._previous.apn}". ${CHANGE_MSG}` });
        toast('Previous APN restored'); activeForm.reload();
      })],
      fields: [
        presetButton(),
        APN_FIELD,
        { key: 'auth', label: 'Authentication', type: 'select', options: AUTH3 },
        { key: 'username', label: 'User name', type: 'text', maxLength: 32, show: (v) => v.auth !== 'none',
          validate: (x, v) => v.auth !== 'none' && !x ? 'Required for PAP/CHAP' : null },
        { key: 'password', label: 'Password', type: 'password', maxLength: 32, show: (v) => v.auth !== 'none',
          placeholder: 'Leave empty to keep the saved one' },
        { key: '_previous', type: 'readonly', label: 'Previous APN', noSave: true, format: (v) => v ? v.apn : dash },
      ],
    });

    // ---- suggestion for the SIM ---------------------------------------------------------------
    section(suggestBox, () => get('/api/apn/lookup'), (r) => {
      const rows = [];
      if (!r.home || !r.home.mcc) return card('Suggested for this SIM', h('p.muted', 'Insert a SIM card to see the APN its carrier uses.'), { icon: 'sim' });
      rows.push(['SIM network', `${r.home.mcc}-${r.home.mnc}`, 'MCC-MNC']);
      rows.push(['Vendor database', r.available ? h('span.mono', r.apn || dash) : h('span.muted', r.reason || 'no match')]);
      if (r.custom) rows.push(['Saved for this SIM', h('span.mono', r.custom.apn || dash)]);
      const use = (apn, auth, username) => () => { activeForm.set('apn', apn); activeForm.set('auth', auth === 'pap_chap' ? 'chap' : (auth || 'none')); activeForm.set('username', username || ''); toast('Filled in - press Save to apply'); };
      return card('Suggested for this SIM', [kv(rows),
        h('div.form-actions',
          r.available && r.apn && smallButton('Use suggested APN', use(r.apn, r.auth, r.username), { kind: 'primary' }),
          smallButton('Browse presets', () => pickPreset((p) => use(p.apn, p.auth, p.username)())))], { icon: 'sim' });
    });

    // ---- saved APN profiles (multi-APN) ------------------------------------------------------------
    const listCfg = {
      title: 'Saved APN profiles', icon: 'sim', max: 10, addLabel: 'Add profile', addTitle: 'New APN profile', editTitle: 'Edit APN profile',
      description: 'APN profiles are stored per SIM card. The profile marked "in use" provides the internet connection; the carrier default can be edited and restored but not deleted.',
      emptyText: 'No saved profiles for this SIM.',
      // adding needs a SIM (profiles are stored per IMSI/ICCID): max 0 disables "Add" without one
      load: async () => { const r = await get('/api/apn/profiles'); listCfg.max = r.sim_present ? r.max : 0; return r.profiles; },
      columns: [
        { key: 'name', label: 'Name', format: (v, r) => h('span', v, r.default ? h('span', ' ', badge('carrier default', 'info')) : '') },
        { key: 'apn', label: 'APN', cls: 'mono' },
        { key: 'auth', label: 'Login', format: (v) => LABEL(AUTH, v) },
        { key: 'pdp', label: 'IP type', format: (v) => LABEL(PDP, v) },
        { key: 'active', label: 'Status', format: (v, r) => v ? badge('in use', 'ok') : (r.this_sim ? badge('saved', 'neutral') : badge('other SIM', 'neutral')) },
      ],
      fields: profileFields(true),
      defaults: { auth: 'none', pdp: 'ipv4v6', username: '', password: '' },
      add: async (row) => {
        const body = pick(row, ['name', 'apn', 'auth', 'username', 'password', 'pdp']);
        if (!body.password) delete body.password;
        await post('/api/apn/profiles', body);
      },
      update: async (row, old) => {
        const body = pick(row, ['name', 'apn', 'auth', 'username', 'password', 'pdp']);
        if (!body.password) delete body.password;
        await put(`/api/apn/profiles/${old.id}`, body, { confirmTitle: 'Change the APN in use?', confirmMessage: CHANGE_MSG });
      },
      rowActions: (r, reload) => [
        !r.active && smallButton('Use', async () => {
          await post(`/api/apn/profiles/${r.id}/activate`, {}, { confirmTitle: `Use "${r.name}" for the internet?`, confirmMessage: CHANGE_MSG });
          toast('Profile in use'); reload(); activeForm.reload();
        }),
        r.default && smallButton('Restore', async () => {
          if (!(await confirmSimple('Restore carrier default', 'Put back the carrier’s original values for this profile?', 'Restore'))) return;
          await post(`/api/apn/profiles/${r.id}/restore`, {}, { confirmMessage: CHANGE_MSG }); toast('Restored'); reload();
        }),
        r.password_set && smallButton('Password', () => revealSecret(`/api/apn/profiles/${r.id}/secret`)),
        !r.default && h('button.icon-btn.danger', { type: 'button', title: 'Delete', 'aria-label': 'Delete', onclick: async () => {
          if (!(await confirmSimple('Delete profile', `Delete the APN profile "${r.name}"?`, 'Delete', 'danger'))) return;
          try {
            await del(`/api/apn/profiles/${r.id}`, {}, { confirmTitle: 'Delete the profile in use?', confirmMessage: CHANGE_MSG });
            toast('Deleted'); reload();
          } catch (e) { if (e.code !== 'cancelled') toast(e.message, 'bad', 6000); }
        } }, '×'),
      ].filter(Boolean),
    };
    list(listBox, listCfg);

    // ---- APN failback -----------------------------------------------------------------------------
    async function loadFailback() {
      await section(failBox, () => get('/api/apn/failback'), (fb) => {
        const host = h('div');
        form(host, { title: 'APN failback', icon: 'refresh',
          description: 'When the data connection cannot be set up, the modem can try the carrier’s other known APNs in turn.',
          load: async () => ({ enabled: !!fb.enabled }),
          save: async (ch) => { const r = await put('/api/apn/failback', ch); return { enabled: !!r.enabled }; },
          actions: [smallButton('Try the next APN now', async () => {
            await post('/api/apn/failback/switch', {}, { confirmTitle: 'Switch to the next APN?', confirmMessage: CHANGE_MSG });
            toast('Switching APN'); loadFailback();
          })],
          fields: [{ key: 'enabled', label: 'APN failback', type: 'switch' }] });
        return [host, card('Failback APN list', [
          table([
            { key: 'plmn', label: 'Network', mono: true, format: (v) => v ? `${v.slice(0, 3)}-${v.slice(3)}` : dash },
            { key: 'apn', label: 'APN', mono: true },
            { key: 'pdp', label: 'IP type', format: (v) => LABEL(PDP, v) },
            { key: 'max_iterations', label: 'Tries' },
            { key: 'backoff_s', label: 'Wait', format: (v) => has(v) ? `${v} s` : dash },
          ], fb.apns, { empty: 'The modem has no failback APNs.' }),
          fb.sims && fb.sims.length ? table([
            { key: 'imsi_masked', label: 'SIM', mono: true }, { key: 'iteration', label: 'Round' }, { key: 'index', label: 'Next APN' },
            { key: 'enabled', label: 'Active', format: (v) => yesNo(v) }], fb.sims) : null,
        ], { icon: 'globe' })];
      });
    }
    loadFailback();

    if (!expert) return () => {};

    // ---- expert: modem data profiles ---------------------------------------------------------------
    function profileEditor(title, initial, saveFn) {
      const host = h('div');
      const m = modal({ title, body: host, wide: true, actions: [{ label: 'Cancel', value: false }] });
      form(host, {
        load: async () => Object.assign({ password: '' }, initial),
          save: async (ch) => {
          const body = pick(ch, ['apn', 'username', 'password', 'name', 'auth', 'pdp', 'apn_disabled', 'inactivity_timeout', 'dns_auto', 'dns1', 'dns2']);
          if (body.password === '') delete body.password;
          await saveFn(body);
          m.close(true);
          return undefined;
        },
        fields: [
          { key: 'apn', label: 'APN', type: 'text', maxLength: 62, pattern: APN_PATTERN, patternMsg: 'Letters, digits, dots and hyphens only' },
          { key: 'name', label: 'Profile name', type: 'text', maxLength: 63 },
          { key: 'auth', label: 'Authentication', type: 'select', options: AUTH3 },
          { key: 'username', label: 'User name', type: 'text', maxLength: 63 },
          { key: 'password', label: 'Password', type: 'password', maxLength: 63, placeholder: 'Leave empty to keep the saved one' },
          { key: 'pdp', label: 'IP type', type: 'select', options: PDP },
          { key: 'dns_auto', label: 'DNS from the network', type: 'switch' },
          { key: 'dns1', label: 'DNS server 1', type: 'text', show: (v) => !v.dns_auto, maxLength: 15, inputmode: 'decimal' },
          { key: 'dns2', label: 'DNS server 2', type: 'text', show: (v) => !v.dns_auto, maxLength: 15, inputmode: 'decimal' },
          { key: 'apn_disabled', label: 'Profile disabled', type: 'switch' },
          { key: 'inactivity_timeout', label: 'Inactivity timeout', type: 'number', unit: 's', min: 0, max: 86400, help: '0 = never' },
        ],
      });
    }

    async function loadModem() {
      await section(modemBox, () => get('/api/apn/modem-profiles?type=0&max=8'), (r) => card('Modem data profiles', [
        h('p.card-desc', 'Low-level 3GPP data profiles inside the modem. One of them carries the internet connection; others are used for '
          + 'network attach, voice (IMS) and carrier services. Changing the wrong one can stop the router from registering.'),
        table([
          { key: 'index', label: '#' },
          { key: 'name', label: 'Name' },
          { key: 'apn', label: 'APN', mono: true },
          { key: 'auth', label: 'Login', format: (v) => LABEL(AUTH, v) },
          { key: 'pdp', label: 'IP type', format: (v) => LABEL(PDP, v) },
          { key: 'dns_auto', label: 'DNS', format: (v, p) => v ? 'network' : `${p.dns1}${p.dns2 && p.dns2 !== '0.0.0.0' ? ', ' + p.dns2 : ''}` },
          { key: 'active', label: '', actions: true, format: (v, p) => [
            v ? badge('internet', 'ok') : smallButton('Use for internet', async () => {
              await put('/api/apn/modem-profiles/active', { type: 0, index: p.index }, { confirmTitle: `Use modem profile ${p.index} for the internet?`, confirmMessage: CHANGE_MSG });
              toast('Active profile changed'); loadModem(); activeForm.reload();
            }),
            ' ',
            smallButton('Edit', () => profileEditor(`Modem profile ${p.index}`, p, async (body) => {
              await put(`/api/apn/modem-profiles/0/${p.index}`, body, { confirmTitle: `Change modem profile ${p.index}?`, confirmMessage: CHANGE_MSG });
              toast('Saved'); loadModem(); activeForm.reload();
            })),
          ] },
        ], r.profiles, { empty: 'The modem did not report any data profile.' }),
        r.previous_active && r.previous_active.index ? h('p.hint', `Previously used for internet: profile ${r.previous_active.index}.`) : null,
      ], { icon: 'chip' }));
    }
    loadModem();

    section(techBox, () => get('/api/apn/tech-profiles'), (r) => {
      const techName = { lte: 'LTE', umts: 'UMTS', ehrpd: 'eHRPD' };
      const rows = [];
      for (const t of ['lte', 'umts', 'ehrpd']) {
        const pri = r.pri[t], cust = r.custom[t];
        rows.push({ tech: techName[t], key: t, pri: pri ? pri.apn || '(empty)' : dash, cust: cust ? cust.apn || '(empty)' : dash, custObj: cust });
      }
      return card('Technology profiles', [
        h('p.card-desc', 'Carrier-provisioned (read only) and custom default profiles per radio technology, used for network attach.'),
        table([
          { key: 'tech', label: 'Technology' },
          { key: 'pri', label: 'Carrier APN', mono: true },
          { key: 'cust', label: 'Custom APN', mono: true },
          { key: 'x', label: '', actions: true, format: (v, row) => row.custObj ? smallButton('Edit custom', () => profileEditor(`Custom ${row.tech} profile`, row.custObj, async (body) => {
            await put(`/api/apn/tech-profiles/custom/${row.key}`, body, { confirmTitle: `Change the custom ${row.tech} profile?`, confirmMessage: CHANGE_MSG });
            toast('Saved');
          })) : dash },
        ], rows),
      ], { icon: 'chip' });
    });

    async function loadPdn() {
      await section(pdnBox, () => get('/api/apn/pdn?max=4'), (r) => card('PDN settings', [
        h('p.card-desc', 'Per-profile overrides: IP type forced by the router, 464XLAT (CLAT) and extra connection parameters.'),
        table([
          { key: 'profile', label: 'Profile' },
          { key: 'pdp_override', label: 'IP type override', format: (v, p) => p.override_available ? LABEL(PDP, v) : dash },
          { key: 'clat', label: 'CLAT', format: (v, p) => p.override_available ? yesNo(v) : dash },
          { key: 'max_connections', label: 'Max connections' },
          { key: 'wait_timer', label: 'Wait timer' },
          { key: 'connection_timer', label: 'Connection timer' },
          { key: 'x', label: '', actions: true, format: (v, p) => (p.override_available || p.ext_available) ? smallButton('Edit', () => {
            const host = h('div');
            const m = modal({ title: `PDN settings of profile ${p.profile}`, body: host, actions: [{ label: 'Cancel', value: false }] });
            form(host, { load: async () => Object.assign({}, p),
              save: async (ch) => {
                await put(`/api/apn/pdn/${p.profile}`, pick(ch, ['pdp_override', 'clat', 'max_connections', 'wait_timer', 'connection_timer']),
                  { confirmTitle: 'Change PDN settings?', confirmMessage: CHANGE_MSG });
                m.close(true); loadPdn(); return undefined;
              },
              fields: [
                p.override_available && { key: 'pdp_override', label: 'IP type override', type: 'select', options: PDP },
                p.override_available && { key: 'clat', label: 'CLAT (464XLAT)', type: 'switch' },
                p.ext_available && { key: 'max_connections', label: 'Max PDN connections', type: 'number', min: 0, max: 1023 },
                p.ext_available && { key: 'wait_timer', label: 'Wait timer', type: 'number', min: 0, max: 65535 },
                p.ext_available && { key: 'connection_timer', label: 'Connection timer', type: 'number', min: 0, max: 65535 },
              ] });
          }) : dash },
        ], r.profiles),
      ], { icon: 'tools' }));
    }
    loadPdn();
    return () => {};
  },
};

function pick(o, keys) {
  const out = {};
  for (const k of keys) if (o && Object.prototype.hasOwnProperty.call(o, k) && o[k] !== undefined) out[k] = o[k];
  return out;
}
