// Services › File sharing: share a USB drive on the local network (SMB), whole drive or selected folders.
import { h, clear, append } from '../../ui/dom.js';
import { card, kv, badge, alert, button, toast, spinner, errorBox } from '../../ui/widgets.js';
import { list } from '../../ui/list.js';
import { get, put, post } from '../../api.js';
import { smartForm, isUnavailable } from '../device/_helpers.js';

const DRIVE = (s) => (s ? String(s).replace(/^usb_drive_/, '').replace(/_/g, ' ') : '—');

export default {
  id: 'services-files', path: '/services/files', group: 'services', order: 20, title: 'File sharing', icon: 'usb',
  feature: 'services.files',
  render(el) {
    const statusBody = h('div', spinner());
    const settings = h('div'), folders = h('div');
    el.append(card('USB drive sharing', statusBody, { icon: 'usb' }), h('div.grid-2', settings, folders));
    let form = null, table = null;

    async function refresh() {
      let d;
      try { d = await get('/api/services/files'); } catch (e) {
        clear(statusBody).appendChild(isUnavailable(e) ? alert('info', e.message) : errorBox(e, refresh));
        return;
      }
      const act = async (url, body, msg) => { await post(url, body); toast(msg); await refresh(); };
      append(clear(statusBody), [
        !d.service_running && alert('info', 'The file-sharing service is not running on this router. It needs a USB drive connected '
          + 'to the router; some models cannot share files at all. Settings below are stored for when it runs.'),
        kv([
          ['Sharing', badge(d.enabled ? 'On' : 'Off', d.enabled ? 'ok' : 'neutral')],
          ['Network name', d.host_name ? h('span.mono', `\\\\${d.host_name}`) : ''],
          ['Shares', d.share_all ? `Whole drive (${d.read_write ? 'read and write' : 'read only'})` : 'Selected folders'],
          ['USB drive', DRIVE(d.drive_status)],
          ['File system', d.fs_type || ''],
          ['Sharing in progress', d.in_progress === null ? '' : (d.in_progress ? 'Yes' : 'No')],
        ]),
        h('div.form-actions',
          button('Start sharing', () => act('/api/services/files/share', { action: 'start' }, 'Sharing started'), { small: true }),
          button('Stop sharing', () => act('/api/services/files/share', { action: 'stop' }, 'Sharing stopped'), { small: true }),
          button('Mount drive', () => act('/api/services/files/drive', { action: 'mount' }, 'Drive mounted'), { small: true }),
          button('Safely remove drive', () => act('/api/services/files/drive', { action: 'unmount' }, 'Drive unmounted — you can unplug it'),
            { small: true }))]);
    }

    smartForm(settings, {
      title: 'Sharing settings', icon: 'system',
      description: 'Devices on your network open the drive as \\\\<network name> in their file manager.',
      load: () => get('/api/services/files'),
      save: async (ch) => { const d = await put('/api/services/files', ch); refresh(); if (table) table.reload(); return d; },
      onReady: (f) => { form = f; },
      fields: [
        { key: 'enabled', label: 'File sharing', type: 'switch', full: true },
        { key: 'host_name', label: 'Network name', type: 'text', maxLength: 15, pattern: /^[A-Za-z0-9][A-Za-z0-9-]{0,14}$/,
          patternMsg: 'Letters, digits and "-" only, up to 15 characters', disabled: (v) => !v.enabled },
        { key: 'share_all', label: 'What to share', type: 'radio', disabled: (v) => !v.enabled,
          options: [[true, 'The whole drive'], [false, 'Only the folders listed below']] },
        { key: 'read_write', label: 'Access', type: 'radio', show: (v) => !!v.share_all, disabled: (v) => !v.enabled,
          options: [[true, 'Read and write'], [false, 'Read only']] },
      ],
    });

    table = list(folders, {
      title: 'Shared folders', icon: 'usb',
      description: 'Top-level folders on the drive that are shared when "Only the folders listed below" is chosen.',
      load: async () => { try { return (await get('/api/services/files')).folders; } catch (e) { if (isUnavailable(e)) return []; throw e; } },
      columns: [{ key: 'name', label: 'Folder', cls: 'mono' }, { key: 'read_only', label: 'Access', format: (v) => (v ? 'Read only' : 'Read and write') }],
      fields: [
        { key: 'name', label: 'Folder name', type: 'text', required: true, maxLength: 64, pattern: /^[A-Za-z0-9_.-]{1,64}$/,
          patternMsg: 'Letters, digits, ".", "_" and "-" only (no spaces)' },
        { key: 'read_only', label: 'Access', type: 'radio', options: [[true, 'Read only'], [false, 'Read and write']] },
      ],
      defaults: { name: '', read_only: true },
      max: 16,
      rowKey: (r) => r.name,
      describe: (r) => `the folder ${r.name}`,
      add: async (row) => { const cur = table.rows(); await put('/api/services/files/folders', { folders: cur.concat([row]) }); },
      update: async (row, old) => {
        const cur = table.rows().map(r => (r.name === old.name ? { name: row.name, read_only: row.read_only } : r));
        await put('/api/services/files/folders', { folders: cur });
      },
      remove: async (row) => {
        const cur = table.rows().filter(r => r.name !== row.name);
        if (!cur.length) throw new Error('Keep at least one folder, or choose to share the whole drive.');
        await put('/api/services/files/folders', { folders: cur });
      },
    });
    refresh();
    return () => { form = null; };
  },
};
