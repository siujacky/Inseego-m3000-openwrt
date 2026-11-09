// Services › TR-069 (expert): remote management by an auto-configuration server (ACS).
import { h, clear, append } from '../../ui/dom.js';
import { card, kv, badge, alert, button, toast, spinner, errorBox, modal } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import { smartForm, isUnavailable } from '../device/_helpers.js';

const LOG_LEVELS = [[0, 'Critical'], [1, 'Warning'], [2, 'Notice'], [3, 'Info'], [4, 'Debug']];

function pw(key, label) {
  return { key, label, type: 'password', maxLength: 96, autocomplete: 'new-password',
    placeholder: 'Unchanged', help: (v) => (v[key + '_set'] ? 'A password is stored. Leave empty to keep it.' : 'No password stored.') };
}

async function reveal() {
  const s = await get('/api/services/tr069/secret');
  modal({ title: 'Stored TR-069 passwords', body: h('div', h('p.hint', 'Anyone who can see your screen can read these.'),
    h('dl.kv', [['ACS server', s.server_password], ['Connection request', s.client_password], ['XMPP', s.xmpp_password]]
      .map(([k, v]) => [h('dt', k), h('dd.mono', v || '—')]))), actions: [{ label: 'Close', value: null }] });
}

export default {
  id: 'services-tr069', path: '/services/tr069', group: 'services', order: 40, title: 'TR-069', icon: 'globe', expert: true,
  feature: 'services.tr069',
  render(el) {
    const statusBody = h('div', spinner());
    const settings = h('div');
    const carriers = h('div');
    el.append(card('TR-069 remote management', statusBody, { icon: 'globe' }), settings, carriers);
    let form = null;

    async function refresh() {
      let t;
      try { t = await get('/api/services/tr069'); } catch (e) {
        clear(statusBody).appendChild(isUnavailable(e) ? alert('info', e.message) : errorBox(e, refresh));
        return;
      }
      const turn = (on) => button(on ? 'Turn on' : 'Turn off', async () => {
        await put('/api/services/tr069/status', { enabled: on }, {
          confirmTitle: 'Turn TR-069 on',
          confirmMessage: `The server ${t.server_url || '(not set)'} will be able to manage this router completely: change any setting, `
            + 'install firmware and reset it. Only continue if you run or trust that server.',
        });
        toast(on ? 'TR-069 is on' : 'TR-069 is off'); refresh(); if (form) form.reload();
      }, { kind: on ? 'primary' : undefined, icon: 'power' });
      const restart = button('Restart client', async () => {
        await post('/api/services/tr069/restart', {}, { confirmTitle: 'Restart the TR-069 client',
          confirmMessage: 'The client restarts and contacts the server with the current settings.' });
        toast('TR-069 client restarted');
      }, { small: true, icon: 'refresh' });
      append(clear(statusBody), [
        alert('warn', 'TR-069 hands control of the router to a remote server. It is off on retail units; turn it on only for '
          + 'your own management server.'),
        kv([
          ['Status', badge(t.enabled ? 'On' : 'Off', t.enabled ? 'warn' : 'neutral')],
          ['Client process', t.service_running ? 'Running' : 'Not running'],
          ['Server', t.server_url ? h('span.mono', `${t.server_url}${t.server_port ? `:${t.server_port}` : ''}`) : ''],
          ['Settings changed since setup', t.modified === null ? '' : (t.modified ? 'Yes' : 'No')],
          ['Keep after factory reset', t.preserve === null ? '' : (t.preserve ? 'Yes' : 'No')],
        ]),
        h('div.form-actions', turn(!t.enabled), restart, button('Show stored passwords', reveal, { small: true, icon: 'eye' }))]);
      clear(carriers).appendChild(card('Carrier profiles', t.carriers.length ? h('div.table-wrap', h('table.table',
        h('thead', h('tr', h('th', 'Profile'), h('th', 'Used for networks (MCC/MNC)'))),
        h('tbody', t.carriers.map(c => h('tr', h('td.mono', c.carrier), h('td.mono', c.mccmnc.join(', '))))))) :
        h('p.hint', 'No carrier profiles.'), { icon: 'sim', subtitle: 'Read only: the ACS profile is picked by the SIM network code' }));
    }

    smartForm(settings, {
      title: 'TR-069 settings', icon: 'system',
      load: () => get('/api/services/tr069'),
      save: async (ch) => {
        const body = Object.assign({}, ch);
        ['server_password', 'client_password', 'xmpp_password'].forEach(k => { if (!body[k]) delete body[k]; });
        const d = await put('/api/services/tr069', body);
        refresh();
        return d;
      },
      onReady: (f) => { form = f; },
      fields: [
        { type: 'section', label: 'Auto-configuration server (ACS)' },
        { key: 'server_url', label: 'Server address', type: 'text', maxLength: 96, placeholder: 'acs.example.net or https://…', full: true },
        { key: 'server_port', label: 'Server port', type: 'number', min: 1, max: 65535 },
        { key: 'server_username', label: 'User name', type: 'text', maxLength: 96 },
        pw('server_password', 'Password'),
        { key: 'server_interface', label: 'Reach the server through', type: 'select', options: [['cellular', 'Mobile network'], ['usb', 'USB / Ethernet']] },
        { key: 'server_ip_type', label: 'IP version', type: 'select', options: [['ipv4', 'IPv4'], ['ipv6', 'IPv6']] },
        { type: 'section', label: 'Connection requests from the server' },
        { key: 'client_port', label: 'Listening port', type: 'number', min: 1, max: 65535 },
        { key: 'client_conn_auth', label: 'Require authentication', type: 'switch' },
        { key: 'client_auth_mode', label: 'Authentication', type: 'select', options: [['basic', 'Basic'], ['digest', 'Digest']] },
        { key: 'client_username', label: 'User name', type: 'text', maxLength: 96 },
        pw('client_password', 'Password'),
        { key: 'client_inform_s', label: 'Periodic inform interval', type: 'number', unit: 's', min: 0, max: 600 },
        { key: 'client_cwmp_version', label: 'CWMP version', type: 'number', min: 0, max: 9 },
        { key: 'client_log_level', label: 'Log level', type: 'select', options: LOG_LEVELS },
        { type: 'section', label: 'XMPP connection requests' },
        { key: 'xmpp_enabled', label: 'XMPP', type: 'switch' },
        { key: 'xmpp_url', label: 'XMPP server', type: 'text', maxLength: 96, show: (v) => !!v.xmpp_enabled },
        { key: 'xmpp_port', label: 'XMPP port', type: 'number', min: 0, max: 65535, show: (v) => !!v.xmpp_enabled },
        { key: 'xmpp_jabber_id', label: 'Jabber ID', type: 'text', maxLength: 96, show: (v) => !!v.xmpp_enabled },
        { key: 'xmpp_username', label: 'User name', type: 'text', maxLength: 96, show: (v) => !!v.xmpp_enabled },
        Object.assign(pw('xmpp_password', 'Password'), { show: (v) => !!v.xmpp_enabled }),
        { type: 'section', label: 'Factory reset' },
        { key: 'preserve', label: 'Keep TR-069 settings after a factory reset', type: 'switch',
          help: 'When on, remote management survives a factory reset.' },
      ],
    });
    refresh();
  },
};
