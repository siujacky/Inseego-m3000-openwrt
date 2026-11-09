import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
const target = new URL('../../web/app/preferences.js', import.meta.url);
const prefs = existsSync(target) ? await import(target) : {};
const storage = (initial = {}) => { const data = { ...initial }; return { getItem: k => data[k] ?? null, setItem: (k, v) => data[k] = v }; };

test('new visitor gets dark and horizontal', () => {
  assert.equal(typeof prefs.readPreferences, 'function');
  assert.deepEqual(prefs.readPreferences(storage()), { theme: 'dark', layout: 'horizontal' });
});
test('saved light/dark preference survives and invalid values are rejected', () => {
  assert.equal(typeof prefs.readPreferences, 'function');
  assert.equal(prefs.readPreferences(storage({ 'm3ui.theme': 'light' })).theme, 'light');
  assert.deepEqual(prefs.readPreferences(storage({ 'm3ui.theme': 'invalid', 'm3ui.layout': 'bad' })), { theme: 'dark', layout: 'horizontal' });
});
test('palette and layout are independent; storage errors are harmless', () => {
  assert.equal(typeof prefs.savePreference, 'function');
  const s = storage(); prefs.savePreference(s, 'theme', 'colourful'); prefs.savePreference(s, 'layout', 'vertical');
  assert.deepEqual(prefs.readPreferences(s), { theme: 'colourful', layout: 'vertical' });
  prefs.savePreference(s, 'theme', 'light'); prefs.savePreference(s, 'layout', 'invalid');
  assert.deepEqual(prefs.readPreferences(s), { theme: 'light', layout: 'vertical' });
  assert.deepEqual(prefs.readPreferences({ getItem() { throw Error('blocked'); } }), { theme: 'dark', layout: 'horizontal' });
});
test('legacy system preference follows the OS palette', () => {
  globalThis.matchMedia = () => ({ matches: false });
  assert.equal(prefs.readPreferences(storage({ 'm3ui.theme': 'system' })).theme, 'light');
  globalThis.matchMedia = () => ({ matches: true });
  assert.equal(prefs.readPreferences(storage({ 'm3ui.theme': 'system' })).theme, 'dark');
  delete globalThis.matchMedia;
});
