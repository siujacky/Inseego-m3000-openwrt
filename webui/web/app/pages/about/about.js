// About: model, versions, identifiers (masked; reveal on request), MAC addresses, system, notices, vendor fallback.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, button, spinner, errorBox, alert } from '../../ui/widgets.js';
import { get } from '../../api.js';
import * as fmt from '../../ui/format.js';

const ID_LABELS = [['imei', 'IMEI'], ['imeisv', 'IMEI software version'], ['meid', 'MEID'], ['esn', 'ESN'],
  ['serial', 'Serial number'], ['iccid', 'SIM card number (ICCID)'], ['phone_number', 'Phone number']];

function mono(v) { return v ? h('span.mono', v) : ''; }

function vendorLink(port) {
  const host = location.hostname.includes(':') ? `[${location.hostname}]` : location.hostname;
  const url = `${location.protocol}//${host}${port && port !== 80 ? ':' + port : ''}/`;
  return url;
}

export default {
  id: 'about', path: '/about', group: 'about', order: 10, title: 'About', icon: 'about',
  async render(el, ctx) {
    el.appendChild(spinner());
    let d;
    try { d = await get('/api/about'); } catch (e) {
      clear(el).appendChild(e.status === 503 ? alert('warn', e.message) : errorBox(e, () => this.render(clear(el), ctx)));
      return;
    }
    clear(el);
    const dev = d.device || {}, sw = d.software || {}, sys = d.system || {};
    const g1 = h('div.grid-2'), g2 = h('div.grid-2'), g3 = h('div.grid-2');
    el.append(g1, g2, g3);

    g1.append(
      card('Device', kv([
        ['Model', dev.model], ['Manufacturer', dev.manufacturer], ['Hardware version', dev.hardware_version],
        ['Product code (SKU)', mono(dev.product_sku)], ['Factory ID', mono(dev.factory_id)],
        ['Radio technologies', dev.radio_technologies && dev.radio_technologies.length ? h('span.chips', dev.radio_technologies.map(t => badge(t, 'accent'))) : ''],
      ]), { icon: 'device' }),
      card('Software', kv([
        ['Firmware', mono(sw.firmware)], ['Modem firmware', mono(sw.modem_firmware)], ['Wi-Fi firmware', mono(sw.wifi_firmware)],
        ['Operating system', sw.os_version], ['Vendor web interface', sw.vendor_web_ui],
        sw.device_screen_ui && ['Device screen software', sw.device_screen_ui],
        ['Software build', sw.software_version], ['Carrier configuration (PRI)', sw.pri_version],
        sw.pri_customization && ['PRI customisation', sw.pri_customization],
        ['Configuration version', sw.configuration_version], ['PRL / ERI', [sw.prl_version, sw.eri_version].filter(x => x !== '' && x !== undefined).join(' / ')],
        ['REST API', sw.rest_api], ['Kernel', h('span.mono', { style: { fontSize: '12px' } }, sw.kernel || '')],
        ['This interface', sw.this_ui],
      ]), { icon: 'chip' }));

    // identifiers: masked, reveal on request
    const idBody = h('div');
    const ids = Object.assign({}, d.identifiers || {});
    const present = d.identifiers_present || {};
    let revealed = false;
    const drawIds = () => {
      clear(idBody).appendChild(kv(ID_LABELS.filter(([k]) => present[k]).map(([k, label]) => [label, mono(ids[k])])));
      if (!ID_LABELS.some(([k]) => present[k])) idBody.appendChild(h('p.hint', 'No identifiers reported.'));
    };
    drawIds();
    const revealBtn = button('Show', async () => {
      if (revealed) { Object.assign(ids, d.identifiers); revealed = false; revealBtn.lastChild.textContent = 'Show'; drawIds(); return; }
      Object.assign(ids, await get('/api/about/identifiers'));
      revealed = true; revealBtn.lastChild.textContent = 'Hide'; drawIds();
    }, { small: true, icon: 'eye' });
    g2.append(
      card('Identifiers', [h('p.card-desc', 'Unique numbers of this router and its SIM. They are hidden by default; avoid sharing them.'), idBody],
        { icon: 'sim', actions: revealBtn }),
      card('Network addresses', (d.macs || []).length ? h('div.table-wrap', h('table.table',
        h('thead', h('tr', h('th', 'Interface'), h('th', 'MAC address'))),
        h('tbody', d.macs.map(m => h('tr', h('td', { dataset: { label: 'Interface' } }, m.name), h('td.mono', { dataset: { label: 'MAC address' } }, m.mac))))))
        : h('p.hint', 'No MAC addresses reported.'), { icon: 'network' }));

    const temps = (sys.temperatures || []).slice(0, 4).map(t => `${t.type || t.zone} ${t.celsius}°C`).join(' · ');
    const cpu = sys.cpu || {};
    const port = (d.vendor_ui && d.vendor_ui.port) || 80;
    const url = vendorLink(port);
    const samePort = String(location.port || (location.protocol === 'https:' ? 443 : 80)) === String(port);
    g3.append(
      card('System', kv([
        ['Running for', fmt.duration(sys.uptime_s)], ['Started', fmt.dateTime(sys.boot_time)], ['Host name', sys.hostname],
        ['Processor', [cpu.model, cpu.cores ? `${cpu.cores} core${cpu.cores > 1 ? 's' : ''}` : '', cpu.freq_mhz ? `${cpu.freq_mhz} MHz` : ''].filter(Boolean).join(', ')],
        ['Memory', sys.memory_total ? `${fmt.bytes(sys.memory_available)} free of ${fmt.bytes(sys.memory_total)}` : ''],
        ['Load', (sys.load || []).join(' ')], ['Temperatures', temps],
      ]), { icon: 'system' }),
      card('Original vendor interface', [
        h('p.card-desc', (d.vendor_ui && d.vendor_ui.note) || ''),
        samePort ? h('p.hint', 'It is not reachable on a separate port in the current installation mode.')
          : h('p', h('a.btn', { href: url, target: '_blank', rel: 'noopener noreferrer' }, 'Open ' + url)),
        h('p.hint', 'It uses the same admin password as this interface.'),
      ], { icon: 'link' }));

    el.appendChild(card('Open-source notices', h('div.table-wrap', h('table.table',
      h('thead', h('tr', h('th', 'Component'), h('th', 'Licence'), h('th', 'Notes'))),
      h('tbody', (d.open_source || []).map(o => h('tr', h('td', { dataset: { label: 'Component' } }, o.name),
        h('td', { dataset: { label: 'Licence' } }, o.license), h('td', { dataset: { label: 'Notes' } }, o.note)))))), { icon: 'about' }));
  },
};
