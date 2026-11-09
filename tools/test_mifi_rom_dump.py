"""Offline tests only: never invoke an installed ADB or access a device."""
import hashlib
import io
import json
import contextlib
from pathlib import Path
import tempfile
import subprocess
import shutil
import sys
import tarfile
import unittest
from unittest import mock
import zipfile

try:
    import mifi_rom_dump as dump
except ImportError:
    dump = None


class DumpTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(dump, 'The offline backup engine must exist')

    def test_binary_stream_preserves_nul_crlf_and_all_bytes(self):
        payload = bytes(range(256)) * 8193 + b'\x00\r\n'
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'image.bin'
            count, digest = dump.copy_binary(io.BytesIO(payload), path)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(count, len(payload))
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

    def test_serial_and_transport_fail_closed(self):
        for serial in ['', 'a;touch /tmp/no', '../bad', 'with space']:
            with self.assertRaises(ValueError):
                dump.validate_serial(serial)
        for inventory in ['List of devices attached\nother device\n',
                          'List of devices attached\nchosen unauthorized\n',
                          'List of devices attached\nchosen device\nchosen device\n',
                          'List of devices attached\nchosen offline\nother device\n']:
            with self.assertRaises(ValueError):
                dump.validate_devices(inventory, 'chosen')
        dump.validate_devices('List of devices attached\nchosen device usb:1\n', 'chosen')
        dump.validate_devices('List of devices attached\nchosen device\nother device\nfire unauthorized\n', 'chosen')

    def test_identity_model_is_not_invented(self):
        identity = {'model': 'SDXLEMUR NVTL MBB V2', 'compatible': 'qcom,sdxlemur',
                    'osrelease': '5.4.161-perf', 'os_release': ''}
        dump.validate_identity(identity)
        with self.assertRaises(ValueError):
            dump.validate_identity(identity, 'M3000')
        with self.assertRaises(ValueError):
            dump.validate_identity(dict(identity, model='other'))
        with self.assertRaises(ValueError):
            dump.validate_identity(dict(identity, os_release='OpenWrt 24'))

    def test_partition_discovery_is_not_fixed_indices(self):
        parts = dump.parse_mtd('dev: size erasesize name\nmtd0: 00001000 00000100 "sbl"\n'
                               'mtd27: 00004000 00001000 "persist"\n')
        self.assertEqual([p['index'] for p in parts], [0, 27])
        self.assertEqual(parts[1]['size'], 16384)
        with self.assertRaises(ValueError):
            dump.parse_mtd('dev: size erasesize name\nmtdx: bad\n')

    def test_capture_truncation_and_hash_mismatch_are_failures(self):
        record = {'bytes': 12, 'sha256': 'a' * 64, 'source_sha256': 'b' * 64,
                  'transport_exit': 0, 'remote_exit': 0}
        self.assertIn('size', ' '.join(dump.capture_errors(record, 16)))
        self.assertIn('SHA256', ' '.join(dump.capture_errors(record, 12)))
        self.assertEqual(dump.capture_errors(dict(record, source_sha256='a' * 64), 12), [])

    def test_tar_excludes_nested_mounts_and_quotes_hostile_names(self):
        mounts = dump.parse_mounts('ubi0:rootfs / ubifs rw 0 0\n'
                                   'ubi0:persist /persist ubifs rw 0 0\n'
                                   'proc /proc proc rw 0 0\n')
        command = dump.tar_command(mounts[0], mounts, '--one-file-system --exclude')
        self.assertIn('--exclude=./persist', command)
        self.assertIn('--exclude=./proc', command)
        self.assertNotIn(' -h', command)
        self.assertTrue(command.startswith('unset TAR_OPTIONS; tar '))
        quoted = dump.shell_quote("/mnt/that's; odd")
        self.assertEqual(__import__('shlex').split(quoted), ["/mnt/that's; odd"])
        with self.assertRaises(ValueError):
            dump.tar_command(mounts[0], mounts, 'BusyBox tar without required options')

    def test_regular_file_bind_mount_has_parent_and_explicit_member(self):
        mount = dict(source='ubi0:usrfs', target='/etc/resolv.conf', fstype='ubifs',
                     options='rw', target_type='file')
        args = __import__('shlex').split(dump.tar_command(mount, [mount], '--one-file-system --exclude'))
        self.assertEqual(args[args.index('-C') + 1:], ['/etc', './resolv.conf'])

    def test_partial_backup_zip_and_zip64_safe_paths(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / 'backup'
            folder.mkdir()
            (folder / 'manifest.json').write_text(json.dumps({'complete': False, 'errors': ['failed']}))
            (folder / 'raw').mkdir()
            (folder / 'raw' / 'mtd0.bin').write_bytes(b'partial\x00\r\n')
            archive, digest = dump.package_backup(folder)
            with zipfile.ZipFile(archive) as zf:
                self.assertIsNone(zf.testzip())
                self.assertTrue(all(not n.startswith('/') and '..' not in n.split('/') for n in zf.namelist()))
                self.assertFalse(json.loads(zf.read('backup/manifest.json'))['complete'])
                self.assertTrue(zf.getinfo('backup/raw/mtd0.bin').extract_version >= 45)
            self.assertEqual(digest, hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_root_privilege_restored_when_capture_fails(self):
        class Fake:
            def __init__(self): self.actions = []; self.uid = '2000'
            def check_device(self): return None
            def text(self, cmd): return self.uid if cmd == 'id -u' else ''
            def action(self, op):
                self.actions.append(op)
                if op == 'root': self.uid = '0'
                if op == 'unroot': self.uid = '2000'
        adb = Fake()
        with self.assertRaisesRegex(RuntimeError, 'capture failed'):
            with dump.RootPrivilege(adb, {}):
                raise RuntimeError('capture failed')
        self.assertEqual(adb.actions, ['root', 'wait-for-device', 'unroot', 'wait-for-device'])
        self.assertEqual(adb.uid, '2000')

    def test_root_was_already_privileged_is_not_unrooted(self):
        class Fake:
            def check_device(self): return None
            def text(self, cmd): return '0'
            def action(self, op): raise AssertionError(op)
        with dump.RootPrivilege(Fake(), {}):
            pass

    def test_stream_keeps_remote_status_and_stderr_out_of_binary(self):
        class StreamAdb(dump.Adb):
            def guard(self): pass
            def text(self, command): return '7' if command.endswith('.status') else 'read error'
        adb = StreamAdb('never-run-adb', 'chosen')
        adb.session = '/tmp/mifi-rom-dump-test'
        payload = bytes(range(256)) * 40 + b'\x00\r\n'
        original_popen = subprocess.Popen
        seen = []
        def spawn(argv, **kwargs):
            seen.append(argv)
            return original_popen([sys.executable, '-c',
                                   'import sys; sys.stdout.buffer.write(' + repr(payload) + '); '
                                   'sys.stderr.write("transport diagnostics")'], **kwargs)
        with tempfile.TemporaryDirectory() as td, mock.patch.object(dump.subprocess, 'Popen', side_effect=spawn):
            path = Path(td) / 'raw.bin'
            record = adb.stream("cat '/dev/path with space'", path)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(record['remote_exit'], 7)
            self.assertEqual(record['remote_stderr'], 'read error')
            script = seen[0][-1]
            self.assertIn("cat '/dev/path with space'", script)
            self.assertIn('2>', script)
            self.assertIn('printf', script)
            self.assertTrue(dump.capture_errors(record))

    def test_current_google_adb_exec_out_serialization_executes_raw_program(self):
        # AOSP commandline.cpp exec-out: argv[1] + escape_arg(each later arg).
        # adb shell instead joins arguments without escaping. Never run ADB.
        shell = shutil.which('sh')
        if not shell:
            candidate = Path('C:/Program Files/Git/bin/sh.exe')
            shell = str(candidate) if candidate.exists() else None
        if not shell:
            self.skipTest('Host POSIX sh is needed to emulate current ADB argument serialization')
        original_popen = subprocess.Popen
        def spawn(argv, **kwargs):
            self.assertEqual(argv[:4], ['never-run-adb', '-s', 'chosen', 'exec-out'])
            args = argv[4:]
            def escape_arg(arg): return "'" + arg.replace("'", "'\\''") + "'"
            serialized = args[0] + ''.join(' ' + escape_arg(arg) for arg in args[1:])
            kwargs['stderr'] = subprocess.STDOUT  # exec raw has no separate remote stderr channel.
            return original_popen([shell, '-c', serialized], **kwargs)
        class LocalAdb(dump.Adb):
            def guard(self): pass
            def text(self, command):
                path = __import__('shlex').split(command)[1]
                return Path(path).read_text().rstrip('\n')
        payload = bytes(range(256)) + b'\x00\r\n'
        octal = ''.join('\\%03o' % value for value in payload)
        with tempfile.TemporaryDirectory() as td, mock.patch.object(dump.subprocess, 'Popen', side_effect=spawn):
            adb = LocalAdb('never-run-adb', 'chosen')
            adb.session = Path(td).as_posix()
            target = Path(td) / 'binary.bin'
            record = adb.stream("printf '" + octal + "'; printf 'read failure' >&2; exit 7", target)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(record['remote_exit'], 7)
            self.assertEqual(record['remote_stderr'], 'read failure')
            self.assertEqual(record['transport_exit'], 0)
            self.assertTrue(dump.capture_errors(record))

    def test_google_adb_shell_join_serialization_keeps_metadata_quoting(self):
        shell = shutil.which('sh')
        if not shell:
            candidate = Path('C:/Program Files/Git/bin/sh.exe')
            shell = str(candidate) if candidate.exists() else None
        if not shell:
            self.skipTest('Host POSIX sh is needed to emulate ADB shell argument serialization')
        adb = dump.Adb('never-run-adb', 'chosen')
        def call(args, timeout):
            self.assertEqual(args[0], 'shell')
            return subprocess.check_output([shell, '-c', ' '.join(args[1:])])
        with mock.patch.object(adb, 'call', side_effect=call):
            self.assertEqual(adb.text("printf '%s' 'metadata with spaces'"), 'metadata with spaces')
            with self.assertRaisesRegex(RuntimeError, 'Remote metadata command failed'):
                adb.text("printf '%s' 'partial metadata'; (exit 7)")

    def test_failed_root_request_is_still_restored(self):
        class Fake:
            def __init__(self): self.actions = []
            def check_device(self): pass
            def text(self, cmd): return '2000'
            def action(self, op):
                self.actions.append(op)
                if op == 'root': raise RuntimeError('transport lost after root request')
        adb = Fake()
        with self.assertRaisesRegex(RuntimeError, 'transport lost'):
            with dump.RootPrivilege(adb, {}): pass
        self.assertEqual(adb.actions, ['root', 'unroot', 'wait-for-device'])

    def test_transport_serial_is_verified_before_session_writes(self):
        adb = dump.Adb('never-run-adb', 'chosen')
        with mock.patch.object(adb, 'call', side_effect=[b'List of devices attached\nchosen device\n', b'other\n']):
            with self.assertRaises(ValueError): adb.start_session()
        self.assertIsNone(adb.session)

    def test_cleanup_refuses_changed_marker_or_path_outside_session(self):
        adb = dump.Adb('never-run-adb', 'chosen')
        adb.token = 'a' * 32
        adb.session = '/tmp/mifi-rom-dump-' + adb.token
        adb.temporary = [adb.session + '/owner']
        with mock.patch.object(adb, 'check_device'), mock.patch.object(adb, 'text', return_value='other') as remote:
            with self.assertRaises(ValueError): adb.cleanup()
            self.assertEqual(remote.call_count, 1)
            self.assertNotIn('rm ', remote.call_args[0][0])
        adb.temporary.append('/tmp/unrelated')
        with mock.patch.object(adb, 'check_device'), mock.patch.object(adb, 'text', return_value=adb.token) as remote:
            with self.assertRaises(ValueError): adb.cleanup()
            self.assertEqual(remote.call_count, 1)

    def test_confirmation_rejection_makes_no_privilege_change(self):
        adb = FakeBackupAdb()
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                dump.run_backup(adb, td, confirm=lambda prompt: 'other')
            self.assertEqual(list(Path(td).iterdir()), [])
        self.assertEqual(adb.actions, [])

    def test_end_to_end_failure_still_packages_and_restores_privilege(self):
        adb = FakeBackupAdb(bad_hash=True, tar_supported=False)
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            archive, status = dump.run_backup(adb, td, yes=True)
            self.assertEqual(status, 2)
            with zipfile.ZipFile(archive) as zf:
                name = next(n for n in zf.namelist() if n.endswith('/manifest.json'))
                manifest = json.loads(zf.read(name))
                self.assertFalse(manifest['complete'])
                self.assertTrue(any('SHA256' in e for e in manifest['errors']))
                self.assertTrue(any('tar lacks' in e for e in manifest['errors']))
                self.assertEqual(manifest['restored_uid'], '2000')
                self.assertTrue(any(n.endswith('/raw/mtd27.bin') for n in zf.namelist()))
        self.assertEqual(adb.uid, '2000')
        self.assertTrue(adb.cleaned)

    def test_end_to_end_valid_capture_and_colon_serial_safe_host_path(self):
        adb = FakeBackupAdb()
        adb.serial = '192.0.2.1:5555'
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            archive, status = dump.run_backup(adb, td, yes=True)
            self.assertEqual(status, 0)
            self.assertNotIn(':', archive.name)
            self.assertIn('192.0.2.1_5555-', archive.name)
            with zipfile.ZipFile(archive) as zf:
                self.assertIsNone(zf.testzip())

    def test_truncated_partition_in_partial_zip_is_never_complete(self):
        class Truncated(FakeBackupAdb):
            def stream(self, command, path):
                record = super().stream(command, path)
                if command.startswith('dd '):
                    path.write_bytes(self.payload[:-1])
                    record.update(bytes=len(self.payload) - 1,
                                  sha256=hashlib.sha256(self.payload[:-1]).hexdigest())
                return record
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            archive, status = dump.run_backup(Truncated(), td, yes=True)
            self.assertEqual(status, 2)
            with zipfile.ZipFile(archive) as zf:
                manifest = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith('/manifest.json'))))
                self.assertFalse(manifest['complete'])
                self.assertTrue(any('partition size' in e for e in manifest['errors']))
                image = zf.read(next(n for n in zf.namelist() if n.endswith('/mtd27.bin')))
                self.assertEqual(len(image), 4095)

    def test_identity_change_after_root_blocks_capture_and_restores_uid(self):
        class ChangedIdentity(FakeBackupAdb):
            def text(self, command):
                if self.uid == '0' and '/proc/device-tree/model' in command:
                    return 'SDXLEMUR NVTL MBB V3'
                return super().text(command)
            def start_session(self):
                raise AssertionError('Identity change must be detected before session creation')
        adb = ChangedIdentity()
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            archive, status = dump.run_backup(adb, td, yes=True)
            self.assertEqual(status, 2)
            with zipfile.ZipFile(archive) as zf:
                manifest = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith('/manifest.json'))))
                self.assertTrue(any('Identity changed after adb root' in e for e in manifest['errors']))
                self.assertEqual(manifest['restored_uid'], '2000')

    def test_auto_detect_selects_only_eligible_mifi_and_skips_fire_and_offline(self):
        inventory = 'List of devices attached\nfire device\nmifi device\nlocked unauthorized\nasleep offline\n'
        inspected = []
        def factory(executable, serial):
            inspected.append(serial)
            return FakeDiscoveryAdb(serial, fire=(serial == 'fire'))
        with mock.patch.object(dump, 'list_transports', return_value=inventory), contextlib.redirect_stdout(io.StringIO()):
            adb, discovery = dump.select_device('bundled adb.exe', adb_factory=factory)
        self.assertEqual(adb.serial, 'mifi')
        self.assertEqual(inspected, ['fire', 'mifi'])
        self.assertEqual(discovery['selected_serial'], 'mifi')
        self.assertEqual(discovery['mode'], 'auto')
        self.assertFalse(adb.actions)

    def test_auto_detect_zero_eligible_and_openwrt_fail_without_root(self):
        instances = []
        def factory(executable, serial):
            adb = FakeDiscoveryAdb(serial, fire=(serial == 'fire'), openwrt=(serial == 'router'))
            instances.append(adb)
            return adb
        with mock.patch.object(dump, 'list_transports', return_value='fire device\nrouter device\n'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'No eligible'):
                dump.select_device('fake-adb', adb_factory=factory)
        self.assertTrue(all(not adb.actions for adb in instances))

    def test_auto_detect_multiple_prompts_number_and_yes_rejects_ambiguity(self):
        inventory = 'one device\ntwo device\n'
        factory = lambda executable, serial: FakeDiscoveryAdb(serial)
        with mock.patch.object(dump, 'list_transports', return_value=inventory), contextlib.redirect_stdout(io.StringIO()):
            adb, report = dump.select_device('fake-adb', choose=lambda prompt: '2', adb_factory=factory)
            self.assertEqual(adb.serial, 'two')
            self.assertEqual(report['selected_serial'], 'two')
            with self.assertRaisesRegex(ValueError, 'Multiple eligible'):
                dump.select_device('fake-adb', yes=True, choose=lambda prompt: self.fail('must not prompt'), adb_factory=factory)
            with self.assertRaisesRegex(ValueError, 'Invalid device selection'):
                dump.select_device('fake-adb', choose=lambda prompt: '0', adb_factory=factory)

    def test_auto_detect_duplicate_serial_is_not_eligible(self):
        with mock.patch.object(dump, 'list_transports', return_value='same device\nsame device\n'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'No eligible'):
                dump.select_device('fake-adb', adb_factory=lambda *args: self.fail('duplicate must not be inspected'))

    def test_explicit_serial_override_bypasses_discovery(self):
        with mock.patch.object(dump, 'list_transports', side_effect=AssertionError('explicit selection must not enumerate here')):
            adb, report = dump.select_device('fake-adb', serial='manual', adb_factory=lambda executable, serial: FakeDiscoveryAdb(serial))
            with self.assertRaises(ValueError):
                dump.select_device('fake-adb', serial='')
        self.assertEqual(adb.serial, 'manual')
        self.assertEqual(report['mode'], 'explicit')

    def test_bundle_adb_with_spaces_default_and_manual_override_in_main(self):
        with tempfile.TemporaryDirectory(prefix='ADB bundle with spaces ') as td:
            directory = Path(td)
            bundled = directory / 'platform-tools' / 'adb.exe'
            bundled.parent.mkdir()
            bundled.write_bytes(b'fake executable; never run')
            manual = directory / 'manual adb.exe'
            manual.write_bytes(b'fake executable; never run')
            selected = FakeBackupAdb()
            with mock.patch.object(dump, '__file__', str(directory / 'mifi_rom_dump.py')), mock.patch.object(dump, 'select_device', return_value=(selected, {'mode': 'auto'})) as select, mock.patch.object(dump, 'run_backup', return_value=(Path('fake.zip'), 0)) as backup:
                self.assertEqual(dump.main([]), 0)
                self.assertEqual(select.call_args.args[0], str(bundled.resolve()))
                self.assertTrue(backup.call_args.kwargs['yes'])
                self.assertEqual(dump.main(['--adb', str(manual), '--serial', 'chosen']), 0)
                self.assertEqual(select.call_args.args[0], str(manual.resolve()))
                self.assertEqual(select.call_args.kwargs['serial'], 'chosen')
                self.assertFalse(backup.call_args.kwargs['yes'])

    def test_missing_bundle_uses_path_but_invalid_override_does_not(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(dump, '__file__', str(Path(td) / 'mifi_rom_dump.py')), mock.patch.object(dump.shutil, 'which', return_value='C:/adb from PATH/adb.exe') as which:
            self.assertEqual(dump.resolve_adb(), 'C:/adb from PATH/adb.exe')
            which.assert_called_once_with('adb')
        with mock.patch.object(dump.shutil, 'which', return_value=None):
            with self.assertRaises(FileNotFoundError): dump.resolve_adb('definitely-missing-adb-executable.exe')

    def test_each_online_candidate_shell_request_is_bound_to_its_serial(self):
        records = []
        inventory = b'List of devices attached\nfire device\nmifi device\nlocked unauthorized\n'
        def run(argv, **kwargs):
            records.append(argv)
            if argv[1:] == ['devices', '-l']:
                return subprocess.CompletedProcess(argv, 0, inventory, b'')
            self.assertEqual(argv[1], '-s')
            serial = argv[2]
            self.assertIn(serial, ('fire', 'mifi'))
            args = argv[3:]
            if args == ['devices', '-l']: out = inventory
            elif args == ['get-serialno']: out = serial.encode()
            else:
                self.assertEqual(args[:3], ['shell', 'sh', '-c'])
                script = __import__('shlex').split(args[-1])[0]
                marker = __import__('re').search(r'__MIFI_RC_[a-f0-9]+__', script)[0]
                value = FakeDiscoveryAdb(serial, fire=(serial == 'fire')).text(script.split('; rc=$?;')[0])
                out = (value + '\n' + marker + '0\n').encode()
            return subprocess.CompletedProcess(argv, 0, out, b'')
        with mock.patch.object(dump.subprocess, 'run', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            adb, report = dump.select_device('never-run-adb')
        self.assertEqual(adb.serial, 'mifi')
        self.assertTrue(all(argv[1:] == ['devices', '-l'] or argv[1:3] in (['-s', 'fire'], ['-s', 'mifi']) for argv in records))
        self.assertFalse(any('root' in argv or 'unroot' in argv or 'exec-out' in argv for argv in records))


class FakeDiscoveryAdb:
    def __init__(self, serial, fire=False, openwrt=False):
        self.serial, self.fire, self.openwrt = serial, fire, openwrt
        self.actions, self.log = [], []
    def check_device(self): pass
    def text(self, command):
        if '/proc/device-tree/model' in command: return 'Amazon Fire' if self.fire else 'SDXLEMUR NVTL MBB V2'
        if '/proc/device-tree/compatible' in command: return 'mediatek,fire' if self.fire else 'qcom,sdxlemur'
        if '/proc/sys/kernel/osrelease' in command: return '5.4.161-perf'
        if '/etc/openwrt_release' in command and self.openwrt: return "DISTRIB_ID='OpenWrt'"
        return ''


class FakeBackupAdb:
    """Controlled stock-like responses; no process creation or device access."""
    def __init__(self, bad_hash=False, tar_supported=True):
        self.serial, self.uid, self.log, self.actions = 'chosen', '2000', [], []
        self.bad_hash, self.tar_supported, self.cleaned = bad_hash, tar_supported, False
        self.payload = bytes(range(256)) * 16

    def check_device(self): pass
    def start_session(self): pass
    def cleanup(self): self.cleaned = True
    def action(self, op):
        self.actions.append(op)
        if op == 'root': self.uid = '0'
        elif op == 'unroot': self.uid = '2000'

    def text(self, command):
        if command == 'id -u': return self.uid
        if command.startswith('if [ -d '): return 'directory'
        if command.startswith('if [ -r '):
            if '/proc/device-tree/model' in command: return 'SDXLEMUR NVTL MBB V2'
            if '/proc/device-tree/compatible' in command: return 'qcom,sdxlemur'
            if '/proc/sys/kernel/osrelease' in command: return '5.4.161-perf'
            return ''
        if command == 'cat /proc/mtd': return 'dev: size erasesize name\nmtd27: 00001000 00000100 "persist"'
        if command == 'cat /proc/mounts': return 'ubi0:rootfs / ubifs rw 0 0\nproc /proc proc rw 0 0'
        if command == 'tar --help': return '--one-file-system --exclude' if self.tar_supported else 'tar'
        if command == 'command -v sha256sum || true': return '/usr/bin/sha256sum'
        if command.startswith('sha256sum '): return ('0' * 64 if self.bad_hash else hashlib.sha256(self.payload).hexdigest()) + '  /dev/mtd27'
        if command.startswith('cat /sys/class/mtd/'):
            return '4096' if '/size ' in command else '0'
        return ''

    def stream(self, command, path):
        payload = self.payload
        if command.startswith('unset TAR_OPTIONS; tar '):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w:gz') as archive:
                member = tarfile.TarInfo('./file')
                member.size = 3
                archive.addfile(member, io.BytesIO(b'abc'))
            payload = output.getvalue()
        path.write_bytes(payload)
        return dict(command=command, path=str(path), bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(), transport_exit=0, remote_exit=0)


if __name__ == '__main__':
    unittest.main()
