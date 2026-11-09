// Services › CBRS (expert, read-only): CBSD settings for the 3.5 GHz shared band (band 48) and registration state.
import { h } from '../../ui/dom.js';
import { kv, badge, alert } from '../../ui/widgets.js';
import { get } from '../../api.js';
import { infoCard } from '../device/_helpers.js';

const set = (v) => badge(v ? 'Stored' : 'Not set', v ? 'ok' : 'neutral');
const RESPONSE_CODES = [[0, 'Success'], [100, 'Protocol version not supported'], [101, 'Device blacklisted'], [102, 'Missing parameter'],
  [103, 'Invalid value'], [104, 'Certificate error'], [105, 'Deregistered'], [200, 'Registration pending'], [201, 'Group error'],
  [300, 'Spectrum not supported'], [400, 'Interference'], [401, 'Grant conflict'], [500, 'Grant terminated'], [501, 'Grant suspended'],
  [502, 'Operating parameters out of sync']];

export default {
  id: 'services-cbsd', path: '/services/cbsd', group: 'services', order: 50, title: 'CBRS (CBSD)', icon: 'antenna', expert: true,
  feature: 'services.cbsd',
  render(el) {
    el.appendChild(alert('info', 'CBRS is regulated shared spectrum: a device must be registered with a Spectrum Access System (SAS) '
      + 'by a certified installer before it may transmit on band 48. This page only shows the stored settings; registration and '
      + 'spectrum grants are deliberately not offered here.'));
    const grid = h('div.grid-2');
    el.appendChild(grid);
    const load = () => get('/api/services/cbsd');
    infoCard(grid, 'Spectrum Access System', 'globe', load, (d) => [kv([
      ['Operator', d.sas.operator], ['Server', d.sas.url ? h('span.mono', d.sas.url) : ''], ['Protocol version', d.sas.version],
      ['CBSD user ID', d.sas.user_id],
    ]), d.info_note && h('p.hint', d.info_note)]);
    infoCard(grid, 'Registration', 'check', load, (d) => [kv([
      ['CBSD service', badge(d.service_running ? 'Running' : 'Not running', d.service_running ? 'ok' : 'neutral')],
      ...(d.registration ? Object.entries(d.registration).map(([k, v]) => [k, v]) : [['State', 'Not available while the service is stopped']]),
      ...(d.info ? Object.entries(d.info).map(([k, v]) => [k, v]) : []),
    ])]);
    infoCard(grid, 'Certified installer (CPI)', 'users', load, (d) => [kv([
      ['Installer ID', d.installer.user_id], ['Name', d.installer.name], ['Signing key', set(d.installer.key_set)],
    ])]);
    infoCard(grid, 'Certificates and antenna', 'lock', load, (d) => [kv([
      ['Device certificate', set(d.certificates.certificate_set)], ['CA certificate', set(d.certificates.ca_certificate_set)],
      ['Private key', set(d.certificates.private_key_set)], ['Key passphrase', set(d.certificates.passphrase_set)],
      ['Antenna azimuth', d.antenna.azimuth === null ? '' : `${d.antenna.azimuth}°`],
      ['Antenna downtilt', d.antenna.downtilt === null ? '' : `${d.antenna.downtilt}°`],
    ])]);
    el.appendChild(h('details', { style: { marginTop: '12px' } }, h('summary', 'SAS response codes'),
      h('div.table-wrap', h('table.table', h('tbody', RESPONSE_CODES.map(([c, t]) => h('tr', h('td.mono', String(c)), h('td', t))))))));
  },
};
