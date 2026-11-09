// Run this function through Chrome DevTools evaluate_script on the native preview.
// Synthetic preview credentials only. No real router may be the target.
async () => {
  const results = [];
  const newApn = 'preview' + Date.now() + '.apn';
  const check = (name, passed, details) => results.push({ name, passed: !!passed, details });
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const route = async path => { location.hash = '#' + path; await wait(250); };
  const button = (root, text) => [...root.querySelectorAll('button')].find(b => b.textContent.trim() === text);
  const field = (root, text) => { const l = [...root.querySelectorAll('label')].find(n => n.textContent.replace(/\s*\*$/, '').trim() === text); return l && document.getElementById(l.htmlFor); };
  const fill = (input, value) => { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); };
  if (!document.querySelector('.demo-banner')?.textContent.includes('Host preview')) throw Error('Only the synthetic host preview may run these tests');
  if (!window.__previewFetch) {
    window.__previewFetch = window.fetch.bind(window); window.__previewRequests = [];
    window.fetch = (url, options = {}) => {
      let body = {}; try { body = JSON.parse(options.body || '{}'); } catch (_) { /* GET */ }
      window.__previewRequests.push({ path: String(url), method: options.method || 'GET', keys: Object.keys(body), confirmed: body.confirm === true });
      return window.__previewFetch(url, options);
    };
  }
  window.__previewRequests = [];
  const { pages } = await import('/app/router.js');
  for (const p of pages) {
    await route(p.path);
    const text = document.querySelector('#content')?.textContent || '';
    check('page ' + p.path, !/This page failed to load:|Page not found/.test(text) && text.length > 0);
    check('no overflow ' + p.path, document.documentElement.scrollWidth <= innerWidth);
  }
  await route('/openwrt/cellular');
  check('missing modem disables connection and radio controls', ['Connect', 'Disconnect', 'Reconnect', 'Radio on', 'Radio off'].every(t => button(document.querySelector('#content'), t)?.disabled));
  await route('/openwrt/sms');
  check('missing SMS provider disables compose', [...document.querySelectorAll('#content input,#content textarea')].every(n => n.disabled));
  await route('/openwrt/hardware'); await route('/openwrt/network');
  const before = window.__previewRequests.filter(r => r.path === '/api/openwrt/hardware').length;
  const wan = [...document.querySelectorAll('.form-card')].find(n => n.querySelector('h2')?.textContent === 'Network: wan');
  fill(field(wan, 'APN'), newApn);
  await wait(5200);
  check('hardware timer stops on navigation', window.__previewRequests.filter(r => r.path === '/api/openwrt/hardware').length === before);
  check('status polling preserves dirty form values', field(wan, 'APN').value === newApn);
  const sentBefore = window.__previewRequests.filter(r => r.method === 'PUT' && r.confirmed).length;
  button(wan, 'Save').click(); await wait(200);
  check('native change requests tier confirmation', !!document.querySelector('.modal'));
  button(document.querySelector('.modal'), 'Cancel').click(); await wait(100);
  check('cancel sends no confirmed mutation', window.__previewRequests.filter(r => r.method === 'PUT' && r.confirmed).length === sentBefore);
  button(wan, 'Save').click(); await wait(150); button(document.querySelector('.modal'), 'Apply').click(); await wait(250);
  const writes = window.__previewRequests.filter(r => r.path === '/api/openwrt/interfaces/wan' && r.confirmed);
  check('APN save omits blank secret', writes.length === 1 && !writes[0].keys.includes('password'));
  const api = await import('/app/api.js');
  const data = await api.get('/api/openwrt/interfaces');
  check('APN read-back and secret preservation', data.interfaces.find(i => i.name === 'wan')?.apn === newApn && data.interfaces.find(i => i.name === 'wan')?.password_set === true);
  await route('/openwrt/system');
  const system = document.querySelector('.form-card'); fill(field(system, 'Host name'), 'bad name');
  button(system, 'Save').click(); await wait(150); button(document.querySelector('.modal'), 'Apply').click(); await wait(250);
  check('server field error appears in the form', !!system.querySelector('.has-error'));
  check('invalid hostname did not change backend', (await api.get('/api/openwrt/system')).config.hostname === 'demo-router');
  await api.post('/api/logout', {}); const store = await import('/app/store.js'); await store.refreshNow(); await wait(250);
  check('expired session shows exact login message', document.querySelector('.login-error')?.textContent === 'Session ended. Log in again.');
  const countAfterExpiry = window.__previewRequests.filter(r => r.path === '/api/status').length;
  await wait(4300);
  check('polling stops when session expires', window.__previewRequests.filter(r => r.path === '/api/status').length === countAfterExpiry);
  fill(document.querySelector('#login-pw'), ' preview-admin '); document.querySelector('form').requestSubmit(); await wait(200);
  check('password is not trimmed and bad password is clear', document.querySelector('.login-error')?.textContent === 'Incorrect password');
  button(document, 'Show').click(); check('login password reveal', document.querySelector('#login-pw')?.type === 'text');
  fill(document.querySelector('#login-pw'), 'preview-admin'); document.querySelector('form').requestSubmit(); await wait(350);
  check('login again restores shell', !!document.querySelector('.shell'));
  await route('/');
  const loadsBefore = window.__previewRequests.filter(r => r.path === '/api/openwrt/system').length;
  await route('/openwrt/system');
  check('relogin has one route listener', window.__previewRequests.filter(r => r.path === '/api/openwrt/system').length === loadsBefore + 1);
  check('native menus include all registered pages', pages.every(p => document.querySelector(`.sidebar-nav a[href="#${p.path}"]`)));
  return { passed: results.every(r => r.passed), checks: results.length, results, requestLog: window.__previewRequests };
};
