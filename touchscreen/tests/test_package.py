"""Archive and reversible RAM transaction tests; no real services or device I/O."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import subprocess
import time
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


def load(name):
    spec = importlib.util.spec_from_file_location('touch_' + name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Services:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def running(self, service):
        return service != 'm3100-touchscreen'

    def action(self, service, verb):
        self.calls.append((service, verb))

    def healthy(self, boot_id):
        if self.fail:
            raise RuntimeError('injected launch failure')


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.package = load('package')
        self.install = load('install')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.root = self.base / 'router'
        self.source.mkdir()
        self.root.mkdir()
        # Minimal reviewed fixture tree; never includes original server snapshot.
        for name in self.package.SOURCES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# fixture\n')
        (self.source / 'backend/admin-password').write_text('SECRET_SENTINEL')
        (self.source / 'backend/m3100ui/app.py').write_text('UNREVIEWED_SENTINEL')
        self.bundle = self.base / 'bundle.tar.gz'
        self.package.build(self.source, self.bundle)
        self.digest = hashlib.sha256(self.bundle.read_bytes()).hexdigest()
        values = {'/proc/sys/kernel/random/boot_id': 'boot\n',
                  '/tmp/sysinfo/board_name': 'inseego,m3100\n',
                  '/tmp/sysinfo/model': 'M3100 RAM\n',
                  '/proc/mounts': 'tmpfs / tmpfs rw,relatime 0 0\n',
                  '/proc/mtd': 'dev:    size   erasesize  name\n',
                  '/usr/libexec/m3100-hardware.py': 'original producer',
                  '/etc/init.d/m3100-hardware': 'original hardware service',
                  '/etc/init.d/m3100-webui': 'original webui service',
                  '/opt/m3100-webui/server/m3100ui/services/__init__.py': 'original registration'}
        for name, content in values.items():
            path = self.root / name.lstrip('/')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.original = {name: value for name, value in values.items()
                         if name.startswith(('/usr/', '/etc/', '/opt/'))}
        self.guard = dict(boot_id='boot', board_name='inseego,m3100', model='M3100 RAM',
                          root_mount='tmpfs / tmpfs rw,relatime 0 0', proc_mtd=values['/proc/mtd'],
                          original_files={str(i): dict(device_path=name,
                            sha256=hashlib.sha256(value.encode()).hexdigest())
                            for i, (name, value) in enumerate(self.original.items())})

    def transaction(self, services=None):
        return self.install.Transaction(self.root, services or Services())

    def test_builder_is_reproducible_and_excludes_snapshot_and_credentials(self):
        second = self.base / 'second.tar.gz'
        self.package.build(self.source, second)
        self.assertEqual(self.bundle.read_bytes(), second.read_bytes())
        manifest, payloads = self.install.validate_archive(self.bundle, self.digest)
        self.assertNotIn('backend/admin-password', payloads)
        self.assertNotIn('backend/m3100ui/app.py', payloads)
        self.assertEqual(set(name for name in payloads if name.startswith('backend/')),
                         {'backend/m3100ui/touchscreen_api.py', 'backend/m3100ui/services/__init__.py'})
        self.assertEqual(manifest['version'], 1)

    def test_missing_frontend_refuses_to_build(self):
        (self.source / 'm3100_touch/__main__.py').unlink()
        with self.assertRaises(ValueError):
            self.package.build(self.source, self.base / 'incomplete.tar.gz')

    def altered_archive(self, name, kind=None, data=b'escape'):
        path = self.base / 'bad.tar'
        with tarfile.open(path, 'w') as archive:
            item = tarfile.TarInfo(name)
            if kind is not None:
                item.type = kind
                item.linkname = '/etc/passwd'
            else:
                item.size = len(data)
            archive.addfile(item, io.BytesIO(data) if kind is None else None)
        return path

    def test_traversal_absolute_backslash_links_devices_and_extra_files_rejected(self):
        for name, kind in (('../escape', None), ('/etc/passwd', None), ('a\\b', None),
                           ('m3100_touch/a.py', tarfile.SYMTYPE),
                           ('m3100_touch/a.py', tarfile.LNKTYPE),
                           ('m3100_touch/a.py', tarfile.CHRTYPE), ('unexpected', None)):
            with self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                self.install.validate_archive(self.altered_archive(name, kind))

    def test_outer_digest_and_manifest_payload_tampering_rejected(self):
        with self.assertRaises(ValueError):
            self.install.validate_archive(self.bundle, '0' * 64)
        bad = self.base / 'tamper.tar'
        with tarfile.open(self.bundle) as archive, tarfile.open(bad, 'w') as out:
            for item in archive:
                data = archive.extractfile(item).read()
                if item.name == 'runtime/hardware.py':
                    data = b'corrupted'
                item.size = len(data)
                out.addfile(item, io.BytesIO(data))
        with self.assertRaises(ValueError):
            self.install.validate_archive(bad)

    def test_identity_mtd_mount_and_original_hash_drift_refuse_before_services(self):
        for name, value in (('/proc/sys/kernel/random/boot_id', 'changed'),
                            ('/tmp/sysinfo/model', 'wrong device'),
                            ('/proc/mounts', '/dev/mtdblock0 / squashfs ro 0 0'),
                            ('/proc/mtd', 'dev:    size   erasesize  name\nmtd0: dead'),
                            ('/usr/libexec/m3100-hardware.py', 'drift')):
            path = self.root / name.lstrip('/')
            old = path.read_bytes()
            path.write_text(value)
            services = Services()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.transaction(services).install(self.bundle, self.guard, self.digest)
            self.assertEqual(services.calls, [])
            path.write_bytes(old)

    def test_failed_launch_restores_original_files_and_removes_new_files(self):
        services = Services(fail=True)
        with self.assertRaisesRegex(RuntimeError, 'launch failure'):
            self.transaction(services).install(self.bundle, self.guard, self.digest)
        for name, content in self.original.items():
            self.assertEqual((self.root / name.lstrip('/')).read_text(), content)
        self.assertFalse((self.root / 'etc/init.d/m3100-touchscreen').exists())
        self.assertFalse((self.root / 'opt/m3100-touchscreen/m3100_touch/__main__.py').exists())
        self.assertEqual({service for service, _ in services.calls},
                         {'m3100-hardware', 'm3100-webui', 'm3100-touchscreen'})

    def test_install_idempotency_rollback_and_modes(self):
        services = Services()
        transaction = self.transaction(services)
        receipt = transaction.install(self.bundle, self.guard, self.digest)
        self.assertEqual(receipt['state'], 'installed')
        count = len(services.calls)
        self.assertEqual(transaction.install(self.bundle, self.guard, self.digest)['state'], 'installed')
        self.assertEqual(len(services.calls), count)
        if os.name == 'posix':
            self.assertEqual((self.root / 'etc/init.d/m3100-touchscreen').stat().st_mode & 0o777, 0o755)
            self.assertEqual(transaction.receipt_path('boot').stat().st_mode & 0o777, 0o600)
        transaction.rollback(self.guard)
        for name, content in self.original.items():
            self.assertEqual((self.root / name.lstrip('/')).read_text(), content)
        self.assertEqual(transaction.rollback(self.guard)['state'], 'rolled_back')

    def test_boot_specific_backup_preserves_all_original_guarded_runtime_files(self):
        transaction = self.transaction()
        receipt = transaction.install(self.bundle, self.guard, self.digest)
        directory = transaction.receipt_path('boot').parent
        saved = {entry['destination']: (directory / entry['backup']).read_text()
                 for entry in receipt['original_backups']}
        self.assertEqual(saved, self.original)

    def test_symlink_parent_prevents_writes_outside_guarded_root(self):
        if os.name != 'posix':
            self.skipTest('Linux symlink boundary')
        outside = self.base / 'outside'
        outside.mkdir()
        (self.root / 'opt/m3100-touchscreen').symlink_to(outside, target_is_directory=True)
        services = Services()
        with self.assertRaises(ValueError):
            self.transaction(services).install(self.bundle, self.guard, self.digest)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(services.calls, [])

    def test_rollback_refuses_changed_installed_file_or_corrupt_backup(self):
        transaction = self.transaction()
        transaction.install(self.bundle, self.guard, self.digest)
        path = self.root / 'usr/libexec/m3100-hardware.py'
        path.write_text('external edit')
        with self.assertRaises(ValueError):
            transaction.rollback(self.guard)
        self.assertEqual(path.read_text(), 'external edit')

    def test_corrupt_backup_refuses_rollback_before_service_or_file_changes(self):
        services = Services()
        transaction = self.transaction(services)
        receipt = transaction.install(self.bundle, self.guard, self.digest)
        directory = transaction.receipt_path('boot').parent
        backup = next(entry['backup'] for entry in receipt['entries'] if entry['backup'])
        (directory / backup).write_text('corrupted backup')
        before = (self.root / 'usr/libexec/m3100-hardware.py').read_bytes()
        count = len(services.calls)
        with self.assertRaises(ValueError):
            transaction.rollback(self.guard)
        self.assertEqual(len(services.calls), count)
        self.assertEqual((self.root / 'usr/libexec/m3100-hardware.py').read_bytes(), before)

    def test_partial_atomic_replacement_failure_restores_all_originals(self):
        original_atomic = self.install.atomic
        failed = []
        def injected(path, data, mode):
            if Path(path).as_posix().endswith('/usr/libexec/m3100-hardware.py') and not failed:
                failed.append(True)
                raise OSError('injected replacement failure')
            return original_atomic(path, data, mode)
        with mock.patch.object(self.install, 'atomic', side_effect=injected):
            with self.assertRaisesRegex(OSError, 'replacement failure'):
                self.transaction().install(self.bundle, self.guard, self.digest)
        for name, content in self.original.items():
            self.assertEqual((self.root / name.lstrip('/')).read_text(), content)
        self.assertFalse((self.root / 'opt/m3100-touchscreen/m3100_touch/__main__.py').exists())

    def test_nested_persistent_mount_refuses_even_with_tmpfs_root(self):
        (self.root / 'proc/mounts').write_text('tmpfs / tmpfs rw,relatime 0 0\n/dev/sda /opt ext4 rw 0 0\n')
        services = Services()
        with self.assertRaises(ValueError):
            self.transaction(services).install(self.bundle, self.guard, self.digest)
        self.assertEqual(services.calls, [])

    @unittest.skipUnless(os.name == 'posix', 'Linux procfs symlink')
    def test_readonly_proc_mounts_symlink_is_supported_without_allowing_destination_symlinks(self):
        path = self.root / 'proc/mounts'
        content = path.read_text()
        path.unlink()
        (self.root / 'proc/self').mkdir()
        (self.root / 'proc/self/mounts').write_text(content)
        path.symlink_to('self/mounts')
        receipt = self.transaction().install(self.bundle, self.guard, self.digest)
        self.assertEqual(receipt['state'], 'installed')

    def runtime_aliases(self, mount_type='tmpfs'):
        (self.root / 'run').symlink_to('/var/run', target_is_directory=True)
        (self.root / 'var').symlink_to('tmp', target_is_directory=True)
        (self.root / 'tmp/run').mkdir()
        (self.root / 'proc/mounts').write_text(
            'tmpfs / tmpfs rw,relatime 0 0\ntmpfs /tmp ' + mount_type + ' rw,relatime 0 0\n')

    @unittest.skipUnless(os.name == 'posix', 'Linux OpenWrt runtime aliases')
    def test_expected_run_var_tmp_aliases_preserved_for_install_idempotency_and_rollback(self):
        self.runtime_aliases()
        transaction = self.transaction()
        receipt = transaction.install(self.bundle, self.guard, self.digest)
        self.assertEqual(receipt['state'], 'installed')
        self.assertEqual(transaction.receipt_path('boot'),
                         self.root / 'tmp/run/m3100-touchscreen-backup/boot/receipt.json')
        self.assertTrue((self.root / 'tmp/run/m3100-touchscreen-install.lock').is_file())
        self.assertEqual(transaction.install(self.bundle, self.guard, self.digest)['state'], 'installed')
        self.assertEqual(transaction.rollback(self.guard)['state'], 'rolled_back')
        self.assertEqual(os.readlink(self.root / 'run'), '/var/run')
        self.assertEqual(os.readlink(self.root / 'var'), 'tmp')
        for name, content in self.original.items():
            self.assertEqual((self.root / name.lstrip('/')).read_text(), content)

    @unittest.skipUnless(os.name == 'posix', 'Linux OpenWrt runtime aliases')
    def test_unexpected_runtime_alias_chain_or_persistent_alias_target_refuses_before_services(self):
        self.runtime_aliases()
        services = Services()
        for link, replacement in (('run', '/tmp/run'), ('var', '/other'), ('var', '/tmp')):
            path = self.root / link
            original = os.readlink(path)
            path.unlink()
            path.symlink_to(replacement, target_is_directory=True)
            with self.subTest(link=link), self.assertRaises(ValueError):
                self.transaction(services).install(self.bundle, self.guard, self.digest)
            path.unlink()
            path.symlink_to(original, target_is_directory=True)
        (self.root / 'proc/mounts').write_text(
            'tmpfs / tmpfs rw,relatime 0 0\n/dev/sda /tmp ext4 rw 0 0\n')
        with self.assertRaises(ValueError):
            self.transaction(services).install(self.bundle, self.guard, self.digest)
        self.assertEqual(services.calls, [])
        self.assertFalse((self.root / 'tmp/run/m3100-touchscreen-backup').exists())

    @unittest.skipUnless(os.name == 'posix', 'Linux OpenWrt runtime aliases')
    def test_runtime_alias_allowance_does_not_allow_symlinked_product_parents(self):
        self.runtime_aliases()
        outside = self.base / 'outside'
        outside.mkdir()
        (self.root / 'opt/m3100-touchscreen').symlink_to(outside, target_is_directory=True)
        services = Services()
        with self.assertRaises(ValueError):
            self.transaction(services).install(self.bundle, self.guard, self.digest)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(services.calls, [])

    def test_new_destination_collision_refuses_before_service_changes(self):
        path = self.root / 'etc/init.d/m3100-touchscreen'
        path.write_text('unrelated service')
        services = Services()
        with self.assertRaises(ValueError):
            self.transaction(services).install(self.bundle, self.guard, self.digest)
        self.assertEqual(path.read_text(), 'unrelated service')
        self.assertEqual(services.calls, [])

    def test_original_service_drift_refuses_idempotent_install_and_rollback(self):
        services = Services()
        transaction = self.transaction(services)
        transaction.install(self.bundle, self.guard, self.digest)
        path = self.root / 'etc/init.d/m3100-webui'
        path.write_text('unexpected service edit')
        count = len(services.calls)
        with self.assertRaises(ValueError):
            transaction.install(self.bundle, self.guard, self.digest)
        with self.assertRaises(ValueError):
            transaction.rollback(self.guard)
        self.assertEqual(len(services.calls), count)

    @unittest.skipUnless(os.name == 'posix', 'Linux installer flock')
    def test_concurrent_transaction_lock_refuses_without_service_changes(self):
        import fcntl
        path = self.root / 'run/m3100-touchscreen-install.lock'
        path.parent.mkdir(exist_ok=True)
        with path.open('a+b') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            services = Services()
            with self.assertRaisesRegex(ValueError, 'transaction'):
                self.transaction(services).install(self.bundle, self.guard, self.digest)
            self.assertEqual(services.calls, [])

    def test_stalled_local_status_is_bounded_and_classified_as_unknown_not_stopped(self):
        adapter = self.install.LocalServices()
        def stalled(command, **kwargs):
            self.assertGreater(kwargs.get('timeout', 0), 0, 'status probe has no finite timeout')
            self.assertLessEqual(kwargs['timeout'], 3)
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        with mock.patch.object(self.install.subprocess, 'run', side_effect=stalled):
            with self.assertRaisesRegex(RuntimeError, 'status.*timed out'):
                adapter.running('m3100-webui')

    def test_initial_status_timeout_stops_before_services_or_file_replacements(self):
        adapter = self.install.LocalServices()
        services = Services()
        services.running = adapter.running
        def stalled(command, **kwargs):
            self.assertGreater(kwargs.get('timeout', 0), 0, 'status probe has no finite timeout')
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        transaction = self.transaction(services)
        with mock.patch.object(self.install.subprocess, 'run', side_effect=stalled):
            with self.assertRaisesRegex(RuntimeError, 'status.*timed out'):
                transaction.install(self.bundle, self.guard, self.digest)
        self.assertEqual(services.calls, [])
        for name, content in self.original.items():
            self.assertEqual((self.root / name.lstrip('/')).read_text(), content)
        self.assertFalse(transaction.receipt_path('boot').exists())
        with transaction.transaction_lock(self.guard):
            pass

    @unittest.skipUnless(os.name == 'posix', 'Linux health/display/installer flocks')
    def test_post_start_stalled_status_uses_remaining_budget_rolls_back_and_releases_lock(self):
        import fcntl
        adapter = self.install.LocalServices(root=self.root, status_timeout=3,
                                            action_timeout=20, health_timeout=0.12)
        actions = []
        qualified = []
        original_running = adapter.running
        started = []
        def running(service, **kwargs):
            if not started:
                return service != 'm3100-touchscreen'
            return original_running(service, **kwargs)
        def action(service, verb):
            actions.append((service, verb))
            if service == 'm3100-touchscreen' and verb == 'start':
                started.append(True)
        adapter.running = running
        adapter.action = action
        original_healthy = adapter.healthy
        def healthy(boot_id):
            result = original_healthy(boot_id)
            qualified.append(True)
            return result
        adapter.healthy = healthy
        status = self.root / 'run/m3100-touchscreen.json'
        status.parent.mkdir(exist_ok=True)
        status.write_text(json.dumps(dict(boot_id='boot', pid=os.getpid(), render_ok=True,
                                         monotonic=time.monotonic(), last_frame_monotonic=time.monotonic())))
        budgets = []
        def stalled(command, **kwargs):
            timeout = kwargs.get('timeout')
            self.assertIsNotNone(timeout, 'health status probe has no timeout')
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 0.12)
            budgets.append(timeout)
            # Real short bounded delay models subprocess.run killing a stalled
            # status child at the passed remaining qualification deadline.
            time.sleep(timeout)
            raise subprocess.TimeoutExpired(command, timeout)
        transaction = self.transaction(adapter)
        with (self.root / 'run/m3100-display.lock').open('a+b') as display:
            fcntl.flock(display, fcntl.LOCK_EX | fcntl.LOCK_NB)
            begin = time.monotonic()
            with mock.patch.object(self.install.subprocess, 'run', side_effect=stalled):
                with self.assertRaisesRegex(RuntimeError, 'status.*timed out'):
                    transaction.install(self.bundle, self.guard, self.digest)
            self.assertLess(time.monotonic() - begin, 2)
        self.assertEqual(qualified, [])
        self.assertEqual(len(budgets), 1)
        self.assertIn(('m3100-touchscreen', 'start'), actions)
        self.assertEqual(transaction.read_receipt('boot')['state'], 'rolled_back')
        for name, content in self.original.items():
            self.assertEqual((self.root / name.lstrip('/')).read_text(), content)
        self.assertFalse((self.root / 'etc/init.d/m3100-touchscreen').exists())
        with transaction.transaction_lock(self.guard):
            pass

    def test_status_probe_with_exhausted_deadline_never_starts_subprocess(self):
        adapter = self.install.LocalServices()
        with mock.patch.object(self.install.subprocess, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'deadline'):
                adapter.running('m3100-webui', deadline=time.monotonic() - 1)
        command.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'Linux executable subprocess timeout')
    def test_real_local_status_and_action_children_cannot_block_adapter_indefinitely(self):
        path = self.root / 'etc/init.d/m3100-webui'
        path.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(5)\n')
        path.chmod(0o755)
        adapter = self.install.LocalServices(root=self.root, status_timeout=0.08, action_timeout=0.08)
        begin = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'status.*timed out'):
            adapter.running('m3100-webui')
        for verb in ('start', 'stop'):
            with self.subTest(verb=verb), self.assertRaisesRegex(RuntimeError, 'action.*timed out'):
                adapter.action('m3100-webui', verb)
        self.assertLess(time.monotonic() - begin, 2)

    def test_status_probe_uses_smaller_remaining_deadline(self):
        adapter = self.install.LocalServices()
        def completed(command, **kwargs):
            self.assertAlmostEqual(kwargs['timeout'], 0.25)
            return subprocess.CompletedProcess(command, 0)
        with (mock.patch.object(self.install.time, 'monotonic', return_value=10),
              mock.patch.object(self.install.subprocess, 'run', side_effect=completed)):
            self.assertTrue(adapter.running('m3100-webui', deadline=10.25))


if __name__ == '__main__':
    unittest.main()
