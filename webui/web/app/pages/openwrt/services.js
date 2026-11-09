import { h, card, badge, button, alert, toast, form, get, put, post, del, block, raw, record, enc, security, confirmMessage, pageScope, heading, modemSupports } from './_shared.js';
import { wifiCreateBody, hardwareSummary } from '../system/_native.js';
export default { id: 'native-services', path: '/openwrt/services', group: 'system', order: 5, title: 'Services', feature: 'openwrt',
render(el) {
  heading(el, 'Services', 'Manage installed services. Protected management actions remain disabled.');
  const scope = pageScope(), section = scope.section;
const servicesBox = block(el);
    const loadServices = () => section(servicesBox, () => get('/api/openwrt/services'), d => card('Native services', d.map(service => h('div.service-row',
      h('div.field-label', service.name + (service.installed ? '' : ' (not installed)')), record(service.runtime),
      service.blocked_actions && service.blocked_actions.length && h('p.hint', 'USB management protection blocks: ' + service.blocked_actions.join(', ') + '.'),
      h('div.form-actions', ['start', 'stop', 'restart', 'reload'].map(action => button(action, async () => {
        await post('/api/openwrt/services/' + enc(service.name) + '/' + action, {}, { confirmMessage }); toast('Service action requested'); loadServices();
      }, { disabled: !service.installed || (service.blocked_actions || []).includes(action) })))))));
    loadServices();


  return scope.destroy;
} };
