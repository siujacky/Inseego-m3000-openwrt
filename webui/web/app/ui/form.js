// Schema-driven settings form.
//
// form(container, {
//   title, description, icon,
//   load: async () => values,                      // object keyed by field key
//   save: async (changes, values) => freshValues,   // PUT partial; return read-back (or undefined to reload)
//   fields: [ {key, label, type, ...} ],
//   warn: (changes, values) => string|null,         // optional "you may get disconnected" confirmation
//   saveLabel: 'Save', readonly: false, actions: [extra buttons]
// })
// Field types: text, password, number, select, switch, checkbox, radio, textarea, chips, readonly,
//   section, custom.  Common props: label, help, unit, placeholder, required, minLength, maxLength,
//   min, max, step, pattern (RegExp|string), patternMsg, validate(v, values) -> msg|null,
//   options ([value,label] pairs | async fn), show(values)->bool, disabled(values)->bool, full: true,
//   render(values, set) for type 'custom', parse/format for value conversion.
import { h, clear, nextId, append } from './dom.js';
import { icon } from './icons.js';
import { card, errorBox, spinner, toast, confirmSimple } from './widgets.js';

function eq(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

export function form(container, cfg) {
  const st = { initial: {}, values: {}, errors: {}, options: {}, loading: true, saving: false, loadError: null };
  const body = h('div.form-body');
  const foot = h('div.form-foot');
  const root = card(cfg.title, [cfg.description && h('p.card-desc', cfg.description), body, foot],
    { icon: cfg.icon, actions: cfg.headerActions, cls: 'form-card' });
  clear(container).appendChild(root);

  const fields = cfg.fields.filter(Boolean);

  function visible(f) { return !f.show || f.show(st.values); }
  function isDisabled(f) { return cfg.readonly || f.readonly || (f.disabled && f.disabled(st.values)); }

  function validateField(f) {
    if (!visible(f) || ['section', 'readonly', 'custom'].includes(f.type) && !f.validate) return null;
    const v = st.values[f.key];
    const empty = v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length);
    if (f.required && empty) return 'Required';
    if (empty) return null;
    if (f.type === 'number') {
      const n = Number(v);
      if (!Number.isFinite(n)) return 'Enter a number';
      if (f.min !== undefined && n < f.min) return `Minimum ${f.min}`;
      if (f.max !== undefined && n > f.max) return `Maximum ${f.max}`;
      if (f.integer !== false && !Number.isInteger(n) && !f.step) return 'Enter a whole number';
    }
    if (typeof v === 'string') {
      if (f.minLength && v.length < f.minLength) return `At least ${f.minLength} characters`;
      if (f.maxLength && v.length > f.maxLength) return `At most ${f.maxLength} characters`;
      if (f.pattern) {
        const rx = f.pattern instanceof RegExp ? f.pattern : new RegExp(f.pattern);
        if (!rx.test(v)) return f.patternMsg || 'Invalid format';
      }
    }
    return f.validate ? f.validate(v, st.values) : null;
  }

  function changes() {
    const out = {};
    for (const f of fields) {
      if (!f.key || ['section', 'readonly'].includes(f.type) || f.noSave) continue;
      if (!visible(f) && !f.saveHidden) continue;
      if (!eq(st.values[f.key], st.initial[f.key])) out[f.key] = st.values[f.key];
    }
    return out;
  }

  function set(key, value) {
    st.values[key] = value;
    delete st.errors[key];
    render();
  }

  function control(f, id) {
    id = id || nextId();
    const v = st.values[f.key];
    const dis = isDisabled(f);
    const onInput = (val) => { st.values[f.key] = f.parse ? f.parse(val) : val; delete st.errors[f.key]; renderFoot(); refreshDeps(f); };
    switch (f.type) {
      case 'switch':
        return h('label.switch', { for: id },
          h('input', { id, type: 'checkbox', checked: !!v, disabled: dis, onchange: (e) => { onInput(e.target.checked); render(); } }),
          h('span.switch-track'), h('span.switch-label', v ? (f.onLabel || 'On') : (f.offLabel || 'Off')));
      case 'checkbox':
        return h('label.check', h('input', { id, type: 'checkbox', checked: !!v, disabled: dis, onchange: (e) => { onInput(e.target.checked); render(); } }), h('span', f.checkLabel || ''));
      case 'select': {
        const opts = st.options[f.key] || f.options || [];
        return h('select', { id, disabled: dis, onchange: (e) => { const o = opts[e.target.selectedIndex]; onInput(Array.isArray(o) ? o[0] : o); render(); } },
          opts.map(o => { const [val, lab] = Array.isArray(o) ? o : [o, o]; return h('option', { selected: eq(val, v) }, lab); }));
      }
      case 'radio': {
        const opts = st.options[f.key] || f.options || [];
        return h('div.radios', { role: 'radiogroup' }, opts.map(o => {
          const [val, lab, hint] = Array.isArray(o) ? o : [o, o];
          return h('label.radio', h('input', { type: 'radio', name: id, checked: eq(val, v), disabled: dis, onchange: () => { onInput(val); render(); } }),
            h('span', lab, hint && h('small.hint', hint)));
        }));
      }
      case 'chips': {
        const opts = st.options[f.key] || f.options || [];
        const sel = new Set(Array.isArray(v) ? v : []);
        return h('div.chips', opts.map(o => {
          const [val, lab, extra] = Array.isArray(o) ? o : [o, o];
          return h('label.chip' + (sel.has(val) ? '.on' : '') + (extra && extra.cls ? '.' + extra.cls : ''), { title: extra && extra.title },
            h('input', { type: 'checkbox', checked: sel.has(val), disabled: dis || (extra && extra.disabled), onchange: (e) => {
              const next = new Set(Array.isArray(st.values[f.key]) ? st.values[f.key] : []);
              e.target.checked ? next.add(val) : next.delete(val);
              const order = opts.map(x => Array.isArray(x) ? x[0] : x);
              onInput(order.filter(x => next.has(x))); render();
            } }), h('span', lab));
        }));
      }
      case 'textarea':
        return h('textarea', { id, rows: f.rows || 4, disabled: dis, placeholder: f.placeholder || '', maxlength: f.maxLength,
          spellcheck: 'false', oninput: (e) => onInput(e.target.value) }, v == null ? '' : String(v));
      case 'readonly':
        return h('div.readonly', f.format ? f.format(v, st.values) : (v === undefined || v === null || v === '' ? '—' : String(v)));
      case 'custom':
        return f.render(st.values, set, { disabled: dis });
      case 'password': {
        const inp = h('input', { id, type: 'password', value: v == null ? '' : v, disabled: dis, autocomplete: f.autocomplete || 'new-password',
          placeholder: f.placeholder || '', maxlength: f.maxLength, spellcheck: 'false', oninput: (e) => onInput(e.target.value) });
        const eye = h('button.icon-btn.reveal', { type: 'button', 'aria-label': 'Show password', onclick: () => {
          inp.type = inp.type === 'password' ? 'text' : 'password'; clear(eye).appendChild(icon(inp.type === 'password' ? 'eye' : 'eyeoff'));
        } }, icon('eye'));
        return h('div.input-group', inp, eye, f.generate && h('button.btn.btn-sm', { type: 'button', onclick: () => { inp.value = f.generate(); onInput(inp.value); inp.type = 'text'; } }, 'Generate'));
      }
      default: {
        const inp = h('input', { id, type: f.type === 'number' ? 'number' : 'text', value: v == null ? '' : (f.format ? f.format(v) : v),
          disabled: dis, placeholder: f.placeholder || '', min: f.min, max: f.max, step: f.step, maxlength: f.maxLength,
          inputmode: f.inputmode || (f.type === 'number' ? 'numeric' : undefined), autocomplete: 'off', spellcheck: 'false',
          oninput: (e) => onInput(f.type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value) });
        return f.unit ? h('div.input-group', inp, h('span.unit', f.unit)) : inp;
      }
    }
  }

  function refreshDeps(f) {
    if (fields.some(x => x.show || x.disabled)) {
      clearTimeout(refreshDeps.t);
      refreshDeps.t = setTimeout(() => {
        const a = document.activeElement, id = a && a.id, pos = a && a.selectionStart;
        render();
        if (id) { const el = document.getElementById(id); if (el) { el.focus(); try { el.setSelectionRange(pos, pos); } catch (_) { /* non-text */ } } }
      }, 250);
    }
  }

  function row(f) {
    if (!visible(f)) return null;
    if (f.type === 'section') return h('div.form-section', h('h3', f.label), f.help && h('p.hint', f.help));
    const err = st.errors[f.key];
    const cid = f._cid || (f._cid = nextId('fld'));
    return h('div.field' + (f.full || ['textarea', 'chips', 'custom', 'radio'].includes(f.type) ? '.field-full' : '') + (err ? '.has-error' : ''),
      { dataset: { key: f.key } },
      f.label && h('label.field-label', { for: cid }, f.label, f.required && h('span.req', ' *')),
      h('div.field-control', control(f, cid)),
      err ? h('div.field-error', err) : (f.help && h('div.field-help', typeof f.help === 'function' ? f.help(st.values) : f.help)));
  }

  function renderFoot() {
    clear(foot);
    if (cfg.readonly) { append(foot, cfg.actions || []); return; }
    const ch = changes();
    const dirty = cfg.alwaysDirty || Object.keys(ch).length > 0;
    const save = h('button.btn.btn-primary' + (st.saving ? '.busy' : ''), { type: 'button', disabled: !dirty || st.saving || st.loading, onclick: doSave },
      h('span', st.saving ? 'Saving…' : (cfg.saveLabel || 'Save')));
    const reset = h('button.btn', { type: 'button', disabled: !dirty || st.saving, onclick: () => { st.values = JSON.parse(JSON.stringify(st.initial)); st.errors = {}; render(); } }, 'Reset');
    append(foot, [h('div.form-actions', save, reset, ...(cfg.actions || [])), dirty ? h('span.dirty', 'Unsaved changes') : null]);
  }

  function render() {
    clear(body);
    if (st.loading) { body.appendChild(spinner()); renderFoot(); return; }
    if (st.loadError) { body.appendChild(errorBox(st.loadError, reload)); clear(foot); return; }
    body.appendChild(h('div.form-grid', fields.map(row)));
    renderFoot();
  }

  async function doSave() {
    const errs = {};
    for (const f of fields) { const e = validateField(f); if (e) errs[f.key] = e; }
    st.errors = errs;
    if (Object.keys(errs).length) { render(); const el = body.querySelector('.has-error input,.has-error select,.has-error textarea'); el && el.focus(); return; }
    const ch = changes();
    if (cfg.warn) {
      const w = cfg.warn(ch, st.values);
      if (w && !(await confirmSimple('Please confirm', w, 'Save anyway', 'primary'))) return;
    }
    st.saving = true; renderFoot();
    try {
      const fresh = await cfg.save(ch, st.values);
      if (fresh && typeof fresh === 'object' && !Array.isArray(fresh)) {
        st.initial = JSON.parse(JSON.stringify(fresh)); st.values = JSON.parse(JSON.stringify(fresh));
      } else {
        await reload(true);
      }
      toast(cfg.savedMessage || 'Saved');
    } catch (e) {
      if (e.code === 'cancelled') { /* user backed out */ }
      else if (e.field && fields.some(f => f.key === e.field)) { st.errors[e.field] = e.message; }
      else toast(e.message || String(e), 'bad', 6000);
    } finally {
      st.saving = false; render();
    }
  }

  async function reload(quiet) {
    if (!quiet) { st.loading = true; render(); }
    try {
      for (const f of fields) if (typeof f.options === 'function') st.options[f.key] = await f.options();
      const vals = (await cfg.load()) || {};
      st.initial = JSON.parse(JSON.stringify(vals)); st.values = JSON.parse(JSON.stringify(vals));
      st.loadError = null;
    } catch (e) { st.loadError = e; }
    st.loading = false; render();
  }

  render();
  reload();
  return { reload, values: () => st.values, set, el: root };
}
