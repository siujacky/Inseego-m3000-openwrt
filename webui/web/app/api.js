// API client. JSON in/out, CSRF header, session expiry handling, and the generic
// tier-confirmation flow: when a write answers HTTP 428 {code: 'confirm_required', details: {tier}},
// the confirm dialog is shown and the request is retried with {confirm: true[, password]}.
import { confirmTier } from './ui/widgets.js';

let csrf = null;
const listeners = new Set();

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message || code || `HTTP ${status}`);
    this.status = status; this.code = code; this.details = details;
  }
  get field() { return this.details && this.details.field; }
}

export function setCsrf(token) { csrf = token; }
export function onAuthRequired(fn) { listeners.add(fn); return () => listeners.delete(fn); }

async function raw(method, path, body, opts = {}) {
  const headers = { Accept: 'application/json' };
  let payload;
  if (body !== undefined && !(body instanceof FormData) && !(body instanceof Blob)) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  } else if (body !== undefined) {
    payload = body;
  }
  if (method !== 'GET' && csrf) headers['X-CSRF-Token'] = csrf;
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload, credentials: 'same-origin', cache: 'no-store',
                              signal: opts.signal });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, 'network', 'The router did not answer. Check the connection.');
  }
  if (res.status === 204) return null;
  const ctype = res.headers.get('Content-Type') || '';
  if (opts.blob && res.ok) return res.blob();
  const data = ctype.includes('application/json') ? await res.json().catch(() => null) : await res.text();
  if (res.ok) return data;
  const err = data && data.error ? data.error : {};
  const e = new ApiError(res.status, err.code || 'http_' + res.status, err.message || `Request failed (${res.status})`, err.details);
  if (res.status === 401 && !opts.noAuthRedirect) listeners.forEach(fn => fn(e));
  throw e;
}

export async function api(method, path, body, opts = {}) {
  try {
    return await raw(method, path, body, opts);
  } catch (e) {
    if (e instanceof ApiError && e.status === 428 && e.code === 'confirm_required' && !opts.noConfirm) {
      const tier = (e.details && e.details.tier) || 2;
      const answer = await confirmTier({ tier, title: opts.confirmTitle, message: opts.confirmMessage || e.message,
                                         phrase: opts.confirmPhrase });
      if (!answer) throw new ApiError(0, 'cancelled', 'Cancelled');
      const merged = Object.assign({}, body || {}, { confirm: true }, answer.password ? { password: answer.password } : {});
      return api(method, path, merged, Object.assign({}, opts, { noConfirm: true }));
    }
    throw e;
  }
}

export const get = (p, o) => api('GET', p, undefined, o);
export const put = (p, b, o) => api('PUT', p, b, o);
export const post = (p, b, o) => api('POST', p, b, o);
export const del = (p, b, o) => api('DELETE', p, b, o);
