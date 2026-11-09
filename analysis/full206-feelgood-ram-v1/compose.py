"""Derive the reviewed WebUI/touch RAM payload from immutable R15 inputs."""
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import lzma
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT / 'analysis/full206-integrated/r15'
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'analysis/wlan148-integration'), str(ROOT / 'analysis/full206-integrated/r14')]
import cpio148
from cpio_patch import replace_payloads
from firmware_ramdisk import inspect_archive, newc_archive, safe_name

sha = lambda data: hashlib.sha256(data).hexdigest()
PINS = {
    'artifacts/openwrt-m3100-full206-r15-scm-status-ram.bin': '81b20eacf658c3ec0e54a2744026d9d003360f2d8fdd1f2d34b6e121f6ee14a4',
    'artifacts/m3100-feelgood-web-overlay-2026.11.08.1.tar.gz': 'd48cd9a51cdd6fe844560cc5ad3d086f40606af8a4773cf5ba4c956c13807d9b',
    'analysis/full206-touch-ui/m3100-touchscreen-candidate-v6.tar.gz': 'e805d348067a8d7f1da5bafeac84c4ff6f10aae203a6f2764d6d5b1a24f08a17',
}


def metadata(member):
    return (member.name, member.mode, member.uid, member.gid, member.type, member.linkname,
            member.mtime, member.uname, member.gname, member.devmajor, member.devminor, member.pax_headers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-password-file', type=Path, required=True)
    args = parser.parse_args()
    if (HERE / 'composition.json').exists():
        raise RuntimeError('Preserve the existing composition; use a distinct revision for changes')
    inputs = {}
    for relative, expected in PINS.items():
        data = (ROOT / relative).read_bytes()
        if sha(data) != expected:
            raise RuntimeError('Input pin mismatch: ' + relative)
        inputs[relative] = expected
    composition = json.loads((BASE / 'composition.json').read_text())
    bootstrap = (BASE / 'full206-lcd.cpio').read_bytes()
    if sha(bootstrap) != composition['bootstrap_sha256']:
        raise RuntimeError('R15 bootstrap mismatch')
    original = {e['name']: e for e in cpio148.parse(bootstrap)}
    userspace = (BASE / 'userspace.tar.xz').read_bytes()
    archive = (BASE / 'payload.cpio.xz').read_bytes()
    if sha(archive) != composition['payload_sha256']:
        raise RuntimeError('R15 payload mismatch')
    firmware = inspect_archive(lzma.decompress(archive))
    if firmware['full206/userspace.tar.xz'] != userspace:
        raise RuntimeError('Userspace is not the original R15 payload')
    replacements = {}
    with tarfile.open(ROOT / 'artifacts/m3100-feelgood-web-overlay-2026.11.08.1.tar.gz', 'r:gz') as overlay:
        for member in overlay:
            safe_name(member.name)
            if not member.isfile() or not (member.name.startswith('web/') or member.name == 'server/m3100ui/services/ui_pages.py'):
                raise RuntimeError('Unexpected WebUI overlay member: ' + member.name)
            name = 'opt/m3100-webui/' + member.name
            if name in replacements:
                raise RuntimeError('Duplicate overlay path')
            replacements[name] = (overlay.extractfile(member).read(), member.mode)
    installer_path = ROOT / 'touchscreen/tools/install.py'
    spec = importlib.util.spec_from_file_location('accepted_touch_installer', installer_path)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    touch_path = ROOT / 'analysis/full206-touch-ui/m3100-touchscreen-candidate-v6.tar.gz'
    manifest, touch_payloads = installer.validate_archive(touch_path, PINS[str(touch_path.relative_to(ROOT)).replace('\\', '/')])
    for row in manifest['files']:
        if row['destination'] is None:
            continue
        name = safe_name(row['destination'].lstrip('/'))
        if name in replacements:
            raise RuntimeError('Browser/touch payload conflict')
        replacements[name] = (touch_payloads[row['source']], row['mode'])
    password = args.admin_password_file.read_bytes().rstrip(b'\r\n')
    if not password or len(password) > 1024 or any(c in password for c in (b'\0', b'\n', b'\r')):
        raise RuntimeError('Invalid private RAM admin input')
    replacements['etc/full206/admin-password'] = (password + b'\n', 0o600)
    del password
    build_info = {'version': '2026.11.08.1', 'base': 'R15', 'touchscreen': 'v6', 'ram_only': True,
                  'mock': False, 'stock_partition_writes': False}
    replacements['etc/full206/ui-build.json'] = ((json.dumps(build_info, sort_keys=True) + '\n').encode(), 0o644)
    members, data_by_name = {}, {}
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(userspace)), mode='r:') as source:
        for member in source:
            safe_name(member.name)
            if member.name in members or not (member.isfile() or member.isdir() or member.issym()):
                raise RuntimeError('Invalid baseline tar member')
            members[member.name] = copy.copy(member)
            data_by_name[member.name] = source.extractfile(member).read() if member.isfile() else None
    baseline_meta = {n: metadata(m) for n, m in members.items()}
    baseline_data = dict(data_by_name)
    for name, (data, mode) in replacements.items():
        safe_name(name)
        for parent in PurePosixPath(name).parents:
            if str(parent) == '.':
                continue
            for registry in (members, original):
                entry = registry.get(str(parent))
                if entry is not None and (entry.issym() if isinstance(entry, tarfile.TarInfo) else stat.S_ISLNK(entry['fields'][1])):
                    raise RuntimeError('Overlay parent is a symlink: ' + str(parent))
        if name in members:
            member = copy.copy(members[name])
            if not member.isfile() or member.mode != mode:
                raise RuntimeError('Unexpected replacement type/mode: ' + name)
        else:
            member = tarfile.TarInfo(name)
            member.mode = mode
            member.uid = member.gid = 0
            member.mtime = 0
        member.size = len(data)
        members[name], data_by_name[name] = member, data
    parents = {str(p) for name in replacements for p in PurePosixPath(name).parents if str(p) != '.'}
    for name in sorted(parents, key=lambda n: (n.count('/'), n)):
        if name not in members and name not in original:
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            member.mode = 0o755
            members[name], data_by_name[name] = member, None
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:', format=tarfile.PAX_FORMAT) as target:
        for name, member in members.items():
            target.addfile(member, io.BytesIO(data_by_name[name]) if member.isfile() else None)
    new_userspace = lzma.compress(output.getvalue(), format=lzma.FORMAT_XZ, preset=6)
    preserved = 0
    for name, meta in baseline_meta.items():
        if name not in replacements:
            if metadata(members[name]) != meta or data_by_name[name] != baseline_data[name]:
                raise RuntimeError('Unscoped userspace change: ' + name)
            preserved += 1
    old_firmware = dict(firmware)
    firmware['full206/userspace.tar.xz'] = new_userspace
    new_payload = lzma.compress(newc_archive(firmware), format=lzma.FORMAT_XZ, preset=6)
    checked_firmware = inspect_archive(lzma.decompress(new_payload))
    firmware_preserved = sum(checked_firmware[name] == data for name, data in old_firmware.items() if name != 'full206/userspace.tar.xz')
    if firmware_preserved != 545:
        raise RuntimeError('Original firmware changed')
    worker_name = 'usr/libexec/full206-ui-ready'
    worker = original[worker_name]['payload']
    anchor = b'rc=$?\n'
    if worker.count(anchor) != 1 or b'm3100-touchscreen start' in worker:
        raise RuntimeError('Unexpected loader startup shape')
    worker = worker.replace(anchor, anchor + b'if [ "$rc" -eq 0 ]; then\n /etc/init.d/m3100-touchscreen start >>"$state/start.log" 2>&1\n touch_rc=$?\n printf \'%s\\n\' "$touch_rc" >"$state/touchscreen-exit-status"\nfi\n')
    bootstrap_replacements = {
        worker_name: worker,
        'etc/m3100-payload.sha256': sha(new_payload).encode() + b'  /tmp/m3100-firmware.cpio.xz\n',
        'etc/full206-userspace.sha256': sha(new_userspace).encode() + b'  full206/userspace.tar.xz\n',
    }
    new_bootstrap = replace_payloads(bootstrap, bootstrap_replacements)
    for name, data in [('userspace.tar.xz', new_userspace), ('payload.cpio.xz', new_payload), ('bootstrap.cpio', new_bootstrap)]:
        with (HERE / name).open('xb') as stream:
            stream.write(data)
    touched = [name for name in replacements if name in baseline_meta and data_by_name[name] != baseline_data[name]]
    report = {'webui_version': '2026.11.08.1', 'touchscreen_version': 'v6', 'inputs': inputs,
        'baseline_bootstrap_sha256': sha(bootstrap), 'bootstrap_sha256': sha(new_bootstrap),
        'userspace_sha256': sha(new_userspace), 'payload_sha256': sha(new_payload),
        'bootstrap_changed_entries': sorted(bootstrap_replacements), 'userspace_changed_entries': sorted(touched),
        'userspace_added_entries': sorted(set(members) - set(baseline_meta)),
        'unchanged_userspace_entries_data_and_metadata': preserved,
        'original_firmware_files_byte_identical': firmware_preserved,
        'accepted_touchscreen_target_count': sum(row['destination'] is not None for row in manifest['files']),
        'browser_overlay_source_hashes_verified': True, 'kernel_source_changes': [],
        'private_admin_input': str(args.admin_password_file), 'admin_seed_mode': '0600 root',
        'hardware_boot_tested': False, 'stock_partition_writes': False}
    (HERE / 'composition.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('bootstrap_sha256', 'userspace_sha256', 'payload_sha256', 'original_firmware_files_byte_identical', 'accepted_touchscreen_target_count')}, indent=2))


if __name__ == '__main__':
    main()
