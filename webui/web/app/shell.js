// Feelgood-inspired shell; no LuCI runtime or platform scripts are required.
import { h, clear } from './ui/dom.js';
import { icon } from './ui/icons.js';
import { GROUPS } from './nav.js';
import { pages } from './router.js';
import { readPreferences, savePreference } from './preferences.js';

const storage = () => { try { return localStorage; } catch (_) { return null; } };
export function applyPreferences() {
  const prefs = readPreferences(storage());
  document.documentElement.dataset.theme = prefs.theme;
  document.documentElement.dataset.layout = prefs.layout;
  document.querySelectorAll('select[aria-label="Theme"]').forEach(n => n.value = prefs.theme);
  document.querySelectorAll('select[aria-label="Navigation"]').forEach(n => n.value = prefs.layout);
  return prefs;
}
export function appearanceControls() {
  const prefs = applyPreferences();
  const select = (name, values, current, label) => h('label.appearance-control', h('span', label),
    h('select', { 'aria-label': label, onchange: e => { savePreference(storage(), name, e.target.value); applyPreferences(); } },
      values.map(([value, text]) => h('option', { value, selected: current === value }, text))));
  return h('div.appearance', select('theme', [['dark', 'Dark'], ['light', 'Light'], ['colourful', 'Colourful']], prefs.theme, 'Theme'),
    select('layout', [['horizontal', 'Horizontal'], ['vertical', 'Vertical']], prefs.layout, 'Navigation'));
}

export function createShell({ session, store, statusBar, logout }) {
  const content = h('main#content.content', { tabindex: '-1' });
  const nav = h('nav.sidebar-nav', { 'aria-label': 'Main navigation', id: 'router-navigation' });
  const isSmall = () => matchMedia('(max-width: 991px)').matches;
  let openHead = null;
  const closeGroups = () => {
    nav.querySelectorAll('.nav-group').forEach(b => { b.classList.remove('open'); b.setAttribute('aria-expanded', 'false'); });
    openHead = null;
  };
  const drawerButton = h('button.icon-btn.drawer-btn', { type: 'button', 'aria-label': 'Open menu', 'aria-expanded': 'false', 'aria-controls': 'router-navigation', onclick: () => {
    sidebar.inert = false; sidebar.setAttribute('aria-hidden', 'false');
    root.querySelector('.main').inert = true;
    document.body.classList.add('drawer-open'); drawerButton.setAttribute('aria-expanded', 'true');
    const current = nav.querySelector('a.active');
    const head = current?.closest('.nav-sec')?.querySelector('.nav-group');
    if (head && !head.classList.contains('open')) head.click();
    (current || nav.querySelector('a,button') || content).focus();
  } }, icon('menu'));
  const closeDrawer = (restore = false) => {
    document.body.classList.remove('drawer-open'); drawerButton.setAttribute('aria-expanded', 'false');
    sidebar.inert = isSmall(); sidebar.setAttribute('aria-hidden', String(isSmall()));
    root.querySelector('.main').inert = false;
    closeGroups(); if (restore) drawerButton.focus();
  };
  const visible = pages.filter(p => (!p.expert || session.expert_mode) && store.feature(p.feature));
  for (const group of GROUPS) {
    const items = visible.filter(p => p.group === group.id);
    if (!items.length) continue;
    const pick = () => { closeDrawer(); content.focus(); };
    if (items.length === 1) {
      nav.appendChild(h('a.nav-top', { href: '#' + items[0].path, dataset: { path: items[0].path }, onclick: pick }, icon(group.icon), group.title));
      continue;
    }
    const listId = 'nav-' + group.id;
    const list = h('div.nav-items', { id: listId }, items.map(p => h('a.nav-item', { href: '#' + p.path, dataset: { path: p.path }, onclick: pick }, p.title)));
    const head = h('button.nav-group', { type: 'button', 'aria-expanded': 'false', 'aria-controls': listId, onclick: () => {
      const wasOpen = head.classList.contains('open'); closeGroups();
      if (!wasOpen) { head.classList.add('open'); head.setAttribute('aria-expanded', 'true'); openHead = head; }
    } }, icon(group.icon), h('span', group.title), icon('chevron', 'chev'));
    nav.appendChild(h('div.nav-sec', head, list));
  }
  const sidebar = h('aside.sidebar',
    h('div.brand', h('img', { src: '/app/img/icon.svg', alt: '', width: 28, height: 28 }), h('span.brand-name', session.router_name || '5G Router'),
      h('button.icon-btn.drawer-close', { type: 'button', 'aria-label': 'Close menu', onclick: () => closeDrawer(true) }, icon('close'))), nav);
  const top = h('header.topbar', drawerButton, h('div#crumb.crumb'), statusBar,
    h('div.top-actions', appearanceControls(), h('button.icon-btn', { type: 'button', 'aria-label': 'Log out', title: 'Log out', onclick: logout }, icon('logout'))));
  const banner = session.mock && h('div.demo-banner', { role: 'status' }, 'Demo data · Host preview — no router is connected');
  const ram = store.state.meta?.platform === 'openwrt' && h('div.ram-notice', 'RAM session · Settings last until the router restarts.');
  const root = h('div.shell', sidebar, h('div.main', top, banner, ram, content,
    h('footer.app-footer', '5G Router', h('span', 'Local management · ', h('a', { href: '/THIRD-PARTY-NOTICES.txt', target: '_blank', rel: 'noopener' }, 'Credits')))));
  const scrim = h('div.scrim', { onclick: () => closeDrawer(true) });
  const outside = e => { if (!sidebar.contains(e.target) && !drawerButton.contains(e.target)) closeGroups(); };
  const onKey = e => {
    if (e.key === 'Escape') {
      const previous = openHead; closeGroups();
      if (document.body.classList.contains('drawer-open')) closeDrawer(true); else previous?.focus();
    }
    if (e.key === 'Tab' && isSmall() && document.body.classList.contains('drawer-open')) {
      const focusable = [...sidebar.querySelectorAll('a,button')].filter(n => n.getClientRects().length);
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  const onResize = () => { if (!isSmall()) closeDrawer(); else if (!document.body.classList.contains('drawer-open')) { sidebar.inert = true; sidebar.setAttribute('aria-hidden', 'true'); } };
  onResize();
  document.addEventListener('click', outside); document.addEventListener('keydown', onKey); window.addEventListener('resize', onResize);
  function markActive(page) {
    nav.querySelectorAll('a').forEach(a => { const active = a.dataset.path === page.path; a.classList.toggle('active', active); if (active) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    const group = GROUPS.find(g => g.id === page.group);
    clear(root.querySelector('#crumb')).append(h('span.crumb-group', group?.title || ''), h('span.crumb-page', page.title));
    document.title = page.title + ' · ' + (session.router_name || '5G Router');
  }
  return { root, scrim, content, markActive, destroy() {
    closeDrawer(); document.removeEventListener('click', outside); document.removeEventListener('keydown', onKey); window.removeEventListener('resize', onResize);
  } };
}
