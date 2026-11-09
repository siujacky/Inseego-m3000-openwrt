// Device › Display & LED: touchscreen timeout, what the screen shows, touchscreen controls, status light, buzzer test.
import { h } from '../../ui/dom.js';
import { card, alert, button, toast } from '../../ui/widgets.js';
import { get, put, post } from '../../api.js';
import { smartForm, secondOptions, serviceNote } from './_helpers.js';

const TIMEOUTS = [30, 60, 120, 300, 600, 28800, 0];

function displayForm(host) {
  smartForm(host, {
    title: 'Touchscreen', icon: 'device',
    description: 'What the screen on the router shows and what can be changed from it.',
    load: () => get('/api/device/display'),
    save: (ch) => put('/api/device/display', ch),
    warn: (ch) => (ch.touch_settings_enabled === false
      ? 'Settings (including the factory reset) can then no longer be changed on the router itself, only here. Continue?'
      : (ch.show_admin_password === true ? 'Anyone near the router can then read the admin password on its screen. Continue?' : null)),
    fields: (d) => [
      d.screen_service_running === false && { key: '_note', type: 'custom', full: true, noSave: true,
        render: () => serviceNote(false, 'touchscreen') },
      { key: 'screen_timeout_s', label: 'Turn the screen off after', type: 'select',
        options: secondOptions(TIMEOUTS, d.screen_timeout_s, 'Never'), help: 'A screen that stays on uses more battery.' },
      { type: 'section', label: 'What the screen shows' },
      { key: 'show_primary_ssid', label: 'Main Wi-Fi name', type: 'switch', onLabel: 'Shown', offLabel: 'Hidden' },
      { key: 'show_primary_password', label: 'Main Wi-Fi password', type: 'switch', onLabel: 'Shown', offLabel: 'Hidden' },
      { key: 'show_guest_ssid', label: 'Guest Wi-Fi name', type: 'switch', onLabel: 'Shown', offLabel: 'Hidden' },
      { key: 'show_guest_password', label: 'Guest Wi-Fi password', type: 'switch', onLabel: 'Shown', offLabel: 'Hidden' },
      { key: 'show_data_usage', label: 'Data usage', type: 'switch', onLabel: 'Shown', offLabel: 'Hidden' },
      { key: 'show_ethernet', label: 'Ethernet port settings', type: 'switch', onLabel: 'Shown', offLabel: 'Hidden' },
      { key: 'show_file_sharing', label: 'File sharing', type: 'switch', onLabel: 'Shown', offLabel: 'Hidden' },
      { key: 'show_admin_password', label: 'Admin password', type: 'switch', onLabel: 'Shown', offLabel: 'Hidden',
        help: (v) => (v.show_admin_password === null || v.show_admin_password === undefined
          ? 'Current state could not be read; choose explicitly to set it.' : 'Shows the password for this web interface on the screen.') },
      { type: 'section', label: 'Touchscreen controls' },
      { key: 'touch_settings_enabled', label: 'Allow changing settings on the screen', type: 'switch',
        help: 'Includes the factory reset from the screen menu.' },
      { key: 'touch_wifi_settings_enabled', label: 'Allow Wi-Fi settings on the screen', type: 'switch' },
      { key: 'usb_prompt_enabled', label: 'Ask what to do when USB is connected', type: 'switch',
        help: 'The choice made on the screen applies to that connection only.' },
      { key: 'wake_on_notification', label: 'Wake the screen for alerts', type: 'switch' },
      { key: 'quickstart_enabled', label: 'Show the quick-start guide', type: 'switch' },
      { key: 'vital_update_prompt', label: 'Ask about important software updates', type: 'switch' },
    ].filter(Boolean),
  });
}

function ledForm(host) {
  smartForm(host, {
    title: 'Status light', icon: 'dot',
    description: 'The light on the front of the router. Stealth mode (Services › Notifications) also turns it off.',
    load: () => get('/api/device/led'),
    save: (ch) => put('/api/device/led', ch),
    fields: [{ key: 'enabled', label: 'Status light', type: 'switch', full: true }],
  });
}

function buzzerCard(host) {
  const on = h('input', { type: 'number', min: 10, max: 1000, value: 100, inputmode: 'numeric', 'aria-label': 'Sound length in milliseconds' });
  const off = h('input', { type: 'number', min: 10, max: 1000, value: 100, inputmode: 'numeric', 'aria-label': 'Pause in milliseconds' });
  const rep = h('input', { type: 'number', min: 1, max: 5, value: 2, inputmode: 'numeric', 'aria-label': 'Repeat count' });
  const play = button('Play', async () => {
    await post('/api/device/buzzer', { on_ms: Number(on.value), off_ms: Number(off.value), repeat: Number(rep.value) });
    toast('Beep sent');
  }, { kind: 'primary', icon: 'check' });
  const stop = button('Stop', async () => { await post('/api/device/buzzer/stop', {}); }, {});
  host.appendChild(card('Buzzer test', [
    h('p.card-desc', 'Plays a short beep pattern to check the buzzer (sound 10–1000 ms, pause 10–1000 ms, up to 5 times).'),
    h('div.form-grid',
      h('div.field', h('label.field-label', 'Sound (ms)'), h('div.field-control', on)),
      h('div.field', h('label.field-label', 'Pause (ms)'), h('div.field-control', off)),
      h('div.field', h('label.field-label', 'Repeat'), h('div.field-control', rep))),
    h('div.form-actions', { style: { marginTop: '12px' } }, play, stop),
  ], { icon: 'sms' }));
}

export default {
  id: 'device-display', path: '/device/display', group: 'device', order: 20, title: 'Display & LED', icon: 'device',
  feature: 'device.display',
  render(el) {
    const grid = h('div.grid-2'), right = h('div');
    el.append(grid);
    const left = h('div');
    grid.append(left, right);
    displayForm(left);
    ledForm(right);
    buzzerCard(right);
    right.appendChild(card('Reset button', [
      alert('info', 'The hardware reset button can be enabled or disabled on the System › Restart & reset page.'),
      h('div.form-actions', h('a.btn.btn-sm', { href: '#/system/power' }, 'Open Restart & reset')),
    ], { icon: 'refresh' }));
  },
};
