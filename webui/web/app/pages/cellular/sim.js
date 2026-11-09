// Cellular > SIM: card state, PIN lock (enable / disable / change / unlock / PUK), identifiers (masked, reveal),
// carrier (network) lock with unlock code, carrier account notice, and SIM slots on dual-SIM units.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, alert, toast } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { get, post, put } from '../../api.js';
import { dash, has, problem, section, table, secret, attempts, withLastAttempt, onOff, yesNo, smallButton } from './_sim_ui.js';

const STATE_TONE = { ready: 'ok', unlocked: 'ok', pin_locked: 'warn', puk_locked: 'bad', perm_blocked: 'bad',
  not_found: 'neutral', error: 'bad', restricted: 'bad', pairing_invalid: 'bad', invalid: 'bad' };
const PIN_HELP = '4 to 8 digits.';

function pinField(key, label, help) {
  return { key, label, type: 'password', required: true, maxLength: 8, pattern: /^[0-9]{4,8}$/, patternMsg: 'Use 4 to 8 digits',
    autocomplete: 'off', help: help || PIN_HELP };
}

export default {
  id: 'cellular-sim', path: '/cellular/sim', group: 'cellular', order: 50, title: 'SIM', icon: 'sim',
  async render(el, ctx) {
    const expert = !!(ctx && ctx.session && ctx.session.expert_mode);
    const top = h('div'), grid = h('div.grid-2'), statusBox = h('div'), pinBox = h('div');
    const identBox = h('div'), lockBox = h('div'), slotsBox = h('div');
    grid.append(statusBox, pinBox);
    el.append(top, grid, h('div.grid-2', identBox, lockBox), slotsBox);

    let st = null;
    async function load() {
      try { st = await get('/api/sim/status'); } catch (e) { clear(top).appendChild(problem(e, load)); return; }
      renderTop(); renderStatus(); renderPin(); renderIdentity();
    }

    function renderTop() {
      clear(top);
      if (!st.present) top.appendChild(alert('warn', 'No SIM card detected. Insert a SIM card to connect, manage its PIN and see its details.'));
      if (st.state === 'perm_blocked') top.appendChild(alert('bad', 'The SIM card is permanently blocked (all PUK attempts used). Ask the carrier for a replacement SIM.'));
      if (st.state === 'pairing_invalid' || st.state === 'restricted') top.appendChild(alert('bad', 'The modem does not accept this SIM card. It may be locked to another carrier; see Carrier lock below.'));
      section(h('div'), () => get('/api/sim/account'), (a) => {
        if (!a.restricted) return null;
        top.appendChild(alert('warn', a.message, ' ', smallButton('Allow limited access', async () => {
          await post('/api/sim/account/accept', {});
          toast('Limited access requested; the carrier portal should open in a browser');
        }, { kind: 'primary' })));
        return null;
      });
    }

    function renderStatus() {
      const p = st.pin || {};
      clear(statusBox).appendChild(card('SIM card', kv([
        ['Status', badge(st.state_label || st.state, STATE_TONE[st.state] || 'neutral')],
        ['PIN lock', p.known || st.present ? onOff(p.lock_enabled) : dash],
        ['PIN attempts', st.present ? attempts(p.retries, 1) : dash, st.present ? 'a wrong PIN uses one' : ''],
        ['PUK attempts', st.present ? attempts(p.puk_retries, 3) : dash, st.present ? 'when none are left the SIM is blocked for good' : ''],
        ['Carrier (from SIM)', st.carrier || dash],
        ['Home network', st.home_network && st.home_network.mcc ? `${st.home_network.mcc}-${st.home_network.mnc}` : dash, st.present ? 'MCC-MNC' : ''],
        expert && ['Vendor status', st.vendor_status || dash],
        expert && ['PIN state', p.state ? p.state.replace(/_/g, ' ') : dash],
      ]), { icon: 'sim', actions: smallButton('Refresh', load, { icon: 'refresh' }) }));
    }

    async function pinRequest(url, body, title, message) {
      const res = await withLastAttempt((b) => post(url, b, { confirmTitle: title, confirmMessage: message }), body);
      st = res; renderTop(); renderStatus(); renderPin(); renderIdentity();
      return res;
    }

    function pinCard(title, description, fields, saveLabel, submit) {
      const box = h('div');
      form(box, { title, icon: 'lock', description, saveLabel,
        load: async () => Object.fromEntries(fields.map(f => [f.key, ''])),
        save: async (ch, v) => { await submit(v); return Object.fromEntries(fields.map(f => [f.key, ''])); },
        savedMessage: 'Done', fields });
      return box;
    }

    function renderPin() {
      clear(pinBox);
      const p = st.pin || {};
      const acts = st.actions || [];
      if (!st.present || !acts.length) {
        pinBox.appendChild(card('PIN lock', h('p.muted', st.present ? 'No PIN action is possible in the current SIM state.'
          : 'Insert a SIM card to manage its PIN.'), { icon: 'lock' }));
        return;
      }
      const warn = `A wrong PIN uses one of the ${has(p.retries) ? p.retries : 'remaining'} attempts.`;
      if (acts.includes('verify_pin')) {
        pinBox.appendChild(pinCard('Unlock the SIM', `The SIM asks for its PIN before it can connect. ${warn}`,
          [pinField('pin', 'PIN')], 'Unlock',
          (v) => pinRequest('/api/sim/pin/verify', { pin: v.pin }, 'Unlock the SIM?', `The PIN is sent to the SIM. ${warn}`)));
      }
      if (acts.includes('unblock_puk')) {
        pinBox.appendChild(pinCard('Unblock the SIM (PUK)',
          `The SIM is blocked after too many wrong PINs. Enter the 8-digit PUK from the SIM card holder or the carrier, and choose a new PIN. `
          + `${has(p.puk_retries) ? p.puk_retries : 'Few'} PUK attempts are left; when they are used up the SIM is blocked for good.`,
          [{ key: 'puk', label: 'PUK', type: 'password', required: true, maxLength: 8, pattern: /^[0-9]{8}$/, patternMsg: 'The PUK has 8 digits' },
            pinField('new_pin', 'New PIN'),
            Object.assign(pinField('confirm_pin', 'Repeat new PIN'), { validate: (x, vals) => x !== vals.new_pin ? 'The PINs do not match' : null })],
          'Unblock',
          (v) => pinRequest('/api/sim/pin/unblock', { puk: v.puk, new_pin: v.new_pin }, 'Unblock the SIM with the PUK?',
            'A wrong PUK uses one of the remaining PUK attempts. When none are left the SIM card stops working permanently.')));
      }
      if (acts.includes('enable_lock')) {
        pinBox.appendChild(pinCard('Turn the PIN lock on',
          'With the lock on, the SIM asks for its PIN every time the router starts (until then there is no mobile connection).',
          [pinField('pin', 'Current PIN', 'The PIN of the SIM card (often 0000 or 1234 when new).')], 'Turn on',
          (v) => pinRequest('/api/sim/pin/lock', { enabled: true, pin: v.pin }, 'Turn the PIN lock on?', warn)));
      }
      if (acts.includes('disable_lock')) {
        pinBox.appendChild(pinCard('Turn the PIN lock off', 'The SIM no longer asks for a PIN when the router starts.',
          [pinField('pin', 'Current PIN')], 'Turn off',
          (v) => pinRequest('/api/sim/pin/lock', { enabled: false, pin: v.pin }, 'Turn the PIN lock off?', warn)));
      }
      if (acts.includes('change_pin')) {
        pinBox.appendChild(pinCard('Change the PIN', 'Choose a new PIN for the SIM card.',
          [pinField('pin', 'Current PIN'), pinField('new_pin', 'New PIN'),
            Object.assign(pinField('confirm_pin', 'Repeat new PIN'), { validate: (x, vals) => x !== vals.new_pin ? 'The PINs do not match' : null })],
          'Change PIN',
          (v) => pinRequest('/api/sim/pin/change', { pin: v.pin, new_pin: v.new_pin }, 'Change the SIM PIN?', warn)));
      }
    }

    function renderIdentity() {
      const id = st.identity || {};
      const reveal = (k) => async () => (await get('/api/sim/identity/secret'))[k];
      clear(identBox).appendChild(card('Identifiers', [
        kv([
          ['ICCID', secret(id.iccid, reveal('iccid')), 'SIM serial number'],
          ['IMSI', secret(id.imsi, reveal('imsi')), 'subscriber identity'],
          ['Phone number', secret(id.msisdn, reveal('msisdn'))],
          ['IMEI', secret(id.imei, reveal('imei')), 'modem identity'],
          ['GID1', st.gid1 ? h('span.mono', st.gid1) : dash, 'group id (carrier sub-brand)'],
          ['GID2', st.gid2 ? h('span.mono', st.gid2) : dash],
          ['MNC length', has(st.mnc_length) ? `${st.mnc_length} digits` : dash],
          ['Test SIM', st.present ? yesNo(st.test_sim) : dash],
        ]),
        h('p.hint', 'Identifiers are hidden by default. "Show" reads the full value from the router.'),
      ], { icon: 'about' }));
    }

    function renderLock(ls) {
      const nv = ls.nv60002 || {};
      const rows = kv([
        ['Carrier lock', badge(ls.carrier_locked ? 'Locked' : 'Not locked', ls.carrier_locked ? 'warn' : 'ok')],
        ['Unlock attempts', ls.carrier_locked ? attempts(ls.attempts_left, 1) : (has(ls.attempts_left) ? String(ls.attempts_left) : dash),
          ls.carrier_locked ? 'when none are left the lock becomes permanent' : ''],
        ['Permanently blocked', yesNo(ls.blocked)],
        ['Lock flag (NV 60002)', nv.available ? h('span', h('span.mono', `0x${nv.hex || '00'}`), ' ', badge(nv.unlocked ? 'unlocked' : 'set', nv.unlocked ? 'ok' : 'warn')) : dash,
          'read only'],
        ['SIM lock policy', ls.sim_lock_policy ? `${ls.sim_lock_policy.label}${has(ls.sim_lock_policy.value) ? ` (${ls.sim_lock_policy.value})` : ''}` : dash],
        ls.sim_restriction && has(ls.sim_restriction.value) && ['Carrier SIM restriction', `${ls.sim_restriction.label} (${ls.sim_restriction.value})`,
          'reported by the carrier management service'],
        expert && ls.rest && ['Vendor counters', `unlock ${has(ls.rest.unlock_counter) ? ls.rest.unlock_counter : dash}, unblock ${has(ls.rest.unblock_counter) ? ls.rest.unblock_counter : dash}`],
        expert && ['Modem lock state', has(ls.state) ? String(ls.state) : dash],
      ]);
      const parts = [h('p.card-desc', 'A carrier lock makes the modem refuse SIM cards from other carriers. The unlock code comes from the carrier that sold the device.'), rows];
      if (ls.can_unlock) {
        const box = h('div');
        form(box, { title: 'Enter the unlock code', icon: 'lock', saveLabel: 'Unlock',
          description: 'Attempts are limited. Enter the code exactly as received; do not guess.',
          load: async () => ({ code: '' }),
          save: async (ch, v) => {
            const res = await withLastAttempt((b) => post('/api/sim/carrier-unlock', b, { confirmTitle: 'Try to remove the carrier lock?',
              confirmMessage: 'A wrong code uses one of the remaining attempts. After the last one the lock cannot be removed any more.',
              confirmPhrase: 'UNLOCK' }), { code: v.code.trim() });
            renderLock(res);
            toast(res.carrier_locked ? 'The modem still reports a lock' : 'Carrier lock removed');
            return { code: '' };
          },
          fields: [{ key: 'code', label: 'Unlock code', type: 'text', required: true, minLength: 4, maxLength: 32,
            pattern: /^[0-9A-Za-z]{4,32}$/, patternMsg: 'Letters and digits only' }] });
        parts.push(box);
      } else if (ls.reason) {
        parts.push(h('p.hint', ls.reason));
      }
      clear(lockBox).appendChild(card('Carrier lock', parts, { icon: 'lock' }));
    }

    function renderSlots(sl) {
      if (!sl.supported) { clear(slotsBox); return; }
      const rows = (sl.slots || []).map(s => Object.assign({}, s));
      const parts = [
        kv([
          ['SIM cards', has(sl.num_sims) ? String(sl.num_sims) : dash],
          ['Active slot', has(sl.active_slot) ? String(sl.active_slot) : dash],
          ['Switching', sl.switch_status ? sl.switch_status.replace(/_/g, ' ') : dash],
          ['SIM manager', sl.manager_running ? badge('running', 'ok') : badge('not running', 'warn')],
        ]),
        table([
          { key: 'slot', label: 'Slot' },
          { key: 'card', label: 'Card' },
          { key: 'sim_state', label: 'SIM', format: (v) => (v || '').replace(/_/g, ' ') },
          { key: 'operator', label: 'Network' },
          { key: 'imsi_masked', label: 'IMSI', mono: true },
          { key: 'wwan_state', label: 'Data' },
          { key: 'active', label: '', actions: true, format: (v, r) => v ? badge('active', 'ok')
            : (r.present ? smallButton('Use this SIM', async () => {
              const res = await post('/api/sim/slots/switch', { slot: r.slot }, { confirmTitle: `Switch to SIM slot ${r.slot}?`,
                confirmMessage: 'The mobile connection drops while the modem changes SIM card. If IP passthrough is on, this browser may lose its address.' });
              renderSlots(res); toast('SIM switched');
            }) : dash) },
        ], rows),
      ];
      if (sl.previous_slot && sl.previous_slot !== sl.active_slot) {
        parts.push(h('p.hint', `Previously active: slot ${sl.previous_slot}.`));
      }
      const settingsBox = h('div');
      form(settingsBox, { title: 'Automatic switching', icon: 'sim',
        description: 'The SIM manager can switch to the other SIM when the connection fails.',
        load: async () => ({ auto_switch: !!sl.auto_switch, conn_monitor: !!sl.conn_monitor, sim_monitor: !!sl.sim_monitor,
          conn_recovery: !!sl.conn_recovery, conn_interval: sl.conn_interval, conn_retries: sl.conn_retries, sim_interval: sl.sim_interval }),
        save: async (ch) => {
          const res = await put('/api/sim/slots/settings', ch, { confirmTitle: 'Change SIM switching?',
            confirmMessage: 'With automatic switching on, the router may change SIM card on its own when the connection fails.' });
          renderSlots(res);
          return undefined;
        },
        fields: [
          { key: 'auto_switch', label: 'Switch SIM automatically', type: 'switch' },
          { key: 'conn_monitor', label: 'Watch the connection', type: 'switch' },
          { key: 'sim_monitor', label: 'Watch the SIM cards', type: 'switch' },
          { key: 'conn_recovery', label: 'Connection recovery', type: 'switch', help: 'Reset the modem or router when the connection stays down' },
          expert && { key: 'conn_interval', label: 'Connection check interval', type: 'number', unit: 's', min: 5, max: 3600 },
          expert && { key: 'conn_retries', label: 'Failed checks before acting', type: 'number', min: 1, max: 20 },
          expert && { key: 'sim_interval', label: 'SIM check interval', type: 'number', unit: 's', min: 60, max: 86400 },
        ] });
      parts.push(settingsBox);
      if (expert) {
        const recBox = h('div');
        form(recBox, { title: 'Connection recovery limits', icon: 'tools',
          load: () => get('/api/sim/slots/recovery'),
          save: (ch) => put('/api/sim/slots/recovery', ch),
          fields: [
            { key: 'period_min', label: 'Recovery period', type: 'number', unit: 'min', min: 30, max: 1440 },
            { key: 'modem_disconnect_timeout_min', label: 'Modem disconnect timeout', type: 'number', unit: 'min', min: 1, max: 15 },
            { key: 'max_sim_switches', label: 'Max SIM switches', type: 'number', min: 0, max: 10 },
            { key: 'max_dns_failures', label: 'Max DNS failures', type: 'number', min: 0, max: 10 },
            { key: 'max_modem_resets', label: 'Max modem resets', type: 'number', min: 1, max: 5 },
            { key: 'max_device_resets', label: 'Max router resets', type: 'number', min: 0, max: 5 },
            { key: 'max_system_resets', label: 'Max system resets', type: 'number', min: 0, max: 10 },
          ] });
        parts.push(recBox);
      }
      clear(slotsBox).appendChild(card('SIM slots', parts, { icon: 'sim' }));
    }

    await load();
    section(lockBox, () => get('/api/sim/lock'), (ls) => { renderLock(ls); return null; });
    if (ctx && ctx.store && ctx.store.feature && ctx.store.feature('sim.multi')) {
      section(slotsBox, () => get('/api/sim/slots'), (sl) => { renderSlots(sl); return null; });
    }
  },
};
