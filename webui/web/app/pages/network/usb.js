// Network › USB: tethering, Wi-Fi while tethered, fixed host IP, default USB mode (standard / debug with
// ADB, the mod-ROM feature) and an immediate mode switch. Mode changes are tier 2.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, button, toast } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import { block, safeForm, banner, ip4Msg } from './_net.js';

const FUNC = { rndis: 'network (RNDIS)', ecm: 'network (ECM)', ncm: 'network (NCM)', mass_storage: 'USB storage', adb: 'ADB',
  diag: 'diagnostics', dun: 'modem (DUN)', hid: 'HID', serial: 'serial', rmnet: 'modem data', dpl: 'data logging', qdss: 'trace' };

function chips(list) {
  return h('span.chips', (list || []).map(f => h('span.chip.on', FUNC[f] || f)));
}

export default {
  id: 'network-usb', path: '/network/usb', group: 'network', order: 60, title: 'USB', icon: 'usb', feature: 'network.usb',
  async render(el) {
    const note = banner(el);
    const statusBox = block(el), tetherBox = block(el), modeBox = block(el);
    let usb = {};

    const showStatus = (d) => {
      const s = d.state || {};
      clear(statusBox).appendChild(card('USB status', kv([
        ['Connection', s.configured ? badge('Connected to a computer', 'ok') : badge('Not connected', 'neutral')],
        ['USB tethering', d.tethering_enabled === null || d.tethering_enabled === undefined ? '—' : (d.tethering_enabled ? 'On' : 'Off (charge only)')],
        ['Current mode', s.default_mode_label || '—', s.start_mode ? ` · ${s.start_mode}` : ''],
        ['Functions', s.functions && s.functions.length ? chips(s.functions) : '—'],
        ['Network interface', s.net_if ? h('span.mono', `${s.net_if} (${s.net_function || '?'})`) : '—'],
        ['MAC (computer side)', d.mac_host ? h('span.mono', d.mac_host) : '—'],
        ['MAC (router side)', d.mac_device ? h('span.mono', d.mac_device) : '—'],
        ['Devices on USB', d.clients === null || d.clients === undefined ? '—' : String(d.clients)],
      ]), { icon: 'usb' }));
    };

    safeForm(tetherBox, {
      title: 'USB tethering', icon: 'usb',
      description: 'Share the internet connection with a computer over the USB cable.',
      load: async () => { usb = await get('/api/usb'); showStatus(usb); return usb; },
      save: async (ch) => { usb = await put('/api/usb', ch); showStatus(usb); return usb; },
      warn: (ch) => ch.tethering_enabled === false
        ? 'The computer on the USB cable loses its network connection. If you are using this page over USB, it stops responding.'
        : null,
      fields: [
        { key: 'tethering_enabled', label: 'USB tethering', type: 'switch', disabled: v => v.tethering_supported === false,
          help: 'Off = the USB cable only charges (the original "charge only" mode).' },
        { key: 'wifi_while_tethered', label: 'Keep Wi-Fi on while tethered', type: 'switch',
          help: 'Off = Wi-Fi turns off while a computer uses USB tethering (the original "USB only" mode).' },
        { key: 'static_ip', label: 'Fixed IP for the tethered computer', placeholder: 'empty = off', validate: (x) => x ? ip4Msg(x) : null,
          help: 'Vendor setting "static IP for Ethernet or USB tethering". Leave empty unless you need it.' },
      ],
    });

    const modeForm = safeForm(modeBox, {
      title: 'USB mode', icon: 'chip',
      description: 'Which functions the USB port offers. Both modes keep the USB network connection.',
      load: async () => { const d = await get('/api/usb'); return { default_mode: d.default_mode, compositions: d.compositions || {} }; },
      save: async (ch, v) => {
        const d = await put('/api/usb/default-mode', { mode: v.default_mode }, { confirmTitle: 'Change the USB mode',
          confirmMessage: `Default USB mode → ${v.default_mode === 'debug' ? 'debug (ADB and diagnostics ports)' : 'standard'}. Applies at the next start; the computer reconnects.` });
        note.restart(d, 'The new USB mode');
        return { default_mode: d.default_mode, compositions: d.compositions || {} };
      },
      fields: [
        { key: 'default_mode', label: 'Mode after start-up', type: 'radio', options: [
          ['standard', 'Standard', 'Network adapter and USB storage'],
          ['debug', 'Debug', 'Network adapter plus ADB, diagnostics and modem ports (as on mod ROMs)'],
        ], help: (v) => {
          const c = v.compositions || {};
          const fmtList = (l) => (l || []).map(f => FUNC[f] || f).join(', ') || 'network';
          return `Standard: ${fmtList(c.standard)}. Debug: ${fmtList(c.debug)}. Debug mode exposes service ports to the connected computer.`;
        } },
      ],
      actions: [
        button('Switch to standard now', async () => {
          const d = await post('/api/usb/mode-switch', { mode: 'standard' }, { confirmMessage: 'Switch the USB port to standard mode now? The computer disconnects for a few seconds.' });
          showStatus(d); toast('USB mode switched'); modeForm.reload();
        }, { kind: 'ghost' }),
        button('Switch to debug now', async () => {
          const d = await post('/api/usb/mode-switch', { mode: 'debug' }, { confirmMessage: 'Switch the USB port to debug mode (ADB, diagnostics) now? The computer disconnects for a few seconds.' });
          showStatus(d); toast('USB mode switched'); modeForm.reload();
        }, { kind: 'ghost' }),
      ],
    });
  },
};
