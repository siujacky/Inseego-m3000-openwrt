import { h, card, badge, button, alert, toast, form, get, put, post, del, block, raw, record, enc, security, confirmMessage, pageScope, heading, modemSupports } from './_shared.js';
import { wifiCreateBody, hardwareSummary } from '../system/_native.js';
export default { id: 'native-system', path: '/openwrt/system', group: 'system', order: 5, title: 'General settings', feature: 'openwrt',
render(el, ctx) {
  heading(el, 'General settings', 'Router name, time zone and available system readings.');
  const scope = pageScope(), section = scope.section;
const systemBox = block(el);
    section(systemBox, () => get('/api/openwrt/system'), d => {
      const host = h('div');
      form(host, { title: 'System name and time zone', icon: 'settings',
        load: async () => d.config,
        save: async ch => { const out = await put('/api/openwrt/system', ch, { confirmMessage }); return out.config; },
        fields: [{ key: 'hostname', label: 'Host name', required: true, maxLength: 63 },
          { key: 'timezone', label: 'POSIX time zone', help: 'For example UTC0 or CST6CDT,M3.2.0,M11.1.0', maxLength: 100 },
          { key: 'zonename', label: 'Time zone name', help: 'For example America/Chicago', maxLength: 100 }] });
      return [host, card('System and power telemetry', record({ board: d.board, info: d.info, power: d.power, thermal: d.thermal, cpu: d.cpu }))];
    });


  const settingsBox = block(el);
  form(settingsBox, { title: 'Web interface', description: 'Browser management settings. Refresh navigation after changing the router name or expert tools.',
    load: () => get('/api/settings'), save: changes => put('/api/settings', changes),
    actions: [button('Refresh navigation', () => ctx.refreshShell())],
    fields: [{ key: 'router_name', label: 'Router name', required: true, maxLength: 32 },
      { key: 'expert_mode', label: 'Show expert tools', type: 'switch' },
      { key: 'session_idle_s', label: 'Session idle timeout', type: 'number', unit: 'seconds', min: 120, max: 86400 },
      { key: 'status_poll_s', label: 'Status refresh interval', type: 'number', unit: 'seconds', min: 2, max: 60 }] });
  return scope.destroy;
} };
