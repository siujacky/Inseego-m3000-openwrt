// Cellular > Signal & cell: live LTE + 5G NR detail, carrier aggregation, neighbour cells, counters and the
// raw field-test values. Charts use the shared status history (ctx.store) plus this page's own 5 s polling
// of /api/cellular/signal, which only runs while the page is open.
import { h, clear } from '../../ui/dom.js';
import { card, kv, stat, badge, signalBars, alert, button, toast } from '../../ui/widgets.js';
import { lineChart } from '../../ui/charts.js';
import * as fmt from '../../ui/format.js';
import { get } from '../../api.js';
import { q, num, mhz, kbps, yesNo, table, problem, every, has, dash } from './_radio.js';

const POLL_MS = 5000;
const MAX = 120; // own history samples (10 min at 5 s)

function legend(items) {
  return h('div.legend', items.map(([cls, label]) => h('span', h('i' + (cls ? '.' + cls : '')), label)));
}

function push(arr, v) {
  arr.push(v === undefined || v === null || v === '' ? null : Number(v));
  if (arr.length > MAX) arr.shift();
}

export default {
  id: 'cellular-signal', path: '/cellular/signal', group: 'cellular', order: 10, title: 'Signal & cell',
  icon: 'antenna', feature: 'cellular.radio',
  async render(el, ctx) {
    const top = h('div');
    const hero = h('div.grid-2');
    const charts = h('div.grid-2');
    const cells = h('div.grid-2');
    const caBox = h('div');
    const nbBox = h('div');
    const lower = h('div.grid-2');
    const rawBox = h('div');
    el.append(top, hero, charts, cells, caBox, nbBox, lower, rawBox);

    const own = { tx: [], nrtx: [], rx: [], txr: [] };
    let last = null;
    let paused = false;
    let lastOk = 0;

    const sigChart = h('div'), qualChart = h('div'), txChart = h('div'), rateChart = h('div');
    charts.append(
      card('Signal strength', [sigChart, legend([['', 'LTE RSRP (dBm)'], ['s2', '5G RSRP (dBm)']])],
        { icon: 'cellular', subtitle: 'last ~12 minutes' }),
      card('Signal quality', [qualChart, legend([['', 'LTE SINR (dB)'], ['s2', 'LTE RSRQ (dB)'], ['s3', '5G SNR (dB)']])],
        { icon: 'cellular', subtitle: 'last ~12 minutes' }),
      card('Transmit power', [txChart, legend([['', 'LTE (dBm)'], ['s2', '5G (dBm)']])],
        { icon: 'antenna', subtitle: 'while this page is open' }),
      card('Modem data rate', [rateChart, legend([['', 'Download (Mbit/s)'], ['s2', 'Upload (Mbit/s)']])],
        { icon: 'globe', subtitle: 'reported by the modem' }));

    function drawStoreCharts(hist) {
      if (!hist) return;
      clear(sigChart).appendChild(lineChart([{ values: hist.rsrp }, { values: hist.nr_rsrp, cls: 's2' }], { height: 160 }));
      clear(qualChart).appendChild(lineChart([{ values: hist.sinr }, { values: hist.rsrq, cls: 's2' }, { values: hist.nr_snr, cls: 's3' }],
        { height: 160 }));
    }
    function drawOwnCharts() {
      clear(txChart).appendChild(lineChart([{ values: own.tx }, { values: own.nrtx, cls: 's2' }], { height: 160 }));
      clear(rateChart).appendChild(lineChart([{ values: own.rx }, { values: own.txr, cls: 's2' }], { height: 160, min: 0 }));
    }
    drawOwnCharts();

    const status = h('span.hint');
    const pauseBtn = button('Pause', () => {
      paused = !paused;
      pauseBtn.querySelector('span:not(.icon)').textContent = paused ? 'Resume' : 'Pause';
      showStatus();
    }, { small: true, icon: 'clock' });
    const refreshBtn = button('Refresh', () => tick(true), { small: true, icon: 'refresh' });
    function showStatus() {
      status.textContent = paused ? 'Paused' : (lastOk ? `Live · updated ${new Date(lastOk).toLocaleTimeString()}` : 'Loading…');
    }

    function renderHero(d) {
      const s = d.summary || {}, lte = d.lte || {}, nr = d.nr || {};
      clear(top);
      if (s.state === 'no_sim') top.appendChild(alert('warn', 'No SIM card detected. Values below are from cells the modem can hear without registering (limited service).'));
      else if (s.state === 'no_service') top.appendChild(alert('warn', 'No mobile service right now.'));
      const stateBadge = s.state === 'in_service' ? badge('In service', 'ok') : s.state === 'no_sim' ? badge('No SIM', 'bad') : badge('No service', 'warn');
      clear(hero).append(
        card(null, h('div.hero',
          h('div', signalBars(s.bars)),
          h('div',
            h('div.hero-tech', s.tech || dash),
            h('div.hero-op', [s.operator || 'No operator', s.mccmnc ? ` · ${s.mccmnc.slice(0, 3)}-${s.mccmnc.slice(3)}` : ''].join('')),
            h('div.hero-state', stateBadge, s.roaming && badge('Roaming', 'warn'), d.ca && d.ca.active && badge('Carrier aggregation', 'accent'),
              nr.endc && badge('EN-DC', 'info'), nr.icon && nr.icon !== 'none' && badge(nr.icon, 'accent'),
              s.band_combo && badge(s.band_combo, 'neutral')))),
          { actions: [status, pauseBtn, refreshBtn] }),
        card('Overview', kv([
          ['Bands', s.band_combo || dash],
          ['Technology (modem)', s.service_tech || dash],
          ['Home network', s.home_network === null || s.home_network === undefined ? dash : (s.home_network ? 'Yes' : 'No (roaming partner)')],
          ['Registration', s.service || dash],
          ['Cell', lte.enb ? `eNB ${lte.enb} · sector ${lte.sector}` : (lte.cell_id || dash)],
          ['Network time', d.network_time && d.network_time.timestamp ? new Date(d.network_time.timestamp * 1000).toLocaleString() : dash],
        ]), { icon: 'about' }));
    }

    function renderCells(d) {
      const lte = d.lte || {}, nr = d.nr || {};
      const nrOn = nr.service_available;
      clear(cells).append(
        card('4G LTE', [
          h('div.stats', stat('RSRP', q('rsrp', lte.rsrp, 'dBm')), stat('RSRQ', q('rsrq', lte.rsrq, 'dB')),
            stat('SINR', q('sinr', lte.sinr, 'dB')), stat('RSSI', q('rssi', lte.rssi, 'dBm'))),
          h('div', { style: { height: '12px' } }),
          kv([
            ['Band', lte.band ? `B${lte.band}${lte.band_name ? ' (' + lte.band_name + ' MHz)' : ''}` : dash],
            ['Bandwidth', mhz(lte.bandwidth_mhz)],
            ['EARFCN', has(lte.earfcn) ? `${lte.earfcn}${lte.dl_mhz ? ' · ' + lte.dl_mhz + ' MHz' : ''}` : dash],
            ['PCI', num(lte.pci)],
            ['Serving cell', num(lte.serving_cell_id)],
            ['Cell ID', lte.cell_id || dash],
            ['eNB / sector', lte.enb ? `${lte.enb} / ${lte.sector}` : dash],
            ['TAC', lte.tac || dash],
            ['ECGI', lte.ecgi_hex ? h('span.mono', lte.ecgi_hex) : dash],
            ['TX power', num(lte.tx_power, 'dBm')],
            ['RX power (avg)', num(lte.avg_rx_power, 'dBm')],
            ['SNR / Ec/Io', `${num(lte.snr, 'dB')} / ${num(lte.ecio, 'dB')}`],
            ['Radio temperature', num(lte.radio_temp, '°C')],
          ], { compact: true }),
        ], { icon: 'cellular' }),
        card('5G NR', nrOn ? [
          h('div.stats', stat('RSRP', q('rsrp', nr.rsrp, 'dBm')), stat('RSRQ', q('rsrq', nr.rsrq, 'dB')),
            stat('SNR', q('snr', nr.snr, 'dB')), stat('Mode', nr.mode || dash)),
          h('div', { style: { height: '12px' } }),
          kv([
            ['Band', nr.band ? `n${nr.band}${nr.band_name ? ' (' + nr.band_name + ')' : ''}` : dash],
            ['Bandwidth', mhz(nr.bandwidth_mhz)],
            ['NR-ARFCN', has(nr.arfcn) ? `${nr.arfcn}${nr.dl_mhz ? ' · ' + nr.dl_mhz + ' MHz' : ''}` : dash],
            ['PCI', num(nr.pci)],
            ['Dual connectivity', `EN-DC ${yesNo(nr.endc)} · DCNR ${yesNo(nr.dcnr)}`],
            ['Bearer', nr.bearer || dash],
            ['Data connected', yesNo(nr.data_connected)],
            ['SSB index / SCS', `${num(nr.ssb_index)} / ${num(nr.scs_khz, 'kHz')}`],
            ['Component carriers', num(nr.num_cc)],
            ['TX power', num(nr.tx_power, 'dBm')],
            ['Radio link failures', num(nr.rlf_count)],
            ['Status icon', nr.icon || dash],
          ], { compact: true }),
        ] : h('p.muted', 'No 5G connection right now. 5G values appear here as soon as the modem uses 5G.'), { icon: 'cellular' }));
    }

    function renderCa(d) {
      const ca = d.ca || {};
      const rows = ca.cells || [];
      clear(caBox).appendChild(card('Carrier aggregation', [
        h('p.card-desc', ca.active ? 'Several LTE carriers are combined for more speed.' : 'Only one LTE carrier is in use.'),
        table([
          { key: 'role', label: 'Carrier', format: (v, r) => v === 'primary' ? 'Primary (PCC)' : `Secondary ${r.index}` },
          { key: 'band', label: 'Band', format: v => v ? 'B' + v : dash },
          { key: 'earfcn', label: 'EARFCN', format: (v, r) => has(v) ? `${v}${r.dl_mhz ? ' · ' + r.dl_mhz + ' MHz' : ''}` : dash },
          { key: 'pci', label: 'PCI' },
          { key: 'bandwidth_mhz', label: 'Width', format: v => mhz(v) },
          { key: 'status', label: 'State' },
          { key: 'rsrp', label: 'RSRP', format: v => q('rsrp', v, 'dBm') },
          { key: 'rsrq', label: 'RSRQ', format: v => q('rsrq', v, 'dB') },
          { key: 'rssi', label: 'RSSI', format: v => num(v, 'dBm') },
          { key: 'snr', label: 'SNR', format: v => q('snr', v, 'dB') },
        ], rows, { empty: 'The modem reports no aggregated carriers.' }),
        kv([
          ['Total bandwidth', mhz(ca.aggregated_bandwidth_mhz)],
          ['Status', ca.status || dash],
          ['Secondary EARFCNs', ca.scells && ca.scells.length ? ca.scells.map(x => `${x.dl_earfcn}${x.band ? ' (B' + x.band + ')' : ''}`).join(', ') : dash],
        ], { compact: true }),
      ], { icon: 'chip' }));
    }

    function renderNeighbors(d) {
      const nb = d.neighbors || {};
      const cols = [
        { key: 'pci', label: 'PCI' },
        { key: 'rsrp', label: 'RSRP', format: v => q('rsrp', v, 'dBm') },
        { key: 'rsrq', label: 'RSRQ', format: v => q('rsrq', v, 'dB') },
        { key: 'rssi', label: 'RSSI', format: v => num(v, 'dBm') },
      ];
      const parts = [
        h('h3', { style: { fontSize: '13.5px', margin: '0 0 6px' } }, 'Same frequency'),
        table(cols, nb.intra || [], { empty: 'No neighbour cells on the serving frequency.' }),
        h('h3', { style: { fontSize: '13.5px', margin: '14px 0 6px' } }, 'Other LTE frequencies'),
        table([{ key: 'freq', label: 'Frequency #' }].concat(cols), nb.inter || [], { empty: 'No neighbour cells on other frequencies.' }),
      ];
      if (nb.wcdma && nb.wcdma.length) {
        parts.push(h('h3', { style: { fontSize: '13.5px', margin: '14px 0 6px' } }, '3G (WCDMA) neighbours'),
          table([{ key: 'freq', label: 'Frequency #' }, { key: 'psc', label: 'PSC' }, { key: 'rscp', label: 'RSCP', format: v => num(v, 'dBm') },
            { key: 'ecno', label: 'Ec/No', format: v => num(v, 'dB') }], nb.wcdma));
      }
      clear(nbBox).appendChild(card('Neighbour cells', parts, { icon: 'antenna', subtitle: 'updated every 15 s' }));
    }

    function renderLower(d) {
      const r = d.rates || {}, c = d.counters || {};
      const s4 = c.session_v4 || {}, s6 = c.session_v6 || {};
      const life = c.lifetime;
      clear(lower).append(
        card('Traffic counters', kv([
          ['Current rate (modem)', r && has(r.cur_rx_kbps) ? `↓ ${kbps(r.cur_rx_kbps)}  ↑ ${kbps(r.cur_tx_kbps)}` : dash],
          ['Peak rate (modem)', r && has(r.max_rx_kbps) ? `↓ ${kbps(r.max_rx_kbps)}  ↑ ${kbps(r.max_tx_kbps)}` : dash],
          ['This session IPv4', c.session_v4 ? `↓ ${fmt.bytes(s4.rx_bytes)}  ↑ ${fmt.bytes(s4.tx_bytes)}` : dash],
          ['This session IPv6', c.session_v6 ? `↓ ${fmt.bytes(s6.rx_bytes)}  ↑ ${fmt.bytes(s6.tx_bytes)}` : dash],
          ['Packets received (IPv4)', c.packets_v4 ? num(c.packets_v4.packets_received) : dash],
          ['Errors (IPv4)', c.packets_v4 ? `${c.packets_v4.errors_sent} sent / ${c.packets_v4.errors_received} received` : dash],
          ['Lifetime total', life ? `↓ ${fmt.bytes(life.rx_kb * 1024)}  ↑ ${fmt.bytes(life.tx_kb * 1024)}` : 'Not reported'],
        ]), { icon: 'globe' }),
        card('Registration', kv([
          ['Random access failures', num(d.rach_failures), 'LTE RACH failures since start-up'],
          ['Last reject cause', d.reject_cause ? (d.reject_cause.code ? `${d.reject_cause.code} · ${d.reject_cause.text}` : 'None') : dash],
          ['Roaming state', (d.summary && d.summary.roaming_state) || dash],
          ['Network time', d.network_time ? (d.network_time.time || dash) : dash,
            d.network_time && has(d.network_time.timezone) ? `zone ${d.network_time.timezone}, DST ${d.network_time.dst}` : ''],
        ]), { icon: 'about' }));
    }

    // raw field-test values (collapsed by default), plus the modem diagnostics buffer on demand
    const rawBody = h('div');
    const diagBox = h('div');
    const details = h('details', { ontoggle: () => { if (details.open && last) renderRaw(last); } },
      h('summary', { style: { cursor: 'pointer', fontWeight: 600 } }, 'Show all raw values (field test)'), rawBody);
    let rawText = '';
    const copyBtn = button('Copy', async () => {
      try { await navigator.clipboard.writeText(rawText); toast('Copied'); } catch (_) { toast('Copying is not allowed by this browser', 'bad'); }
    }, { small: true });
    const diagBtn = button('Modem diagnostics', async () => {
      const d = await get('/api/cellular/signal/diag');
      clear(diagBox).appendChild(d.ok ? h('pre.pre', d.text) : alert('info', `Diagnostics not available: ${d.message}`));
    }, { small: true, icon: 'terminal' });
    rawBox.appendChild(card('Field test', [h('p.card-desc', 'Every value the modem reports, exactly as read. Useful for antenna alignment and troubleshooting.'),
      details, diagBox], { icon: 'tools', actions: [copyBtn, diagBtn] }));

    function renderRaw(d) {
      const rows = [];
      const lines = [];
      for (const g of d.raw || []) {
        if (!g.ok) { rows.push({ command: g.command, field: '(not available)', value: g.message || '' }); lines.push(`${g.command}: not available (${g.message || ''})`); continue; }
        for (const [k, v] of g.values) { rows.push({ command: g.command, field: k, value: v }); lines.push(`${g.command}  ${k} = ${v}`); }
      }
      rawText = lines.join('\n');
      if (!details.open) return;
      clear(rawBody).appendChild(table([{ key: 'command', label: 'Command', mono: true }, { key: 'field', label: 'Field' },
        { key: 'value', label: 'Value', mono: true }], rows));
    }

    function renderAll(d) {
      last = d;
      renderHero(d);
      renderCells(d);
      renderCa(d);
      renderNeighbors(d);
      renderLower(d);
      renderRaw(d);
    }

    let busy = false;
    async function tick(force) {
      if ((paused && !force) || busy) return;
      busy = true;
      try {
        const d = await get('/api/cellular/signal');
        lastOk = Date.now();
        push(own.tx, d.lte && d.lte.tx_power); push(own.nrtx, d.nr && d.nr.service_available ? d.nr.tx_power : null);
        push(own.rx, d.rates && has(d.rates.cur_rx_kbps) ? d.rates.cur_rx_kbps / 1000 : null);
        push(own.txr, d.rates && has(d.rates.cur_tx_kbps) ? d.rates.cur_tx_kbps / 1000 : null);
        renderAll(d);
        drawOwnCharts();
      } catch (e) {
        if (!last) { clear(top).appendChild(problem(e, () => tick(true))); }
        else status.textContent = `Update failed: ${e.message}`;
        busy = false;
        return;
      }
      busy = false;
      showStatus();
    }

    await tick(true);
    const stop = every(POLL_MS, () => tick(false));
    const unsub = ctx.store.subscribe((s) => drawStoreCharts(s.history));
    return () => { stop(); unsub(); };
  },
};
