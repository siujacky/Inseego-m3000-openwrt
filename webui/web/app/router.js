// Hash router + page registry. Pages are ES modules under /app/pages/** (listed by GET /api/ui/pages).
//
// Page module contract (export default):
//   { id, path: '/area/page', group: 'cellular', order: 10, title: 'Bands', icon?, feature?, expert?,
//     async render(el, ctx), destroy?() }
// ctx = { params, query, navigate, api, store, meta, setTitle }
import { get } from './api.js';

export const pages = [];
let current = null;

export async function loadPages() {
  const list = await get('/api/ui/pages');
  const mods = await Promise.all(list.map(p => import(p).then(m => m.default).catch(e => { console.error('page failed to load', p, e); return null; })));
  for (const p of mods) if (p && p.path && p.render) pages.push(p);
  pages.sort((a, b) => (a.order || 0) - (b.order || 0));
  return pages;
}

function compile(path) {
  const keys = [];
  const rx = new RegExp('^' + path.replace(/\/:([a-z_]+)/gi, (_, k) => { keys.push(k); return '/([^/]+)'; }) + '/?$');
  return { rx, keys };
}

export function match(hash) {
  const [path, qs] = (hash.replace(/^#/, '') || '/').split('?');
  for (const p of pages) {
    p._c = p._c || compile(p.path);
    const m = p._c.rx.exec(path);
    if (m) {
      const params = {};
      p._c.keys.forEach((k, i) => { params[k] = decodeURIComponent(m[i + 1]); });
      return { page: p, params, query: Object.fromEntries(new URLSearchParams(qs || '')) };
    }
  }
  return null;
}

export function navigate(path) {
  if (location.hash !== '#' + path) location.hash = '#' + path;
}

// render() may return a cleanup function (clear timers, unsubscribe); it runs when the user navigates away.
let cleanup = null;
let seq = 0;

export function disposePage() {
  ++seq;
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
}

export async function show(el, ctxBase, onShown) {
  const my = ++seq;
  const hit = match(location.hash) || match('#/');
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
  current = hit ? hit.page : null;
  el.textContent = '';
  if (!hit) { el.textContent = 'Page not found'; return; }
  const ctx = Object.assign({}, ctxBase, { params: hit.params, query: hit.query, navigate });
  onShown && onShown(hit.page);
  try {
    const c = await hit.page.render(el, ctx);
    if (typeof c === 'function') { if (my === seq) cleanup = c; else c(); }
  } catch (e) { console.error(e); el.textContent = 'This page failed to load: ' + (e.message || e); }
}
