// Shared helpers for the Device and Services pages (not a page: file name starts with "_").
import { h, clear, append } from '../../ui/dom.js';
import { form } from '../../ui/form.js';
import { card, alert, errorBox, spinner, toast } from '../../ui/widgets.js';

export function isUnavailable(e) {
  return !!e && (e.status === 503 || ['unavailable', 'unsupported', 'disabled'].includes(e.code));
}

// form() that loads once first: "not available on this unit" (HTTP 503) becomes a friendly note instead
// of an error box, and `fields` / `description` may be functions of the loaded data (e.g. to offer the
// current value in a select).  Returns a promise of the form handle (or null).
export function smartForm(host, cfg) {
  const slot = h('div');
  host.appendChild(slot);
  const run = async () => {
    clear(slot).appendChild(card(cfg.title, spinner(), { icon: cfg.icon }));
    let data;
    try { data = await cfg.load(); } catch (e) {
      clear(slot).appendChild(card(cfg.title, isUnavailable(e) ? alert('info', e.message) : errorBox(e, run), { icon: cfg.icon }));
      return null;
    }
    let first = data;
    const fields = typeof cfg.fields === 'function' ? cfg.fields(data) : cfg.fields;
    const description = typeof cfg.description === 'function' ? cfg.description(data) : cfg.description;
    const f = form(slot, Object.assign({}, cfg, {
      fields, description,
      load: async () => { if (first) { const d = first; first = null; return d; } return cfg.load(); },
    }));
    if (cfg.onReady) cfg.onReady(f, data);
    return f;
  };
  return run();
}

// Read-only card filled by loader(); 503 -> note.  Returns {reload}.
export function infoCard(host, title, icon, loader, render, opts = {}) {
  const body = h('div', spinner());
  host.appendChild(card(title, body, { icon, actions: opts.actions }));
  const reload = async (quiet) => {
    if (!quiet) clear(body).appendChild(spinner());
    try {
      const d = await loader();
      clear(body);
      const out = render(d, reload);
      if (out) append(body, [].concat(out));   // skips null/false (native append would print them)
      return d;
    } catch (e) {
      clear(body).appendChild(isUnavailable(e) ? alert('info', e.message) : errorBox(e, () => reload()));
      return undefined;
    }
  };
  reload();
  return { reload, body };
}

export function seconds(s) {
  s = Number(s);
  if (!Number.isFinite(s)) return '—';
  if (s === 0) return 'Never';
  if (s < 60) return `${s} s`;
  if (s < 3600) return s % 60 ? `${(s / 60).toFixed(1)} min` : `${s / 60} min`;
  const hrs = s / 3600;
  return `${Number.isInteger(hrs) ? hrs : hrs.toFixed(1)} h`;
}

// [value, label] options from a list of seconds, adding the current value when it is not in the list.
export function secondOptions(values, current, never) {
  const vals = values.slice();
  if (current !== null && current !== undefined && !vals.includes(current)) vals.push(current);
  vals.sort((a, b) => (a === 0 ? Infinity : a) - (b === 0 ? Infinity : b));
  return vals.map(v => [v, v === 0 ? (never || 'Never') : seconds(v) + (values.includes(v) ? '' : ' (current)')]);
}

export function yesNo(v, yes = 'Yes', no = 'No') {
  return v === null || v === undefined ? '—' : (v ? yes : no);
}

export function notify(e) {
  if (e && e.code !== 'cancelled') toast(e.message || String(e), 'bad', 6000);
}

export function serviceNote(running, what) {
  return running === false ? alert('info', `The ${what} service is not running on the router right now. Settings are stored and `
    + 'take effect when it runs.') : null;
}

// External link the user may open (nothing is loaded automatically).
export function extLink(href, text) {
  return h('a', { href, target: '_blank', rel: 'noopener noreferrer' }, text);
}
