// Run through Chrome DevTools evaluate_script after setting a viewport.
async () => {
  const results = [], check = (name, passed, detail) => results.push({ name, passed: !!passed, detail });
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const change = (label, value) => { const node = document.querySelector('select[aria-label="' + label + '"]'); node.value = value; node.dispatchEvent(new Event('change')); };
  const { pages } = await import('/app/router.js');
  for (const layout of ['horizontal', 'vertical']) {
    change('Navigation', layout);
    for (const p of pages) {
      location.hash = '#' + p.path; await wait(120);
      check(layout + ' overflow ' + p.path, document.documentElement.scrollWidth <= innerWidth);
    }
    if (innerWidth >= 992 && layout === 'horizontal') check('compact horizontal navigation', document.querySelector('.sidebar').getBoundingClientRect().height < 90);
  }
  change('Theme', 'colourful'); change('Navigation', 'horizontal');
  check('palette persists independently', localStorage.getItem('m3ui.theme') === 'colourful' && document.documentElement.dataset.theme === 'colourful');
  change('Theme', 'light');
  check('layout persists independently', localStorage.getItem('m3ui.layout') === 'horizontal');
  if (innerWidth < 992) {
    const open = document.querySelector('.drawer-btn');
    check('closed drawer is inert', document.querySelector('.sidebar').inert);
    open.click(); await wait(50);
    check('drawer opens with expanded state', open.getAttribute('aria-expanded') === 'true' && !document.querySelector('.sidebar').inert);
    check('open drawer moves focus inside', document.querySelector('.sidebar').contains(document.activeElement));
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    check('Escape closes and restores focus', open.getAttribute('aria-expanded') === 'false' && document.activeElement === open && document.querySelector('.sidebar').inert);
    open.click(); document.querySelector('.sidebar-nav a').click(); await wait(150);
    check('page pick closes drawer', !document.body.classList.contains('drawer-open'));
  } else {
    const head = document.querySelector('.nav-group'); head.focus(); head.click();
    check('desktop group expands', head.getAttribute('aria-expanded') === 'true');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    check('desktop Escape restores trigger', document.activeElement === head && head.getAttribute('aria-expanded') === 'false');
  }
  return { width: innerWidth, passed: results.every(r => r.passed), checks: results.length, results };
};
