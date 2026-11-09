import { h, card, kv, button, alert, toast, form, get, post, block, record, pageScope, heading, modemSupports } from './_shared.js';
export default { id: 'native-sms', path: '/openwrt/sms', group: 'cellular', order: 2, title: 'Messages', feature: 'openwrt',
  render(el) {
    heading(el, 'Messages', 'Read and send SMS through the native modem provider.');
    const scope = pageScope(), provider = block(el), messages = block(el), compose = block(el);
    const loadMessages = () => scope.section(messages, () => get('/api/openwrt/sms'), data => {
      const items = Array.isArray(data) ? data : data.messages || [];
      return card('Inbox', [items.length ? items.map(m => card(m.number || m.sender || 'Message', kv([
        ['From', m.number || m.sender], ['Received', m.timestamp || m.time], ['Message', m.text || m.body],
      ]))) : h('p.muted', 'No messages returned.'), record(data), button('Refresh messages', loadMessages)]);
    });
    scope.section(provider, () => get('/api/openwrt/modem'), data => {
      const supported = modemSupports(data, 'sms');
      if (supported) loadMessages();
      form(compose, { title: 'Send a message', readonly: !supported,
        description: supported ? 'Sending a message may incur carrier charges.' : data.reason || 'The modem provider does not currently support SMS.',
        load: async () => ({ number: '', text: '' }),
        save: async changes => { await post('/api/openwrt/sms', changes); toast('Message submitted'); return { number: '', text: '' }; },
        fields: [{ key: 'number', label: 'Phone number', required: true, maxLength: 24, pattern: /^\+?[0-9]{3,24}$/ },
          { key: 'text', label: 'Message', type: 'textarea', required: true, maxLength: 160 }] });
      return supported ? alert('info', 'SMS provider available.') : alert('warn', data.reason || 'SMS is unavailable. Sending is disabled.');
    });
    return scope.destroy;
  },
};
