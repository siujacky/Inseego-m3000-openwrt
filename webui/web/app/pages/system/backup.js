// System › Backup & restore: encrypted settings backup (download) and restore from a file (tier 3, restarts).
import { h, clear } from '../../ui/dom.js';
import { card, kv, badge, alert, button, toast, spinner, errorBox, empty } from '../../ui/widgets.js';
import { get, post } from '../../api.js';
import * as fmt from '../../ui/format.js';
import { icon } from '../../ui/icons.js';
import { postDownload, stamp, waitForRestart, notify } from './_common.js';

function backupCard(host, info, refresh) {
  const pw = h('input', { type: 'password', autocomplete: 'current-password', placeholder: 'Admin password', style: { maxWidth: '280px' } });
  const btn = button('Download backup', async () => {
    if (!pw.value) { pw.focus(); toast('Enter the admin password first', 'bad'); return; }
    try {
      await postDownload('/api/system/backup', { password: pw.value }, `settings-backup-${stamp()}.bin`);
      pw.value = '';
      toast('Backup downloaded');
      refresh();
    } catch (e) {
      if (e.field === 'password' || e.code === 'reauth_failed') { toast('Wrong password', 'bad'); pw.select(); } else notify(e);
    }
  }, { kind: 'primary', icon: 'download' });
  pw.addEventListener('keydown', (e) => { if (e.key === 'Enter') btn.click(); });
  host.appendChild(card('Back up settings', [
    h('p.card-desc', 'Saves the router\'s settings to a file on this computer. ' + info.contents),
    alert('info', 'The file can only be restored on a router of the same model. It contains your passwords in encrypted form: keep it private.'),
    h('div.field', { style: { marginTop: '12px' } }, h('label.field-label', 'Confirm with the admin password'),
      h('div.input-group', pw, btn)),
  ], { icon: 'download' }));
}

function historyCard(host, info) {
  const rows = info.history || [];
  host.appendChild(card('Backups made here', rows.length ? h('div.table-wrap', h('table.table',
    h('thead', h('tr', h('th', 'Created'), h('th', 'Size'), h('th', 'Fingerprint'))),
    h('tbody', rows.map(r => h('tr', h('td', { dataset: { label: 'Created' } }, fmt.dateTime(r.t)),
      h('td', { dataset: { label: 'Size' } }, fmt.bytes(r.size)), h('td.mono', { dataset: { label: 'Fingerprint' } }, r.sha256))))))
    : empty('No backup has been made with this interface yet.'), { icon: 'clock', subtitle: 'used to recognise your files when restoring' }));
}

function restoreCard(host) {
  const status = h('div');
  const file = h('input', { type: 'file', accept: '.bin,application/octet-stream,*/*' });
  let pending = null;
  const restoreBtn = h('button.btn.btn-danger', { type: 'button', disabled: true, onclick: async () => {
    if (!pending || restoreBtn.classList.contains('busy')) return;
    restoreBtn.classList.add('busy');
    try {
      const res = await post('/api/system/backup/restore', { token: pending.token }, {
        confirmTitle: 'Restore settings',
        confirmMessage: 'All current settings will be replaced by the ones in the file and the router will restart. '
          + 'Devices will disconnect. If the file changes the Wi-Fi or network settings, reconnect afterwards.',
        confirmPhrase: 'RESTORE',
      });
      pending = null;
      file.value = '';
      if (res.restarting) {
        waitForRestart({ instance: res.instance, title: 'Restoring settings', expectedS: res.expected_s || 120,
          message: 'The settings were restored. The router is restarting to apply them.' });
      } else toast('Settings restored');
    } catch (e) {
      if (e.field === 'token') { pending = null; file.value = ''; }
      notify(e);
    } finally {
      restoreBtn.classList.remove('busy');
      render();
    }
  } }, icon('upload'), h('span', 'Restore and restart'));

  function render() {
    clear(status);
    restoreBtn.disabled = !pending;
    if (!pending) return;
    status.append(kv([
      ['File', pending.name || '—'], ['Size', fmt.bytes(pending.size)], ['Fingerprint', h('span.mono', pending.sha256)],
      ['Origin', pending.created_here ? badge('Made by this router on ' + fmt.dateTime(pending.created_at), 'ok')
        : badge('Not made with this interface (check that it is from this model)', 'warn')],
    ]));
  }

  file.addEventListener('change', async () => {
    pending = null;
    render();
    const f = file.files && file.files[0];
    if (!f) return;
    clear(status).appendChild(spinner('Checking the file…'));
    try {
      const res = await post('/api/system/backup/upload?name=' + encodeURIComponent(f.name), f);
      pending = Object.assign({ name: f.name }, res);
      render();
    } catch (e) {
      clear(status).appendChild(errorBox(e));
      file.value = '';
    }
  });

  host.appendChild(card('Restore settings', [
    h('p.card-desc', 'Replaces all settings with the ones saved in a backup file, then restarts the router. '
      + 'Takes about two minutes; connected devices disconnect meanwhile.'),
    h('div.field', h('label.field-label', 'Backup file'), file),
    status,
    h('div.form-foot', h('div.form-actions', restoreBtn)),
  ], { icon: 'upload' }));
}

export default {
  id: 'system-backup', path: '/system/backup', group: 'system', order: 20, title: 'Backup & restore', icon: 'download',
  async render(el, ctx) {
    const grid = h('div.grid-2'), left = h('div'), right = h('div'), lower = h('div');
    grid.append(left, right);
    el.append(grid, lower);
    left.appendChild(spinner());
    const load = async () => {
      let info;
      try { info = await get('/api/system/backup'); } catch (e) { clear(left).appendChild(errorBox(e, load)); return; }
      clear(left); clear(lower);
      backupCard(left, info, load);
      historyCard(lower, info);
    };
    await load();
    restoreCard(right);
    el.appendChild(card('Factory reset', h('p.card-desc', 'To erase every setting and start over, use ',
      h('a', { href: '#/system/power' }, 'Restart & reset'), '.'), { icon: 'power' }));
  },
};
