import { h, card, button, alert, toast, get, put, post, block, record, pageScope, heading, modemSupports } from './_shared.js';
export default { id: 'native-cellular', path: '/openwrt/cellular', group: 'cellular', order: 1, title: 'Modem and connection', feature: 'openwrt',
  render(el) {
    heading(el, 'Cellular', 'Connection controls depend on the native modem provider.');
    const scope = pageScope(), host = block(el);
    const refresh = () => scope.section(host, () => get('/api/openwrt/modem'), d => card('Modem status', [
      !d.available && alert('warn', d.reason || 'The modem provider is unavailable.'), record(d),
      h('div.form-actions',
        ...['connect', 'disconnect', 'reconnect'].map(action => button(action[0].toUpperCase() + action.slice(1), async () => {
          await post('/api/cellular/' + action, {}); toast('Modem action requested'); refresh();
        }, { disabled: !modemSupports(d, 'connect') })),
        ...[[false, 'Radio on'], [true, 'Radio off']].map(([airplane, title]) => button(title, async () => {
          await put('/api/cellular/mode', { airplane }); toast('Radio action requested'); refresh();
        }, { disabled: !modemSupports(d, 'mode') })), button('Refresh status', refresh)),
      (!modemSupports(d, 'connect') || !modemSupports(d, 'mode')) && h('p.hint', 'Unavailable connection or radio controls are disabled. The provider must advertise support.'),
      h('a.btn', { href: '#/openwrt/network' }, 'APN and upstream settings'), h('a.btn', { href: '#/openwrt/sms' }, 'Messages'),
    ]));
    refresh(); return scope.destroy;
  },
};
