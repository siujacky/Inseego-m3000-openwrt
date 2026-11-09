// Independent, validated browser preferences. Existing m3ui.theme is preserved.
export function readPreferences(storage) {
  const read = key => { try { return storage.getItem('m3ui.' + key); } catch (_) { return null; } };
  let theme = read('theme');
  const layout = read('layout');
  if (theme === 'system') theme = globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  return { theme: ['dark', 'light', 'colourful'].includes(theme) ? theme : 'dark',
    layout: ['horizontal', 'vertical'].includes(layout) ? layout : 'horizontal' };
}
export function savePreference(storage, key, value) {
  const values = { theme: ['dark', 'light', 'colourful'], layout: ['horizontal', 'vertical'] };
  if (!values[key]?.includes(value)) return;
  try { storage.setItem('m3ui.' + key, value); } catch (_) { /* private browsing */ }
}
