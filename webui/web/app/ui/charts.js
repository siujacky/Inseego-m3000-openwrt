// Small SVG charts without dependencies.
const NS = 'http://www.w3.org/2000/svg';
const el = (t, a) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); return e; };

// sparkline(values, {width, height, min, max, cls})
export function sparkline(values, o = {}) {
  const w = o.width || 120, ht = o.height || 32;
  const svg = el('svg', { viewBox: `0 0 ${w} ${ht}`, class: 'spark ' + (o.cls || ''), preserveAspectRatio: 'none', 'aria-hidden': 'true' });
  const v = values.filter(x => x !== null && x !== undefined && Number.isFinite(Number(x))).map(Number);
  if (v.length < 2) return svg;
  const min = o.min !== undefined ? o.min : Math.min(...v), max = o.max !== undefined ? o.max : Math.max(...v);
  const span = max - min || 1;
  const pts = v.map((y, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(ht - 2 - (y - min) / span * (ht - 4)).toFixed(1)}`);
  svg.appendChild(el('polyline', { points: pts.join(' '), fill: 'none', 'stroke-width': 1.6, 'vector-effect': 'non-scaling-stroke' }));
  return svg;
}

// lineChart([{name, values:[...], cls}], {height, min, max, unit, labels: count})
export function lineChart(series, o = {}) {
  const w = 600, ht = o.height || 180, padL = 36, padB = 18, padT = 8;
  const svg = el('svg', { viewBox: `0 0 ${w} ${ht}`, class: 'chart', role: 'img', 'aria-label': o.label || 'chart' });
  const all = series.flatMap(s => s.values).filter(x => x !== null && x !== undefined && Number.isFinite(Number(x))).map(Number);
  if (!all.length) {
    const t = el('text', { x: w / 2, y: ht / 2, 'text-anchor': 'middle', class: 'chart-empty' }); t.textContent = 'Collecting data…'; svg.appendChild(t);
    return svg;
  }
  let min = o.min !== undefined ? o.min : Math.floor(Math.min(...all));
  let max = o.max !== undefined ? o.max : Math.ceil(Math.max(...all));
  if (min === max) { min -= 1; max += 1; }
  const n = Math.max(...series.map(s => s.values.length));
  const X = (i) => padL + (n <= 1 ? 0 : i / (n - 1) * (w - padL - 4));
  const Y = (y) => padT + (1 - (y - min) / (max - min)) * (ht - padT - padB);
  for (let k = 0; k <= 4; k++) {
    const yv = min + (max - min) * k / 4, y = Y(yv);
    svg.appendChild(el('line', { x1: padL, x2: w - 4, y1: y, y2: y, class: 'grid' }));
    const t = el('text', { x: padL - 6, y: y + 4, 'text-anchor': 'end', class: 'axis' }); t.textContent = Math.round(yv); svg.appendChild(t);
  }
  for (const s of series) {
    let d = '';
    s.values.forEach((y, i) => { if (y === null || y === undefined || !Number.isFinite(Number(y))) return; d += (d ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(Number(y)).toFixed(1); });
    if (d) svg.appendChild(el('path', { d, class: 'series ' + (s.cls || ''), fill: 'none' }));
  }
  if (o.unit) { const t = el('text', { x: 4, y: padT + 4, class: 'axis' }); t.textContent = o.unit; svg.appendChild(t); }
  return svg;
}
