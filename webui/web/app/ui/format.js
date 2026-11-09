// Formatting helpers.
export function metricNumber(value) {
  if (!['number', 'string'].includes(typeof value) || typeof value === 'string' && !value.trim()) return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

export function bytes(n, digits = 1) {
  n = metricNumber(n);
  if (n === null) return 'Unavailable';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i ? n.toFixed(digits) : n} ${u[i]}`;
}

export function rate(bps) {
  const n = metricNumber(bps);
  if (n === null) return 'Unavailable';
  if (n >= 1e9) return (n / 1e9).toFixed(2) + ' Gbit/s';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + ' Mbit/s';
  if (n >= 1e3) return (n / 1e3).toFixed(0) + ' kbit/s';
  return n.toFixed(0) + ' bit/s';
}

export function duration(s) {
  s = metricNumber(s);
  if (s === null) return 'Unavailable';
  s = Math.floor(s);
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
  if (d) return `${d}d ${h}h ${m}m`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}

export function dbm(v, unit = 'dBm') {
  return v === null || v === undefined || v === '' || Number(v) === 0 ? '—' : `${v} ${unit}`;
}

export function ago(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleString();
}

export function dateTime(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : '—';
}

// Signal quality -> 'good' | 'fair' | 'poor' | 'none'  (thresholds per common LTE/NR practice)
export function quality(kind, v) {
  if (v === null || v === undefined || v === '' || Number(v) === 0) return 'none';
  v = Number(v);
  const t = {
    rsrp: [-90, -105], rsrq: [-10, -15], sinr: [13, 3], snr: [13, 3], rssi: [-70, -90],
  }[kind];
  if (!t) return 'none';
  return v >= t[0] ? 'good' : v >= t[1] ? 'fair' : 'poor';
}

export function mask(s, keep = 4) {
  if (!s) return '';
  s = String(s);
  return s.length <= keep ? s : '•'.repeat(Math.max(0, s.length - keep)) + s.slice(-keep);
}

export function yesno(b) { return b ? 'Yes' : 'No'; }
export function onoff(b) { return b ? 'On' : 'Off'; }
