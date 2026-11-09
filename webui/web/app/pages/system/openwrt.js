import { h, card, badge, button, alert, toast, form, get, put, post, del, block, raw, record, enc, security, confirmMessage, pageScope, heading, modemSupports } from '../openwrt/_shared.js';
import { wifiCreateBody, hardwareSummary } from './_native.js';
export default { id:'system-openwrt',path:'/system/openwrt',group:'tools',order:0,title:'Capabilities',feature:'openwrt', render(el) {
heading(el,'Router capabilities','Available controls use this router’s native services. Unsupported features include a reason.');
const scope=pageScope(),section=scope.section,capsBox=block(el);
    const loadCaps = () => section(capsBox, () => get('/api/openwrt'), d => card('Capabilities', [
      h('div.chips', Object.entries(d.capabilities).map(([name, value]) => badge(name + ': ' + (value.available ? 'detected' : 'unavailable'), value.available ? 'ok' : 'warn'))),
      ...Object.entries(d.capabilities).map(([name, value]) => h('details', h('summary', name + (value.reason ? ': ' + value.reason : '')), raw(value))),
      button('Refresh capabilities', loadCaps),
    ]));
    loadCaps();


el.appendChild(card('Settings',h('div.nav-grid',[["/openwrt/cellular", "Cellular", "Provider status and connection controls"], ["/openwrt/sms", "Messages", "SMS reading and sending"], ["/openwrt/wireless", "Wireless", "Radios, access points and clients"], ["/openwrt/network", "Interfaces", "Addresses, DNS and upstreams"], ["/openwrt/dhcp", "DHCP and leases", "Local address allocation"], ["/firewall/openwrt", "Firewall", "Zones, forwarding and traffic rules"], ["/openwrt/system", "System", "Name, time zone and telemetry"], ["/openwrt/services", "Services", "Installed service controls"], ["/openwrt/logs", "Logs", "Bounded system log"], ["/openwrt/hardware", "Hardware", "Freshness, battery observations and GNSS"]].map(([path,title,desc])=>h('a.nav-card',{href:'#'+path},h('strong',title),h('span',desc))))));
el.appendChild(alert('info','Firmware updates and recovery actions are not available from this RAM interface.'));
return scope.destroy; } };
