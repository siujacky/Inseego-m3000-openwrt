// Shared native-page presentation. All mutations use the existing API client.
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, button, alert, errorBox, toast, spinner } from '../../ui/widgets.js';
import { form } from '../../ui/form.js';
import { get, put, post, del } from '../../api.js';
export { h, clear, card, kv, badge, button, alert, toast, form, get, put, post, del };
export const enc = encodeURIComponent;
export const security = [['open', 'Open'], ['wpa2', 'WPA2'], ['wpa3', 'WPA3'], ['wpa2_wpa3', 'WPA2/WPA3'], ['owe', 'Enhanced Open']];
export const confirmMessage = 'Applying this change can disconnect devices. Reconnect through USB or the configured network if this page stops responding.';
export function block(el) { const node = h('div.native-section'); el.appendChild(node); return node; }
export function raw(data) { return h('details.technical', h('summary', 'Technical details'), h('pre.pre', JSON.stringify(data, null, 2))); }
const label = key => key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
export function record(data) {
  const rows = [];
  function walk(value, prefix, depth) {
    prefix = prefix || 'Value';
    if (value === null || value === undefined) { rows.push([prefix, 'Unavailable']); return; }
    if (depth > 6) { rows.push([prefix, raw(value)]); return; }
    if (Array.isArray(value)) {
      if (value.every(v => v === null || typeof v !== 'object')) rows.push([prefix, value.join(', ') || 'None']);
      else value.forEach((item, i) => walk(item, (prefix === 'Value' ? 'Entry' : prefix) + ' ' + (i + 1), depth + 1));
      return;
    }
    if (typeof value === 'object') { Object.entries(value).forEach(([key, val]) => walk(val, prefix === 'Value' ? label(key) : prefix + ' · ' + label(key), depth + 1)); return; }
    rows.push([prefix, typeof value === 'boolean' ? value ? 'Yes' : 'No' : String(value)]);
  }
  walk(data, '', 0);
  return h('div', rows.length ? kv(rows) : h('p.muted', 'No readings available.'), raw(data));
}
function table(headers, rows) {
  return h('div.table-wrap', rows.length ? h('table.table',
    h('thead', h('tr', headers.map(title => h('th', title)))),
    h('tbody', rows.map(row => h('tr', row.map((value, i) => h('td', { dataset: { label: headers[i] } }, value ?? 'Unavailable')))))) : h('p.muted', 'No entries returned.'));
}
export function leaseList(data) {
  const leases = Array.isArray(data) ? data : data.leases || [];
  return h('div', table(['Host', 'IP address', 'MAC address', 'Expiry'], leases.map(row => [row.hostname || 'Unnamed', row.ip, row.mac,
    Number.isFinite(row.expires) ? row.expires === 0 ? 'Permanent' : new Date(row.expires * 1000).toLocaleString() : 'Unavailable'])), raw(data));
}
export function interfaceList(data) {
  const interfaces = data?.interface || [];
  return h('div', table(['Interface', 'State', 'Device', 'Addresses'], interfaces.map(row => [row.interface,
    row.up === true ? 'Up' : row.up === false ? 'Down' : 'Unknown', row.l3_device || row.device,
    [...(row['ipv4-address'] || []), ...(row['ipv6-address'] || [])].map(a => a.address + '/' + a.mask).join(', ') || 'Unavailable'])), raw(data));
}
export function heading(el, title, description) { el.appendChild(h('div.page-head', h('h1', title), h('p', description))); }
export function pageScope() {
  let active = true;
  const versions = new WeakMap(), cleanups = [];
  async function section(el, loader, render) {
    const version = (versions.get(el) || 0) + 1; versions.set(el, version);
    if (!el.hasChildNodes()) el.appendChild(spinner());
    try {
      const data = await loader();
      if (active && versions.get(el) === version) clear(el).append(...[].concat(render(data)).filter(Boolean));
    } catch (e) {
      if (active && versions.get(el) === version) clear(el).appendChild(errorBox(e, () => section(el, loader, render)));
    }
  }
  return { section, add: fn => cleanups.push(fn), destroy() { active = false; cleanups.forEach(fn => fn()); } };
}
export function modemSupports(data, capability) { return data?.available === true && data?.capabilities?.[capability] === true; }
