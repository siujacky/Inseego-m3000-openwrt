#!/usr/bin/env python3
"""Guarded RAM-only transaction. Standard library; never extract untrusted tar paths."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile
import time

MODULES = ('__init__.py', '__main__.py', 'api.py', 'catalog.py', 'controller.py',
           'font.py', 'font9x16.bin', 'i18n.py', 'input.py', 'keyboard.py', 'lcd.py',
           'render.py', 'stock_coverage.json')
SOURCES = {'m3100_touch/' + name: ('/opt/m3100-touchscreen/m3100_touch/' + name, 0o644)
           for name in MODULES}
SOURCES.update({
    'runtime/hardware.py': ('/usr/libexec/m3100-hardware.py', 0o755),
    'runtime/m3100-touchscreen.init': ('/etc/init.d/m3100-touchscreen', 0o755),
    'backend/m3100ui/touchscreen_api.py': ('/opt/m3100-webui/server/m3100ui/touchscreen_api.py', 0o644),
    'backend/m3100ui/services/__init__.py': ('/opt/m3100-webui/server/m3100ui/services/__init__.py', 0o644),
    'tools/install.py': (None, 0o644), 'tools/install.sh': (None, 0o755),
    'tools/rollback.sh': (None, 0o755),
})
GUARD_PATHS = {'/usr/libexec/m3100-hardware.py', '/etc/init.d/m3100-hardware',
               '/etc/init.d/m3100-webui', '/opt/m3100-webui/server/m3100ui/services/__init__.py'}
SERVICES = ('m3100-touchscreen', 'm3100-hardware', 'm3100-webui')
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    return digest(path.read_bytes()) if path.exists() else None


def validate_archive(path, expected_sha256=None):
    """Read only whitelisted regular members; verify exact closed manifest."""
    if Path(path).stat().st_size > MAX_TOTAL:
        raise ValueError('archive exceeds size bound')
    if expected_sha256 is not None and digest(Path(path).read_bytes()) != expected_sha256:
        raise ValueError('archive SHA256 mismatch')
    data = {}
    total = 0
    try:
        with tarfile.open(path, 'r:*') as archive:
            for item in archive:
                name = item.name
                if (name not in SOURCES and name != 'manifest.json') or name in data:
                    raise ValueError('unexpected or duplicate archive entry')
                if (not item.isfile() or item.pax_headers or '\\' in name or
                        str(PurePosixPath(name)) != name or name.startswith('/') or
                        '..' in PurePosixPath(name).parts or item.size > MAX_FILE or item.size < 0):
                    raise ValueError('unsafe archive entry')
                total += item.size
                if total > MAX_TOTAL:
                    raise ValueError('expanded archive exceeds size bound')
                data[name] = archive.extractfile(item).read(MAX_FILE + 1)
                if len(data[name]) != item.size:
                    raise ValueError('incomplete archive entry')
    except (tarfile.TarError, EOFError) as error:
        raise ValueError('invalid archive') from error
    if set(data) != set(SOURCES) | {'manifest.json'}:
        raise ValueError('incomplete package')
    try:
        manifest = json.loads(data.pop('manifest.json'))
        entries = manifest['files']
        if manifest['version'] != 1 or type(entries) is not list or len(entries) != len(SOURCES):
            raise ValueError('invalid manifest')
        seen = set()
        for entry in entries:
            name = entry['source']
            if name in seen or name not in SOURCES:
                raise ValueError('duplicate or unknown manifest source')
            seen.add(name)
            destination, mode = SOURCES[name]
            if (entry['destination'] != destination or entry['mode'] != mode or
                    entry['size'] != len(data[name]) or entry['sha256'] != digest(data[name])):
                raise ValueError('manifest entry mismatch')
        return manifest, data
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('invalid manifest') from error


def atomic(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode) if hasattr(os, 'fchmod') else None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LocalServices:
    """Only the three reviewed userspace services cross this boundary."""
    def __init__(self, root=Path('/'), status_timeout=3, action_timeout=20, health_timeout=20):
        self.root = Path(root)
        self.status_timeout = status_timeout
        self.action_timeout = action_timeout
        self.health_timeout = health_timeout

    def running(self, service, deadline=None):
        if service not in SERVICES:
            raise ValueError('unapproved service')
        timeout = self.status_timeout
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise RuntimeError('service health deadline expired before status probe')
        try:
            result = subprocess.run([str(self.root / 'etc/init.d' / service), 'running'],
                                    timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError('service status timed out: ' + service) from error
        return result.returncode == 0

    def action(self, service, verb):
        if service not in SERVICES or verb not in ('start', 'stop'):
            raise ValueError('unapproved service action')
        path = self.root / 'etc/init.d' / service
        if not path.exists() and verb == 'stop':
            return
        try:
            subprocess.run([str(path), verb], check=True, timeout=self.action_timeout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError('service action timed out: ' + service + ' ' + verb) from error

    def healthy(self, boot_id):
        deadline = time.monotonic() + self.health_timeout
        while time.monotonic() < deadline:
            try:
                path = self.root / 'run/m3100-touchscreen.json'
                if path.stat().st_size > 16384:
                    raise ValueError('status too large')
                value = json.loads(path.read_text())
                now = time.monotonic()
                valid = value.get('boot_id') == boot_id and value.get('render_ok') is True
                valid = valid and type(value.get('pid')) is int and value['pid'] > 0
                for key in ('monotonic', 'last_frame_monotonic'):
                    item = value.get(key)
                    valid = valid and type(item) in (int, float) and math.isfinite(item) and 0 <= now - item <= 15
                if valid:
                    os.kill(value['pid'], 0)
                    import fcntl
                    with open(self.root / 'run/m3100-display.lock', 'a+b') as lock:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            if (all(self.running(service, deadline=deadline) for service in SERVICES) and
                                    time.monotonic() < deadline):
                                return
            except (OSError, ValueError, TypeError):
                pass
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        raise RuntimeError('touchscreen did not publish a fresh owned successful frame')


class Transaction:
    def __init__(self, root=Path('/'), services=None):
        self.root = Path(root).resolve()
        self.services = services or LocalServices()

    def runtime_destination(self, absolute):
        """Allow only the observed /run -> /var/run, /var -> tmp state alias."""
        if not absolute.startswith('/run/') or not (self.root / 'run').is_symlink():
            return absolute
        if not (absolute == '/run/m3100-touchscreen-install.lock' or
                absolute.startswith('/run/m3100-touchscreen-backup/')):
            raise ValueError('unapproved runtime state path')
        if (os.readlink(self.root / 'run') != '/var/run' or
                not (self.root / 'var').is_symlink() or
                os.readlink(self.root / 'var') != 'tmp'):
            raise ValueError('unexpected runtime state alias chain')
        # Return the verified canonical destination. Never mkdir/replace /run
        # or /var themselves, and never follow arbitrary destination symlinks.
        return '/tmp/run/' + absolute[len('/run/'):]

    def path(self, absolute):
        if not absolute.startswith('/') or '..' in PurePosixPath(absolute).parts or '\\' in absolute:
            raise ValueError('invalid destination')
        absolute = self.runtime_destination(absolute)
        target = self.root / absolute.lstrip('/')
        current = target
        while current != self.root:
            if current.is_symlink():
                raise ValueError('symlink in destination path')
            current = current.parent
        if self.root != target and self.root not in target.resolve().parents:
            raise ValueError('destination escapes root')
        if target.exists() and not target.is_file():
            raise ValueError('destination is not a regular file')
        return target

    def receipt_path(self, boot_id):
        if not re.fullmatch('[A-Za-z0-9-]{1,64}', boot_id):
            raise ValueError('invalid boot ID')
        return self.path('/run/m3100-touchscreen-backup/' + boot_id + '/receipt.json')

    def probe(self, absolute):
        # /proc/mounts -> self/mounts is a normal read-only procfs alias.
        # Destination paths continue to reject every symlink component.
        if absolute not in ('/proc/sys/kernel/random/boot_id', '/tmp/sysinfo/board_name',
                            '/tmp/sysinfo/model', '/proc/mtd', '/proc/mounts'):
            raise ValueError('unapproved identity probe')
        path = self.root / absolute.lstrip('/')
        if self.root not in path.resolve().parents:
            raise ValueError('probe escapes root')
        return path.read_text()

    def identity(self, guard):
        probes = {'boot_id': '/proc/sys/kernel/random/boot_id',
                  'board_name': '/tmp/sysinfo/board_name', 'model': '/tmp/sysinfo/model'}
        for key, path in probes.items():
            if self.probe(path).strip() != guard.get(key):
                raise ValueError('identity changed: ' + key)
        if self.probe('/proc/mtd') != guard.get('proc_mtd') or guard['proc_mtd'].strip() != 'dev:    size   erasesize  name':
            raise ValueError('MTD must contain only the captured header')
        mounts = [line.split() for line in self.probe('/proc/mounts').splitlines()]
        roots = [' '.join(row) for row in mounts if len(row) == 6 and row[1] == '/']
        if roots != [guard.get('root_mount')] or not roots[0].split()[2] == 'tmpfs':
            raise ValueError('root mount is not captured tmpfs')
        targets = [destination for destination, _ in SOURCES.values() if destination] + [
            '/run/m3100-touchscreen-backup/' + guard['boot_id'] + '/receipt.json']
        for target in targets:
            target = self.runtime_destination(target)
            applicable = [row for row in mounts if len(row) >= 3 and
                          (row[1] == '/' or target == row[1] or target.startswith(row[1].rstrip('/') + '/'))]
            if not applicable or max(applicable, key=lambda row: len(row[1]))[2] != 'tmpfs':
                raise ValueError('target mount is not tmpfs')
        self.receipt_path(guard['boot_id'])

    def save(self, receipt):
        path = self.receipt_path(receipt['boot_id'])
        atomic(path, (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode(), 0o600)
        os.chmod(path.parent, 0o700)

    def read_receipt(self, boot_id):
        path = self.receipt_path(boot_id)
        if not path.exists():
            return None
        if path.stat().st_size > 128 * 1024:
            raise ValueError('receipt too large')
        return json.loads(path.read_text())

    def verify_installed(self, receipt):
        for entry in receipt['entries']:
            if file_hash(self.path(entry['destination'])) != entry['new_hash']:
                raise ValueError('installed file changed: ' + entry['destination'])

    def verify_sentinels(self, guard):
        replaced = {destination for destination, _ in SOURCES.values() if destination}
        for value in guard['original_files'].values():
            name = value['device_path']
            if name not in replaced and file_hash(self.path(name)) != value['sha256']:
                raise ValueError('original service changed: ' + name)

    @contextmanager
    def transaction_lock(self, guard):
        self.identity(guard)
        path = self.path('/run/m3100-touchscreen-install.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a+b') as stream:
            if os.name == 'posix':
                import fcntl
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError('another transaction owns the install lock') from error
            # Non-POSIX hosts exercise filesystem transactions only. Device CLI
            # requires geteuid and therefore cannot execute on those hosts.
            yield

    def install(self, bundle, guard, expected_sha256):
        with self.transaction_lock(guard):
            return self._install(bundle, guard, expected_sha256)

    def _install(self, bundle, guard, expected_sha256):
        if not re.fullmatch('[0-9a-f]{64}', expected_sha256 or ''):
            raise ValueError('expected archive SHA256 is required')
        manifest, payloads = validate_archive(bundle, expected_sha256)
        self.identity(guard)
        self.verify_sentinels(guard)
        previous = self.read_receipt(guard['boot_id'])
        if previous and previous['state'] == 'installed':
            if previous['bundle_sha256'] != expected_sha256 or previous['guard'] != guard:
                raise ValueError('different package active; rollback first')
            self.verify_installed(previous)
            return previous
        if previous and previous['state'] != 'rolled_back':
            raise ValueError('unfinished transaction; rollback first')
        originals = {value['device_path']: value['sha256'] for value in guard['original_files'].values()}
        if set(originals) != GUARD_PATHS:
            raise ValueError('runtime original hash guard incomplete')
        for name, expected in originals.items():
            if file_hash(self.path(name)) != expected:
                raise ValueError('original hash changed: ' + name)
        entries = []
        for item in manifest['files']:
            name = item['destination']
            if name is None:
                continue
            target = self.path(name)
            if name not in originals and target.exists():
                raise ValueError('new destination already exists: ' + name)
            old = target.read_bytes() if target.exists() else None
            entries.append(dict(destination=name, source=item['source'], mode=item['mode'],
                                new_hash=item['sha256'], old_hash=digest(old) if old is not None else None,
                                old_mode=stat.S_IMODE(target.stat().st_mode) if old is not None else None,
                                backup='%03d.bin' % len(entries) if old is not None else None))
        # Backups and receipt are durably published before stopping any service.
        for entry in entries:
            if entry['backup']:
                atomic(self.path(str(PurePosixPath('/run/m3100-touchscreen-backup') / guard['boot_id'] / entry['backup'])),
                       self.path(entry['destination']).read_bytes(), 0o600)
        original_backups = []
        for index, (name, expected) in enumerate(sorted(originals.items())):
            backup = 'original-%03d.bin' % index
            data = self.path(name).read_bytes()
            if digest(data) != expected:
                raise ValueError('original changed while backing up: ' + name)
            atomic(self.path('/run/m3100-touchscreen-backup/' + guard['boot_id'] + '/' + backup), data, 0o600)
            original_backups.append(dict(destination=name, sha256=expected, backup=backup))
        states = {name: self.services.running(name) if self.path('/etc/init.d/' + name).exists() else False
                  for name in SERVICES}
        receipt = dict(version=1, boot_id=guard['boot_id'], bundle_sha256=expected_sha256,
                       guard=guard, state='prepared', entries=entries, services=states,
                       original_backups=original_backups)
        self.save(receipt)
        try:
            for name in SERVICES:
                self.services.action(name, 'stop')
            receipt['state'] = 'installing'
            self.save(receipt)
            for entry in entries:
                atomic(self.path(entry['destination']), payloads[entry['source']], entry['mode'])
            self.verify_installed(receipt)
            for name in reversed(SERVICES):
                self.services.action(name, 'start')
            self.services.healthy(guard['boot_id'])
            receipt['state'] = 'installed'
            self.save(receipt)
            return receipt
        except BaseException:
            self._rollback(guard)
            raise

    def rollback(self, guard):
        with self.transaction_lock(guard):
            return self._rollback(guard)

    def _rollback(self, guard):
        self.identity(guard)
        self.verify_sentinels(guard)
        receipt = self.read_receipt(guard['boot_id'])
        if not receipt or receipt.get('guard') != guard:
            raise ValueError('matching boot-specific receipt required')
        if receipt['state'] == 'rolled_back':
            return receipt
        allowed = {destination for destination, _ in SOURCES.values() if destination}
        if set(entry['destination'] for entry in receipt['entries']) != allowed:
            raise ValueError('invalid receipt destinations')
        backups = {}
        # Validate ALL bytes before changing files or stopping services.
        for index, entry in enumerate(receipt['entries']):
            target = self.path(entry['destination'])
            if file_hash(target) not in (entry['old_hash'], entry['new_hash']):
                raise ValueError('rollback target drift: ' + entry['destination'])
            if entry['old_hash'] is not None:
                if entry['backup'] != '%03d.bin' % index:
                    raise ValueError('invalid backup path')
                path = self.path('/run/m3100-touchscreen-backup/' + guard['boot_id'] + '/' + entry['backup'])
                data = path.read_bytes()
                if digest(data) != entry['old_hash']:
                    raise ValueError('backup hash mismatch')
                backups[entry['destination']] = data
        for name in SERVICES:
            self.services.action(name, 'stop')
        for entry in receipt['entries']:
            path = self.path(entry['destination'])
            if entry['old_hash'] is None:
                if path.exists():
                    path.unlink()
            else:
                atomic(path, backups[entry['destination']], entry['old_mode'])
        for name in reversed(SERVICES):
            if receipt['services'][name]:
                self.services.action(name, 'start')
        receipt['state'] = 'rolled_back'
        self.save(receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('install', 'rollback', 'validate'))
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--guard', type=Path)
    args = parser.parse_args()
    if args.operation == 'validate':
        validate_archive(args.bundle, args.sha256)
        print('archive validated')
        return
    if os.geteuid() != 0:
        parser.error('root is required for target transaction')
    guard = json.loads(args.guard.read_text())
    transaction = Transaction()
    result = transaction.install(args.bundle, guard, args.sha256) if args.operation == 'install' else transaction.rollback(guard)
    print(json.dumps({'state': result['state'], 'boot_id': result['boot_id'], 'bundle_sha256': result['bundle_sha256']}))


if __name__ == '__main__':
    main()
