import { h, clear, card, badge, button, alert, toast, form, get, put, post, del, block, raw, record, enc, security, confirmMessage, pageScope, heading, modemSupports } from './_shared.js';
import { wifiCreateBody, hardwareSummary } from '../system/_native.js';
export default { id: 'native-hardware', path: '/openwrt/hardware', group: 'tools', order: 5, title: 'Hardware and GNSS', feature: 'openwrt',
render(el) {
  heading(el, 'Hardware and GNSS', 'Provider observations include their freshness. Missing readings stay unavailable.');
  const scope = pageScope(), section = scope.section;
const hardwareBox = block(el), deviceBox = block(el);
    let hardwareActive = true, hardwareBusy = false, hardwareExpiry = null;
    const loadHardware = async () => {
      if (!hardwareActive || hardwareBusy) return;
      hardwareBusy = true;
      await section(hardwareBox, () => get('/api/openwrt/hardware'), d => {
        if (!hardwareActive) return [];
        if (hardwareExpiry) clearTimeout(hardwareExpiry);
        if (d.fresh === true && Number.isFinite(d.age_s) && d.age_s >= 0 && d.age_s <= 30) {
          // Use the backend age rather than assuming the browser and router clocks agree.
          hardwareExpiry = setTimeout(() => {
            if (!hardwareActive) return;
            clear(hardwareBox).appendChild(card('M3100 hardware report', alert('warn', 'Hardware observation expired; refreshing.')));
            loadHardware();
          }, Math.max(1, (30 - d.age_s) * 1000 + 1));
        }
        return card('M3100 hardware report', [
          ...Object.entries(hardwareSummary(d)).map(([label, value]) => h('p', h('b', label + ': '), value)),
          h('details', h('summary', 'Producer observation details'), raw(d)),
          button('Refresh hardware report', loadHardware),
        ]);
      });
      hardwareBusy = false;
    };
    loadHardware();
    const hardwareTimer = setInterval(loadHardware, 5000);
    scope.add(() => { hardwareActive = false; clearInterval(hardwareTimer); if (hardwareExpiry) clearTimeout(hardwareExpiry); });


section(deviceBox, () => get('/api/openwrt/gnss'), d => card('GNSS status', record(d)));
  return scope.destroy;
} };
