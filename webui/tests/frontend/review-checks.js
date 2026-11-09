// Regression checks for the independent review findings, native preview only.
async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const results = [];
  const check = (name, passed, details) => results.push({ name, passed: !!passed, details });
  if (!document.querySelector('.demo-banner')?.textContent.includes('Host preview')) throw Error('Preview only');
  location.hash = '#/openwrt/dhcp'; await wait(200);
  const leases = [...document.querySelectorAll('.card')].find(c => c.querySelector('h2')?.textContent === 'Current leases');
  check('lease hostname and address readable without JSON', leases?.innerText.includes('demo-laptop') && leases?.innerText.includes('192.168.8.101'));
  location.hash = '#/openwrt/network'; await wait(200);
  const runtime = [...document.querySelectorAll('.card')].find(c => c.querySelector('h2')?.textContent === 'Interface runtime');
  check('interface runtime readable without JSON', runtime?.innerText.includes('br-lan') && runtime?.innerText.includes('192.168.8.1'));
  const luminance = rgb => {
    const channels = rgb.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; });
    return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
  };
  const button = document.createElement('button'); button.className = 'btn btn-danger'; button.textContent = 'Delete'; document.body.appendChild(button);
  for (const theme of ['dark', 'light', 'colourful']) {
    const n = document.querySelector('select[aria-label="Theme"]'); n.value = theme; n.dispatchEvent(new Event('change'));
    const style = getComputedStyle(button), a = luminance(style.color), b = luminance(style.backgroundColor), contrast = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    check('danger contrast ' + theme, contrast >= 4.5, { foreground: style.color, background: style.backgroundColor, contrast });
  }
  button.remove();
  const original = window.fetch, errors = []; let count = 0;
  const onError = e => { errors.push(e.message); e.preventDefault(); };
  window.addEventListener('error', onError);
  window.fetch = async (url, opts) => {
    if (String(url) === '/api/openwrt/hardware') {
      if (++count === 1) { const response = await original(url, opts), data = await response.json(); data.age_s = 29.9; data.fresh = true; return new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } }); }
      await wait(1500); return new Response(JSON.stringify({ error: { code: 'fixture_failure', message: 'Delayed hardware read failed' } }), { status: 503, headers: { 'Content-Type': 'application/json' } });
    }
    return original(url, opts);
  };
  try {
    location.hash = '#/openwrt/hardware'; await wait(400);
    const text = document.querySelector('#content').innerText;
    check('near-expiry observation clears before delayed next read', text.includes('Hardware observation expired') && !text.includes('Fresh producer observation'), { errors });
    check('expiry timer has no exception', errors.length === 0, errors);
  } finally {
    window.fetch = original; window.removeEventListener('error', onError); location.hash = '#/'; await wait(100);
  }
  return { passed: results.every(r => r.passed), checks: results.length, results };
};
