import { h, card, button, get, block, pageScope, heading } from './_shared.js';
export default { id: 'native-logs', path: '/openwrt/logs', group: 'system', order: 30, title: 'System log', feature: 'openwrt',
  render(el) {
    heading(el, 'System log', 'Read recent messages without restarting any services.');
    const scope = pageScope();
    const lines = h('select', { 'aria-label': 'Log lines', onchange: () => load() }, [50, 100, 250, 500].map(n => h('option', { value: n, selected: n === 100 }, n + ' lines')));
    const controls = h('div.form-actions', lines, button('Refresh log', () => load()));
    el.appendChild(controls); const host = block(el);
    const load = () => scope.section(host, () => get('/api/openwrt/logs?lines=' + lines.value), d => card('Recent messages', h('pre.pre.log-output', d.output || 'No log messages')));
    load(); return scope.destroy;
  },
};
