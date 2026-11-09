// VPN › VPN client: status, OpenVPN profile upload, WireGuard tunnels, IPsec tunnels, log.
import { h, clear } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { list } from '../../ui/list.js';
import { card, kv, badge, alert, button, toast, spinner, confirmSimple, modal } from '../../ui/widgets.js';
import { get, put, post, del } from '../../api.js';
import { stateBadge, statsRows, readFiles, randomWgKey, WG_KEY } from './_vpn.js';

const TABS = [['openvpn', 'OpenVPN'], ['wireguard', 'WireGuard'], ['ipsec', 'IPsec'], ['log', 'Log']];
const unavailable = (e) => e && (e.status === 503 || ['unavailable', 'unsupported'].includes(e.code));

function problem(e, retry) {
  return alert(unavailable(e) ? 'warn' : 'bad', unavailable(e) ? 'The VPN service is not available right now. ' : 'The router reported a problem. ',
    h('span', (e && e.message) || String(e)), ' ', retry && button('Retry', retry, { small: true }));
}

export default {
  id: 'vpn-client', path: '/vpn/client', group: 'vpn', order: 10, title: 'VPN client', icon: 'vpn', feature: 'vpn',
  async render(el, ctx) {
    const statusBox = h('div');
    const tabBar = h('div.tabs', { role: 'tablist' });
    const body = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '16px' } });
    el.append(statusBox, h('div', tabBar, body));
    let tab = (ctx && ctx.query && ctx.query.tab) || 'openvpn';
    let status = null;
    let timer = null;

    async function refreshStatus() {
      try {
        status = await get('/api/vpn/status');
        renderStatus();
      } catch (e) {
        clear(statusBox).appendChild(problem(e, refreshStatus));
      }
      return status;
    }

    function renderStatus() {
      const s = status;
      clear(statusBox);
      if (!s.running) { statusBox.appendChild(alert('warn', 'The router\'s VPN service is not running.')); return; }
      const ov = s.openvpn || {};
      const tone = s.connected ? 'ok' : (s.summary === 'Connecting' ? 'info' : 'neutral');
      const settings = h('div');
      statusBox.appendChild(h('div.grid-2',
        card('VPN status', [
          h('div', { style: { marginBottom: '10px' } }, badge(s.summary, tone)),
          kv([
            ['OpenVPN profile', ov.configured ? ov.config_file : 'None'],
            ov.configured && ['Server', h('span.mono', ov.server || '—')],
            ...statsRows(ov.stats),
          ]),
        ], { icon: 'vpn', actions: [button('Refresh', refreshStatus, { small: true, icon: 'refresh' })] }),
        settings));
      form(settings, {
        title: 'Services', icon: 'services',
        description: 'Auto-connect starts the configured VPN whenever the mobile connection comes up.',
        load: async () => ({ auto_connect: s.auto_connect, ipsec: s.services.ipsec, wireguard: s.services.wireguard }),
        save: async (ch) => {
          const r = await put('/api/vpn/settings', ch);
          if (r.reboot_required) toast('Saved. Restart the router for the service change to take effect.', 'ok', 8000);
          status = r; renderStatus();
          return { auto_connect: r.auto_connect, ipsec: r.services.ipsec, wireguard: r.services.wireguard };
        },
        fields: [
          { key: 'auto_connect', label: 'Auto-connect', type: 'switch' },
          { key: 'wireguard', label: 'WireGuard service', type: 'switch', help: 'Needed for WireGuard tunnels. May need a restart.' },
          { key: 'ipsec', label: 'IPsec service', type: 'switch',
            help: s.supported && s.supported.ipsec === false ? 'This unit reports IPsec as not supported; the router may refuse it.' : 'Needed for IPsec tunnels. May need a restart.' },
        ],
      });
    }

    function renderTabs() {
      clear(tabBar).append(...TABS.map(([id, label]) => h('button.tab' + (id === tab ? '.active' : ''),
        { type: 'button', role: 'tab', 'aria-selected': String(id === tab), onclick: () => { tab = id; renderTabs(); renderBody(); } }, label)));
    }

    function renderBody() {
      clear(body);
      ({ openvpn: openvpnTab, wireguard: wireguardTab, ipsec: ipsecTab, log: logTab }[tab])(body);
    }

    // ---- OpenVPN -------------------------------------------------------------------------
    function openvpnTab(box) {
      const ov = (status && status.openvpn) || {};
      if (ov.configured) {
        const st = ov.stats || {};
        const connected = st.state === 'connected' || st.state === 'connecting';
        box.appendChild(card('Saved profile', kv([
          ['Profile file', h('span.mono', ov.config_file)], ['Server', h('span.mono', ov.server || '—')],
          ['User name', ov.username || '—'], ['Password', ov.password_set ? 'Saved' : 'None'], ...statsRows(st),
        ]), { icon: 'lock', actions: [
          connected ? button('Disconnect', async () => { status = await post('/api/vpn/openvpn/disconnect'); renderStatus(); renderBody(); }, { small: true })
            : button('Connect', async () => { status = await post('/api/vpn/openvpn/connect'); renderStatus(); renderBody(); toast('Connecting…'); }, { small: true, kind: 'primary' }),
          button('Show password', async () => {
            const sec = await get('/api/vpn/openvpn/secret');
            modal({ title: 'OpenVPN credentials', body: kv([['User name', sec.username], ['Password', h('span.mono', sec.password)]]), actions: [{ label: 'Close' }] });
          }, { small: true }),
          button('Remove profile', async () => {
            if (!(await confirmSimple('Remove profile', 'Delete the OpenVPN profile, its files and the VPN log from the router?', 'Remove', 'danger'))) return;
            status = await del('/api/vpn/openvpn'); renderStatus(); renderBody(); toast('Profile removed');
          }, { small: true, kind: 'danger' })] }));
      }
      const host = h('div');
      box.appendChild(host);
      let picked = [];
      form(host, {
        title: ov.configured ? 'Replace the profile' : 'Set up OpenVPN', icon: 'upload',
        description: 'Upload the .ovpn profile from your VPN provider together with every certificate or key file it refers to (or a profile with inline certificates). The router accepts "dev tun" client profiles only and refuses directives that run scripts or change routing.',
        saveLabel: 'Save profile', savedMessage: 'Profile saved',
        load: async () => ({ files: [], username: ov.username || '', password: '', retries: 3, connect: true }),
        save: async (ch, v) => {
          const files = await readFiles(v.files);
          const s = await post('/api/vpn/openvpn', { files, username: v.username, password: v.password, retries: Number(v.retries) || 0, connect: !!v.connect });
          status = s; renderStatus(); renderBody();
          return undefined;
        },
        actions: [button('Check files', async () => {
          if (!picked.length) { toast('Choose the profile files first', 'bad'); return; }
          const r = await post('/api/vpn/openvpn/validate', { files: await readFiles(picked) });
          toast(`The profile looks good (server ${r.server || 'unknown'})`);
        }, { small: true })],
        fields: [
          { key: 'files', label: 'Profile files', type: 'custom', full: true, required: true,
            validate: (v) => (v && v.length) ? null : 'Choose the .ovpn file (and its certificate files)',
            render: (values, set) => {
              const inp = h('input', { type: 'file', multiple: true, accept: '.ovpn,.conf,.crt,.pem,.key,.cer,.der,.p12,.txt',
                onchange: (e) => { picked = Array.from(e.target.files); set('files', picked); } });
              const names = (values.files || []).map(f => f.name);
              return h('div', inp, names.length ? h('p.hint', 'Selected: ' + names.join(', ')) : h('p.hint', 'You can select several files at once.'));
            } },
          { key: 'username', label: 'User name', type: 'text', required: true, maxLength: 128, autocomplete: 'off' },
          { key: 'password', label: 'Password', type: 'password', required: true, maxLength: 128 },
          { key: 'retries', label: 'Connection attempts', type: 'number', min: 0, max: 100, help: 'How often the router retries a failed connection.' },
          { key: 'connect', label: 'Connect after saving', type: 'switch' },
        ],
      });
    }

    // ---- WireGuard -----------------------------------------------------------------------
    function wireguardTab(box) {
      const info = h('div');
      box.appendChild(info);
      const host = h('div');
      box.appendChild(host);
      const action = (path) => async (row) => { await post(`/api/vpn/wireguard/${row.index}/${path}`); lst.reload(); refreshStatus(); };
      const lst = list(host, {
        title: 'WireGuard tunnels', icon: 'vpn', editTitle: 'WireGuard tunnel', wideForm: true,
        description: 'Up to three WireGuard tunnels. Give the router\'s public key to the server after saving.',
        load: async () => {
          const r = await get('/api/vpn/wireguard');
          clear(info);
          if (r.service_enabled === false) info.appendChild(alert('info', 'The WireGuard service is off. Turn it on under Services above to use these tunnels.'));
          return r.tunnels;
        },
        columns: [
          { key: 'index', label: 'Tunnel', format: (v, r) => `${v + 1}: ${r.name}` },
          { key: 'stats', label: 'Status', format: (v, r) => stateBadge(v, r.configured) },
          { key: 'endpoint', label: 'Server', format: (v, r) => r.configured ? `${v}:${r.endpoint_port}` : '—', cls: 'mono' },
          { key: 'tunnel_ip', label: 'Tunnel address', cls: 'mono' },
          { key: 'public_key', label: 'Router public key', format: (v) => v ? h('span.mono', v) : '—', cls: 'mono' },
        ],
        rowActions: (row) => row.configured ? [
          row.stats && ['connected', 'connecting'].includes(row.stats.state)
            ? button('Disconnect', () => action('disconnect')(row), { small: true })
            : button('Connect', () => action('connect')(row), { small: true }),
          button('Key', async () => {
            const s = await get(`/api/vpn/wireguard/${row.index}/secret`);
            modal({ title: 'Private key of ' + row.name, body: h('div', h('p', 'Keep this key secret.'), h('p.mono', s.private_key || '(none)')), actions: [{ label: 'Close' }] });
          }, { small: true })] : [],
        describe: (r) => `WireGuard tunnel ${r.index + 1} (${r.name})`,
        fields: [
          { type: 'section', label: 'Tunnel' },
          { key: 'name', label: 'Interface name', type: 'text', required: true, maxLength: 15, pattern: /^[A-Za-z0-9_-]+$/,
            patternMsg: 'Letters, digits, _ or -', help: 'Keep the suggested name (wg1, wg2 …) unless you have a reason to change it.' },
          { key: 'enabled', label: 'Tunnel enabled', type: 'switch' },
          { key: 'startup', label: 'Start', type: 'select', options: [['manual', 'Manually'], ['auto', 'Automatically when online']] },
          { key: 'retries', label: 'Connection attempts', type: 'number', min: 0, max: 100 },
          { type: 'section', label: 'Keys' },
          { key: 'private_key', label: 'Router private key', type: 'password', generate: randomWgKey,
            placeholder: '', help: (v) => v.private_key_set ? 'Leave empty to keep the stored key, or press Generate for a new one.' : 'Press Generate, or paste a key.',
            validate: (v, vals) => v ? (WG_KEY.test(v) ? null : '44 characters ending with =') : (vals.private_key_set ? null : 'Required') },
          { key: 'public_key', label: 'Router public key', type: 'readonly', format: (v) => v || 'Shown after saving', full: true },
          { key: 'peer_public_key', label: 'Server public key', type: 'text', required: true, full: true,
            validate: (v) => WG_KEY.test(v || '') ? null : '44 characters ending with =' },
          { key: 'psk', label: 'Pre-shared key (optional)', type: 'password', help: (v) => v.psk_set ? 'A pre-shared key is stored. Leave empty to keep it.' : 'Only if the server uses one.',
            validate: (v) => !v || WG_KEY.test(v) ? null : '44 characters ending with =' },
          { key: 'psk_clear', label: 'Remove the stored pre-shared key', type: 'checkbox', show: (v) => !!v.psk_set },
          { type: 'section', label: 'Addresses' },
          { key: 'endpoint', label: 'Server address', type: 'text', required: true, help: 'IPv4 address or host name' },
          { key: 'endpoint_port', label: 'Server port', type: 'number', min: 1, max: 65535, required: true },
          { key: 'tunnel_ip', label: 'Router tunnel address', type: 'text', required: true, placeholder: '10.0.0.2', help: 'IPv4; the router uses a /24 network' },
          { key: 'peer_ip', label: 'Server tunnel address', type: 'text', required: true, placeholder: '10.0.0.1' },
          { key: 'allowed_ips', label: 'Networks through the tunnel', type: 'text', required: true, full: true,
            format: (v) => Array.isArray(v) ? v.join(', ') : v, placeholder: '0.0.0.0/0',
            help: '0.0.0.0/0 sends all traffic through the tunnel; or list networks separated by commas.' },
          { key: 'listen_port', label: 'Router listen port', type: 'number', min: 1, max: 65535 },
          { key: 'keepalive', label: 'Keepalive', type: 'number', min: 1, max: 65535, unit: 'seconds' },
        ],
        update: async (row) => {
          const body = {};
          for (const k of ['name', 'enabled', 'startup', 'retries', 'peer_public_key', 'endpoint', 'endpoint_port', 'tunnel_ip',
            'peer_ip', 'allowed_ips', 'listen_port', 'keepalive']) if (row[k] !== undefined) body[k] = row[k];
          if (row.private_key) body.private_key = row.private_key;
          if (row.psk) body.psk = row.psk;
          if (row.psk_clear) body.psk_clear = true;
          await put(`/api/vpn/wireguard/${row.index}`, body);
          refreshStatus();
        },
        remove: async (row) => { await del(`/api/vpn/wireguard/${row.index}`); refreshStatus(); },
      });
    }

    // ---- IPsec ---------------------------------------------------------------------------
    function ipsecTab(box) {
      const info = h('div');
      box.appendChild(info);
      const host = h('div');
      box.appendChild(host);
      const action = (path) => async (row) => { await post(`/api/vpn/ipsec/${row.index}/${path}`); lst.reload(); };
      const lst = list(host, {
        title: 'IPsec tunnels', icon: 'vpn', editTitle: 'IPsec tunnel', wideForm: true,
        description: 'Up to six IPsec (IKEv1/IKEv2) tunnels with a pre-shared key or EAP-MSCHAPv2 login. The router never shows stored keys or passwords, so enter them again whenever you save.',
        load: async () => {
          const r = await get('/api/vpn/ipsec');
          clear(info);
          if (r.supported === false) info.appendChild(alert('warn', 'This unit reports IPsec as not supported. The router may refuse to save or start IPsec tunnels.'));
          else if (r.service_enabled === false) info.appendChild(alert('info', 'The IPsec service is off. Turn it on under Services above.'));
          return r.tunnels.map(t => Object.assign({}, t, { dpd_enabled: t.dpd.enabled, dpd_action: t.dpd.action, dpd_delay: t.dpd.delay, dpd_timeout: t.dpd.timeout }));
        },
        columns: [
          { key: 'index', label: 'Tunnel', format: (v, r) => `${v + 1}: ${r.name}` },
          { key: 'stats', label: 'Status', format: (v, r) => stateBadge(v, r.configured) },
          { key: 'remote_ip', label: 'Server', format: (v, r) => r.configured ? v : '—', cls: 'mono' },
          { key: 'remote_subnet', label: 'Remote network', format: (v, r) => r.configured ? v : '—', cls: 'mono' },
        ],
        rowActions: (row) => row.configured ? [row.stats && ['connected', 'connecting'].includes(row.stats.state)
          ? button('Disconnect', () => action('disconnect')(row), { small: true })
          : button('Connect', () => action('connect')(row), { small: true })] : [],
        describe: (r) => `IPsec tunnel ${r.index + 1} (${r.name})`,
        fields: [
          { type: 'section', label: 'Connection' },
          { key: 'name', label: 'Connection name', type: 'text', required: true, maxLength: 32, pattern: /^[A-Za-z0-9_-]+$/, patternMsg: 'Letters, digits, _ or -' },
          { key: 'enabled', label: 'Tunnel enabled', type: 'switch' },
          { key: 'startup', label: 'Start', type: 'select', options: [['manual', 'Manually'], ['auto', 'Automatically when online']] },
          { key: 'ike_version', label: 'IKE version', type: 'select', options: [[2, 'IKEv2'], [1, 'IKEv1']] },
          { key: 'remote_ip', label: 'Server address', type: 'text', help: 'IPv4 address or host name' },
          { key: 'remote_subnet', label: 'Remote network', type: 'text', placeholder: '10.0.0.0/24' },
          { key: 'local_ip', label: 'Router address (optional)', type: 'text', show: (v) => v.client_auth !== 'eap', help: 'Leave empty to use the mobile connection address.' },
          { key: 'local_subnet', label: 'Local network', type: 'text', show: (v) => v.client_auth !== 'eap', placeholder: '192.168.1.0/24' },
          { key: 'local_id', label: 'Local identity', type: 'text', placeholder: '%any' },
          { key: 'remote_id', label: 'Remote identity', type: 'text', placeholder: '%any' },
          { key: 'anonymous', label: 'Accept any remote peer', type: 'switch', help: 'Server-like mode (remote address %any).' },
          { key: 'left_firewall', label: 'Add firewall rules for the tunnel', type: 'switch' },
          { type: 'section', label: 'Authentication' },
          { key: 'client_auth', label: 'Router authenticates with', type: 'select', options: [['psk', 'Pre-shared key'], ['eap', 'EAP-MSCHAPv2 user name and password']] },
          { key: 'client_psk', label: 'Pre-shared key', type: 'password', show: (v) => v.client_auth !== 'eap', required: true, minLength: 8 },
          { key: 'client_username', label: 'User name', type: 'text', show: (v) => v.client_auth === 'eap', required: true },
          { key: 'client_password', label: 'Password', type: 'password', show: (v) => v.client_auth === 'eap', required: true },
          { key: 'server_auth', label: 'Server authenticates with', type: 'select', options: [['psk', 'Pre-shared key'], ['eap', 'EAP-MSCHAPv2']] },
          { key: 'server_psk', label: 'Server pre-shared key', type: 'password', show: (v) => v.server_auth !== 'eap', help: 'Leave empty to use the same key.' },
          { key: 'server_username', label: 'Server user name', type: 'text', show: (v) => v.server_auth === 'eap', required: true },
          { key: 'server_password', label: 'Server password', type: 'password', show: (v) => v.server_auth === 'eap', required: true },
          { type: 'section', label: 'Proposals and timers' },
          { key: 'phase1', label: 'Phase 1 (IKE) proposals', type: 'text', required: true, placeholder: 'aes256-sha2_256-modp2048',
            help: 'encryption-integrity-dhgroup, comma separated. Encryption: aes256, aes128, 3des …; integrity: sha1, sha2_256 …; DH: modp1024, modp2048 …' },
          { key: 'ike_lifetime', label: 'IKE lifetime', type: 'text', unit: 'seconds', maxLength: 6, pattern: /^[1-9]\d{0,5}$/, patternMsg: '1-999999' },
          { key: 'phase2', label: 'Phase 2 (ESP) proposals', type: 'text', required: true, placeholder: 'aes256-sha2_256' },
          { key: 'key_lifetime', label: 'Key lifetime', type: 'text', unit: 'seconds', maxLength: 6, pattern: /^[1-9]\d{0,5}$/, patternMsg: '1-999999' },
          { key: 'dpd_enabled', label: 'Dead peer detection', type: 'switch' },
          { key: 'dpd_action', label: 'When the peer is gone', type: 'select', show: (v) => !!v.dpd_enabled,
            options: [[3, 'Restart the tunnel'], [1, 'Close the tunnel'], [2, 'Hold'], [0, 'Do nothing']] },
          { key: 'dpd_delay', label: 'Check every', type: 'number', min: 1, max: 3600, unit: 'seconds', show: (v) => !!v.dpd_enabled },
          { key: 'dpd_timeout', label: 'Give up after', type: 'number', min: 1, max: 3600, unit: 'seconds', show: (v) => !!v.dpd_enabled },
        ],
        update: async (row) => {
          const body = {};
          for (const k of ['name', 'enabled', 'startup', 'ike_version', 'remote_ip', 'remote_subnet', 'local_ip', 'local_subnet', 'local_id',
            'remote_id', 'anonymous', 'left_firewall', 'client_auth', 'client_psk', 'client_username', 'client_password', 'server_auth',
            'server_psk', 'server_username', 'server_password', 'phase1', 'ike_lifetime', 'phase2', 'key_lifetime']) {
            if (row[k] !== undefined && row[k] !== '') body[k] = row[k];
          }
          body.ike_version = Number(row.ike_version) || 2;
          body.dpd = { enabled: !!row.dpd_enabled, action: Number(row.dpd_action), delay: Number(row.dpd_delay), timeout: Number(row.dpd_timeout) };
          await put(`/api/vpn/ipsec/${row.index}`, body);
        },
        remove: (row) => del(`/api/vpn/ipsec/${row.index}`),
      });
    }

    // ---- log -----------------------------------------------------------------------------
    function logTab(box) {
      const out = h('pre.pre.console');
      const src = h('select', { style: { maxWidth: '260px' } },
        [['openvpn', 'OpenVPN'], ['wireguard', 'WireGuard'], ['charon', 'IPsec (charon)'], ...[0, 1, 2, 3, 4, 5].map(i => [`ipsec:${i}`, `IPsec tunnel ${i + 1}`])]
          .map(([v, l]) => h('option', { value: v }, l)));
      const load = async () => {
        clear(out).append('Loading…');
        const [source, index] = src.value.split(':');
        try {
          const q = new URLSearchParams({ source, index: index || '0' });
          const r = await get('/api/vpn/log?' + q.toString());
          clear(out).append(r.missing ? '(no log file yet)' : (r.lines.length ? r.lines.join('\n') : '(empty)'));
        } catch (e) { clear(out).appendChild(problem(e, load)); }
      };
      src.addEventListener('change', load);
      box.appendChild(card('VPN log', [h('div', { style: { display: 'flex', gap: '8px', marginBottom: '10px' } }, src, button('Refresh', load, { small: true, icon: 'refresh' })), out], { icon: 'terminal' }));
      load();
    }

    clear(body).appendChild(spinner());
    renderTabs();
    await refreshStatus();
    renderBody();
    timer = setInterval(() => { if (!document.hidden && tab === 'openvpn') refreshStatus(); }, 15000);
    return () => clearInterval(timer);
  },
};
