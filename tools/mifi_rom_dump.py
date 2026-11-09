#!/usr/bin/env python3
"""Read-only stock Inseego SDXLEMUR backup; Python 3 standard library only.

Auto-detects eligible stock Inseego devices; --serial overrides selection.
Never invokes reboot,
flash, erase, unlock, mount, remount, or filesystem freezing operations.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import uuid
import zipfile

shell_quote = shlex.quote
VOLATILE = ('/dev', '/proc', '/sys', '/run', '/tmp', '/var/run', '/var/lock',
            '/var/tmp', '/var/log', '/opt/nvtl/log')
IDENTITY_FILES = {
    'model': '/proc/device-tree/model', 'compatible': '/proc/device-tree/compatible',
    'board_serial': '/proc/device-tree/serial-number',
    'soc_machine': '/sys/devices/soc0/machine', 'soc_family': '/sys/devices/soc0/family',
    'soc_serial': '/sys/devices/soc0/serial_number',
    'osrelease': '/proc/sys/kernel/osrelease', 'os_release': '/etc/os-release',
    'openwrt_release': '/etc/openwrt_release', 'cmdline': '/proc/cmdline',
}


def validate_serial(serial):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', serial):
        raise ValueError('An explicit, unambiguous ADB serial is required')
    return serial


def device_rows(inventory):
    return [line.split() for line in inventory.splitlines()
            if line.strip() and not line.startswith(('List of devices', '*'))]


def validate_devices(inventory, serial):
    rows = device_rows(inventory)
    selected = [row for row in rows if row[0] == serial]
    if len(selected) != 1 or selected[0][:2] != [serial, 'device']:
        raise ValueError('The exact --serial must identify one authorized online transport; missing/duplicate/offline selections are rejected')


def validate_identity(identity, expect_model=None):
    board = identity.get('model', '') + ' ' + identity.get('soc_machine', '')
    vendor = board + ' ' + identity.get('compatible', '')
    if ('SDXLEMUR' not in board.upper() or
            not re.search(r'NVTL|INSEEGO|NOVATEL', vendor, re.I) or
            not re.search(r'qcom|qualcomm', identity.get('compatible', '') + ' ' +
                          identity.get('soc_family', ''), re.I)):
        raise ValueError('Identity is not a verified stock Inseego/Qualcomm SDXLEMUR board')
    if ('openwrt' in ' '.join(identity.values()).lower() or not identity.get('osrelease')):
        raise ValueError('Stock identity gate rejected this operating system')
    if expect_model and expect_model.casefold() not in board.casefold():
        raise ValueError('--expect-model does not match the reported board model; marketing model is not inferred')


def read_identity(adb):
    identity = {'adb_serial': adb.serial}
    for name, path in IDENTITY_FILES.items():
        identity[name] = adb.text('if [ -r ' + shell_quote(path) + ' ]; then cat ' +
                                  shell_quote(path) + '; fi').replace('\x00', ' ').strip()
    return identity


def parse_mtd(text):
    result = []
    for line in text.splitlines():
        if line.startswith('dev:') or not line.strip():
            continue
        match = re.fullmatch(r'mtd(\d+):\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+"(.*)"', line)
        if not match:
            raise ValueError('Unrecognized /proc/mtd line: ' + line)
        index, size, erase, name = match.groups()
        result.append(dict(index=int(index), size=int(size, 16),
                           erasesize=int(erase, 16), name=name))
    if not result or len({p['index'] for p in result}) != len(result):
        raise ValueError('Empty or duplicate /proc/mtd partition inventory')
    return result


def parse_mounts(text):
    def unescape(value):
        return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
    mounts = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4:
            raise ValueError('Malformed /proc/mounts entry')
        source, target, kind, options = map(unescape, fields[:4])
        if not target.startswith('/') or '..' in PurePosixPath(target).parts:
            raise ValueError('Unsafe mount target')
        mounts.append(dict(source=source, target=target, fstype=kind, options=options))
    return mounts


def persistent_mount(mount):
    target, kind, source = mount['target'], mount['fstype'], mount['source']
    if any(target == p or target.startswith(p + '/') for p in VOLATILE):
        return False
    return kind in {'ubifs', 'jffs2', 'ext2', 'ext3', 'ext4', 'f2fs', 'squashfs',
                    'vfat', 'xfs', 'yaffs', 'yaffs2', 'overlay'} or source.startswith(('/dev/', 'ubi'))


def tar_command(mount, mounts, helptext):
    if not all(option in helptext for option in ('--one-file-system', '--exclude')):
        raise ValueError('tar lacks --one-file-system/--exclude; logical capture cannot safely traverse this device')
    target = mount['target'].rstrip('/') or '/'
    exclude = set(VOLATILE)
    exclude.update(m['target'] for m in mounts if m['target'] != target)
    relative = []
    for path in sorted(exclude):
        if target == '/' and path != '/':
            relative.append('./' + path.lstrip('/'))
        elif path.startswith(target + '/'):
            relative.append('./' + path[len(target) + 1:])
    options = ['tar', '--one-file-system']
    if '--numeric-owner' in helptext:
        options.append('--numeric-owner')
    if '--xattrs' in helptext:
        options.append('--xattrs')
    if '--acls' in helptext:
        options.append('--acls')
    options.extend('--exclude=' + path for path in relative)
    if mount.get('target_type') == 'file':
        options.extend(['-czf', '-', '-C', str(PurePosixPath(target).parent),
                        './' + PurePosixPath(target).name])
    else:
        options.extend(['-czf', '-', '-C', target, '.'])
    return 'unset TAR_OPTIONS; ' + ' '.join(map(shell_quote, options))


def copy_binary(source, destination):
    digest, count = hashlib.sha256(), 0
    with Path(destination).open('xb') as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
            count += len(chunk)
    return count, digest.hexdigest()


def capture_errors(record, size=None):
    errors = []
    if record.get('transport_exit') != 0 or record.get('remote_exit') != 0:
        errors.append('capture command/transport failed')
    if size is not None and record.get('bytes') != size:
        errors.append('partition size differs from actual captured bytes')
    if record.get('source_sha256') and record['source_sha256'] != record.get('sha256'):
        errors.append('device source SHA256 differs from host SHA256 (source may have changed)')
    return errors


class Adb:
    def __init__(self, executable, serial):
        self.executable, self.serial = str(executable), validate_serial(serial)
        self.log, self.session, self.token, self.temporary = [], None, None, []

    def call(self, args, timeout=120):
        command = [self.executable, '-s', self.serial] + args
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        self.log.append(dict(argv=command, exit=result.returncode,
                             stderr=result.stderr.decode(errors='replace')))
        if result.returncode:
            raise RuntimeError('ADB command failed: ' + repr(command) + ': ' + result.stderr.decode(errors='replace'))
        return result.stdout

    def check_device(self):
        validate_devices(self.call(['devices', '-l']).decode(errors='replace'), self.serial)
        if self.call(['get-serialno']).decode().strip() != self.serial:
            raise ValueError('Selected ADB transport serial changed')

    def action(self, operation):
        return self.call([operation]).decode(errors='replace')

    def text(self, command):
        # A sentinel checks remote status even with an old adbd that hides shell exit codes.
        marker = '__MIFI_RC_' + uuid.uuid4().hex + '__'
        script = command + '; rc=$?; printf "\\n' + marker + '%s\\n" "$rc"'
        data = self.call(['shell', 'sh', '-c', shell_quote(script)], timeout=3600).decode(errors='replace')
        body, separator, status = data.rpartition('\n' + marker)
        if not separator or status.strip() != '0':
            raise RuntimeError('Remote metadata command failed: ' + command + ': ' + data[-2000:])
        return body.replace('\r\n', '\n').rstrip('\r\n')

    def start_session(self):
        self.check_device()
        self.token = uuid.uuid4().hex
        self.session = '/tmp/mifi-rom-dump-' + self.token
        self.text('umask 077; mkdir ' + shell_quote(self.session) + ' && printf %s ' +
                  shell_quote(self.token) + ' > ' + shell_quote(self.session + '/owner'))
        self.temporary = [self.session + '/owner']

    def guard(self):
        self.check_device()
        if not self.session or self.session != '/tmp/mifi-rom-dump-' + self.token:
            raise ValueError('Invalid temporary session path')
        if self.text('cat ' + shell_quote(self.session + '/owner')) != self.token:
            raise ValueError('Remote session marker changed; cleanup/capture refused')

    def stream(self, command, destination):
        self.guard()
        stem = self.session + '/' + uuid.uuid4().hex
        status, errors = stem + '.status', stem + '.stderr'
        self.temporary.extend([status, errors])
        script = '( ' + command + ' ) 2>' + shell_quote(errors) + '; rc=$?; printf "%s\\n" "$rc" > ' + shell_quote(status)
        # Current Google ADB exec-out escapes each argument itself; unlike
        # adb shell, its sh -c program must be passed without another quote layer.
        argv = [self.executable, '-s', self.serial, 'exec-out', 'sh', '-c', script]
        transport_log = Path(str(destination) + '.transport-stderr.txt')
        record = dict(command=command, path=str(destination), bytes=0, sha256=None,
                      transport_exit=None, remote_exit=None)
        started = time.monotonic()
        with transport_log.open('xb') as local_errors:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=local_errors)
            timer = threading.Timer(4 * 3600, process.kill)
            timer.daemon = True
            timer.start()
            try:
                record['bytes'], record['sha256'] = copy_binary(process.stdout, destination)
                record['transport_exit'] = process.wait(timeout=30)
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
        record['elapsed_seconds'] = round(time.monotonic() - started, 3)
        try:
            record['remote_exit'] = int(self.text('cat ' + shell_quote(status)))
            record['remote_stderr'] = self.text('cat ' + shell_quote(errors))
        except Exception as exc:
            record['status_error'] = str(exc)
        Path(str(destination) + '.remote-stderr.txt').write_text(record.get('remote_stderr', ''), encoding='utf-8')
        self.log.append(record.copy())
        return record

    def cleanup(self):
        if not self.session:
            return
        self.guard()
        if any(str(PurePosixPath(p).parent) != self.session for p in self.temporary):
            raise ValueError('Cleanup path is outside the exact session directory')
        self.text('rm -f ' + ' '.join(map(shell_quote, self.temporary)) +
                  ' && rmdir ' + shell_quote(self.session))
        self.session = None


class RootPrivilege:
    def __init__(self, adb, manifest):
        self.adb, self.manifest, self.requested = adb, manifest, False

    def __enter__(self):
        self.adb.check_device()
        self.manifest['initial_uid'] = self.adb.text('id -u')
        try:
            if self.manifest['initial_uid'] != '0':
                self.requested = True
                self.manifest['adb_root_reply'] = self.adb.action('root')
                self.adb.action('wait-for-device')
                self.adb.check_device()
            if self.adb.text('id -u') != '0':
                raise RuntimeError('Ordinary adb root did not grant read access; no exploit is attempted')
        except BaseException:
            self.restore()
            raise
        return self

    def restore(self):
        if self.requested:
            self.adb.check_device()
            self.manifest['adb_unroot_reply'] = self.adb.action('unroot')
            self.adb.action('wait-for-device')
            self.adb.check_device()
            self.manifest['restored_uid'] = self.adb.text('id -u')
            if self.manifest['restored_uid'] != self.manifest['initial_uid']:
                raise RuntimeError('adbd privilege restoration did not return the initial UID')
            self.requested = False

    def __exit__(self, *exception):
        self.restore()


def resolve_adb(override=None):
    if override:
        path = Path(override).expanduser()
        found = str(path.resolve()) if path.is_file() else shutil.which(override)
        if found:
            return found
        raise FileNotFoundError('--adb executable was not found: ' + override)
    bundled = Path(__file__).resolve().parent / 'platform-tools' / 'adb.exe'
    if bundled.is_file():
        return str(bundled)
    found = shutil.which('adb')
    if not found:
        raise FileNotFoundError('ADB unavailable: retain bundled platform-tools or supply --adb PATH')
    return found


def list_transports(executable):
    # Inventory is the only unselected ADB request. All metadata is serial-bound.
    result = subprocess.run([executable, 'devices', '-l'], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise RuntimeError('ADB inventory failed: ' + result.stderr.decode(errors='replace'))
    return result.stdout.decode(errors='replace')


def select_device(executable, serial=None, yes=False, choose=input, expect_model=None, adb_factory=Adb):
    if serial is not None:
        return adb_factory(executable, validate_serial(serial)), dict(mode='explicit', selected_serial=serial)
    inventory = list_transports(executable)
    rows = device_rows(inventory)
    report = dict(mode='auto', inventory=inventory, inspected=[], excluded=[])
    eligible = []
    for row in rows:
        candidate = row[0]
        reason = None
        if len(row) < 2 or row[1] != 'device':
            reason = 'not an authorized online transport'
        elif sum(other[0] == candidate for other in rows) != 1:
            reason = 'duplicate transport serial'
        if reason:
            report['excluded'].append(dict(serial=candidate, reason=reason))
            continue
        try:
            adb = adb_factory(executable, validate_serial(candidate))
            adb.check_device()
            identity = read_identity(adb)
            validate_identity(identity, expect_model)
            eligible.append((adb, identity))
            report['inspected'].append(dict(serial=candidate, eligible=True, identity=identity))
        except Exception as exc:
            report['inspected'].append(dict(serial=candidate, eligible=False, reason=str(exc)))
    for item in report['excluded'] + [i for i in report['inspected'] if not i['eligible']]:
        print('Skipping %s: %s' % (item['serial'], item['reason']), flush=True)
    if not eligible:
        raise ValueError('No eligible stock Inseego M3000/M3100-family device found; enable/authorize ADB or use --serial')
    print('Eligible stock Inseego M3000/M3100-family devices (marketing model is not proven):')
    for number, (adb, identity) in enumerate(eligible, 1):
        print('%d. %s | %s' % (number, adb.serial, identity['model']), flush=True)
    if len(eligible) > 1:
        if yes:
            raise ValueError('Multiple eligible devices: --yes cannot choose one; provide --serial')
        selection = choose('Select the device number to back up: ').strip()
        if not selection.isdigit() or not 1 <= int(selection) <= len(eligible):
            raise ValueError('Invalid device selection; no privilege change or capture performed')
        selected = eligible[int(selection) - 1][0]
    else:
        selected = eligible[0][0]
    report['selected_serial'] = selected.serial
    print('Selected ' + selected.serial + '; starting backup.', flush=True)
    return selected, report


def package_backup(directory):
    archive = Path(str(directory) + '.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=1, allowZip64=True) as output:
        for path in sorted(directory.rglob('*')):
            if path.is_symlink():
                raise ValueError('Host output unexpectedly contains a symlink')
            if path.is_file():
                name = path.relative_to(directory.parent).as_posix()
                if '..' in PurePosixPath(name).parts or PurePosixPath(name).is_absolute():
                    raise ValueError('Unsafe ZIP member name')
                with path.open('rb') as source, output.open(name, 'w', force_zip64=True) as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
    with zipfile.ZipFile(archive) as check:
        bad = check.testzip()
        if bad:
            raise RuntimeError('ZIP integrity verification failed for ' + bad)
    digest = hashlib.sha256()
    with archive.open('rb') as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    digest = digest.hexdigest()
    Path(str(archive) + '.sha256').write_text(digest + '  ' + archive.name + '\n', encoding='ascii')
    return archive, digest


def verify_tar(path):
    with gzip.open(path, 'rb') as data:
        while data.read(1024 * 1024):
            pass  # Read to EOF to verify the gzip trailer/CRC, without extracting.
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            if PurePosixPath(member.name).is_absolute() or '..' in PurePosixPath(member.name).parts:
                raise ValueError('Unsafe logical archive member: ' + member.name)


def acquire(adb, directory, manifest):
    raw, logical, metadata = (directory / name for name in ('raw', 'logical', 'metadata'))
    for path in (raw, logical, metadata):
        path.mkdir()
    adb.start_session()
    try:
        saved = {}
        for name, command in {'proc-mtd.txt': 'cat /proc/mtd', 'proc-mounts.txt': 'cat /proc/mounts',
                              'proc-partitions.txt': 'cat /proc/partitions', 'uname.txt': 'uname -a',
                              'ubinfo.txt': 'ubinfo -a', 'tar-help.txt': 'tar --help'}.items():
            try:
                saved[name] = adb.text(command)
                (metadata / name).write_text(saved[name] + '\n', encoding='utf-8')
            except Exception as exc:
                manifest['errors' if name in ('proc-mtd.txt', 'proc-mounts.txt', 'tar-help.txt') else 'warnings'].append(name + ': ' + str(exc))
        try:
            if adb.text('test -r /proc/config.gz && echo yes || true') == 'yes':
                config = adb.stream('cat /proc/config.gz', metadata / 'kernel-config.gz')
                manifest['kernel_config'] = config
                manifest['errors'].extend('kernel config: ' + e for e in capture_errors(config))
            else:
                manifest['warnings'].append('/proc/config.gz unavailable')
        except Exception as exc:
            manifest['warnings'].append('kernel config: ' + str(exc))
        hasher = adb.text('command -v sha256sum || true').strip()
        if not hasher:
            manifest['warnings'].append('Device SHA256 utility unavailable; raw source hashes cannot be cross-checked')
        partitions = parse_mtd(saved.get('proc-mtd.txt', ''))
        for partition in partitions:
            manifest['partitions'].append(partition)
            index = partition['index']
            print('Reading mtd%d (%s, %d bytes)' % (index, partition['name'], partition['size']), flush=True)
            try:
                attributes = {}
                for attribute in ('size', 'erasesize', 'writesize', 'oobsize', 'bad_blocks',
                                  'bbt_blocks', 'ecc_strength', 'ecc_step_size', 'corrected_bits', 'ecc_failures', 'type'):
                    attributes[attribute] = adb.text('cat /sys/class/mtd/mtd%d/%s 2>/dev/null || true' % (index, attribute)) or None
                partition['sysfs'] = attributes
                if attributes['size'] is not None and int(attributes['size']) != partition['size']:
                    raise ValueError('/proc/mtd and sysfs partition sizes disagree')
                record = adb.stream('dd if=/dev/mtd%d bs=1048576' % index, raw / ('mtd%d.bin' % index))
                partition.update(record)
                if hasher:
                    source_hash = adb.text('sha256sum /dev/mtd%d' % index).split()[0]
                    if not re.fullmatch('[0-9a-fA-F]{64}', source_hash):
                        raise ValueError('Device returned an invalid SHA256')
                    partition['source_sha256'] = source_hash.lower()
                manifest['errors'].extend('mtd%d: %s' % (index, e) for e in capture_errors(partition, partition['size']))
            except Exception as exc:
                manifest['errors'].append('mtd%d: %s' % (index, exc))
        mounts = parse_mounts(saved.get('proc-mounts.txt', ''))
        for number, mount in enumerate(mounts):
            if not persistent_mount(mount):
                continue
            manifest['filesystems'].append(mount)
            print('Archiving mounted filesystem ' + mount['target'], flush=True)
            try:
                target = shell_quote(mount['target'])
                mount['target_type'] = adb.text('if [ -d ' + target + ' ]; then printf directory; '
                                                'elif [ -f ' + target + ' ]; then printf file; else printf unsupported; fi')
                if mount['target_type'] not in ('directory', 'file'):
                    raise ValueError('Mounted target is not a directory or regular file')
                mount['archive_base'] = (str(PurePosixPath(mount['target']).parent)
                                         if mount['target_type'] == 'file' else mount['target'])
                mount['archive_member'] = ('./' + PurePosixPath(mount['target']).name
                                           if mount['target_type'] == 'file' else '.')
                command = tar_command(mount, mounts, saved.get('tar-help.txt', ''))
                record = adb.stream(command, logical / ('fs%02d.tar.gz' % number))
                mount.update(record)
                manifest['errors'].extend(mount['target'] + ': ' + e for e in capture_errors(record))
                verify_tar(Path(record['path']))
            except Exception as exc:
                manifest['errors'].append(mount['target'] + ': ' + str(exc))
        if not manifest['filesystems']:
            manifest['errors'].append('No mounted persistent filesystem was captured')
        after = adb.text('cat /proc/mounts')
        (metadata / 'proc-mounts-after.txt').write_text(after + '\n', encoding='utf-8')
        if after != saved.get('proc-mounts.txt'):
            manifest['errors'].append('Mount topology changed during capture')
    finally:
        adb.cleanup()


def run_backup(adb, output, expect_model=None, yes=False, confirm=input, discovery=None):
    adb.check_device()
    identity = read_identity(adb)
    validate_identity(identity, expect_model)
    print(json.dumps(identity, indent=2), flush=True)
    print('Board identity does not establish a marketing M3000/M3100 model. Live files can change.')
    if not yes and confirm('Type the exact serial %s to back up this device: ' % adb.serial).strip() != adb.serial:
        raise ValueError('Confirmation did not match; no privilege change or capture performed')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    safe_serial = re.sub(r'[^A-Za-z0-9_.-]', '_', adb.serial)
    directory = Path(output).resolve() / (safe_serial + '-' + timestamp + '-' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    manifest = dict(format_version=1, complete=False, identity=identity, started_utc=timestamp,
                    errors=[], warnings=[], partitions=[], filesystems=[],
                    limitations=['ECC-corrected MTD data only; no NAND OOB or bit-exact chip dump',
                                 'Live mounted filesystems: no snapshot or freeze; cross-filesystem consistency is not guaranteed',
                                 'Symlinks retained; nested mounts and volatile paths excluded',
                                 'Logical archives are complementary file backups, not rebuilt flashable UBIFS images',
                                 'Restore is untested; retain original persist, calibration, identity and TrustZone data'])
    if discovery is not None:
        manifest['discovery'] = discovery
    (directory / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n', encoding='utf-8')
    try:
        with RootPrivilege(adb, manifest):
            after_root = read_identity(adb)
            manifest['identity_after_root'] = after_root
            validate_identity(after_root, expect_model)
            stable_keys = ('model', 'compatible', 'soc_machine', 'soc_family',
                           'board_serial', 'soc_serial', 'osrelease')
            changed = [key for key in stable_keys if identity.get(key) and identity[key] != after_root.get(key)]
            if changed:
                raise ValueError('Identity changed after adb root: ' + ', '.join(changed))
            acquire(adb, directory, manifest)
    except (Exception, KeyboardInterrupt) as exc:
        manifest['errors'].append(type(exc).__name__ + ': ' + str(exc))
    manifest['complete'] = not manifest['errors'] and bool(manifest['partitions']) and bool(manifest['filesystems'])
    manifest['finished_utc'] = datetime.now(timezone.utc).isoformat()
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (directory / 'commands.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in adb.log), encoding='utf-8')
    archive, digest = package_backup(directory)
    print(('COMPLETE capture' if manifest['complete'] else 'INCOMPLETE backup') + ': ' + str(archive))
    print('ZIP SHA256: ' + digest)
    for error in manifest['errors']:
        print('ERROR: ' + error, file=sys.stderr)
    return archive, 0 if manifest['complete'] else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', help='Override auto-detection with the exact intended ADB serial')
    parser.add_argument('--adb', help='ADB override; default bundled platform-tools/adb.exe, then PATH')
    parser.add_argument('--output', type=Path, default=Path.cwd() / 'backups', help='Parent folder for a unique backup directory and ZIP')
    parser.add_argument('--expect-model', help='Require this text in the reported board model; does not infer marketing model')
    parser.add_argument('--yes', action='store_true', help='Skip manual serial confirmation; ambiguous auto-detection still fails')
    args = parser.parse_args(argv)
    try:
        adb, discovery = select_device(resolve_adb(args.adb), serial=args.serial,
                                       yes=args.yes, expect_model=args.expect_model)
        return run_backup(adb, args.output, args.expect_model, yes=args.yes or args.serial is None,
                          discovery=discovery)[1]
    except (Exception, KeyboardInterrupt) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
