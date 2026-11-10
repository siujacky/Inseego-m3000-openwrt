"""Derive a public, USB-first RAM test image without personal provisioning.

Preserve the tested private image and all firmware/kernel code. This program
only reads host files; it never contacts a device.
"""
import copy
import hashlib
import io
import json
import lzma
from pathlib import Path
import sys
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIVATE = ROOT / 'analysis/full206-feelgood-ram-v1'
BASE = ROOT / 'analysis/full206-integrated/r15'
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'analysis/wlan148-integration'),
               str(ROOT / 'analysis/full206-integrated/r14')]
import cpio148
from cpio_patch import replace_payloads
from firmware_ramdisk import inspect_archive, newc_archive, safe_name

sha = lambda b: hashlib.sha256(b).hexdigest()


def metadata(m):
    return (m.name, m.mode, m.uid, m.gid, m.type, m.linkname, m.mtime,
            m.uname, m.gname, m.devmajor, m.devminor, m.pax_headers)


def main():
    if (HERE / 'composition.json').exists():
        raise RuntimeError('Preserve the existing public revision')
    original_report = json.loads((PRIVATE / 'composition.json').read_text())
    old = {}
    for name, key in [('bootstrap.cpio', 'bootstrap_sha256'),
                      ('userspace.tar.xz', 'userspace_sha256'),
                      ('payload.cpio.xz', 'payload_sha256')]:
        old[name] = (PRIVATE / name).read_bytes()
        if sha(old[name]) != original_report[key]:
            raise RuntimeError('Private source pin changed: ' + name)
    bootstrap = {e['name']: e for e in cpio148.parse(old['bootstrap.cpio'])}
    base_bootstrap = {e['name']: e for e in cpio148.parse((BASE / 'full206-lcd.cpio').read_bytes())}
    members, contents = {}, {}
    with tarfile.open(fileobj=io.BytesIO(old['userspace.tar.xz'])) as source:
        for m in source:
            safe_name(m.name)
            if m.name in members or not (m.isfile() or m.isdir() or m.issym()):
                raise RuntimeError('Unexpected source tar member')
            members[m.name] = copy.copy(m)
            contents[m.name] = source.extractfile(m).read() if m.isfile() else None
    private_wifi = contents['etc/full206/wifi-password'].strip()
    private_key = bootstrap['etc/dropbear/authorized_keys']['payload'].strip().split()[1]
    old_meta = {n: metadata(m) for n, m in members.items()}
    old_contents = dict(contents)
    replacements = {
        'etc/full206/admin-password': b'12345\n',
        'etc/full206/wifi-password': b'M3100-ram-test\n',
        'etc/full206/modem-autostart': b'0\n',
        'etc/full206/ui-build.json': (json.dumps({
            'version': '2026.11.09.1-public', 'base': 'R15', 'touchscreen': 'v6',
            'ram_only': True, 'mock': False, 'stock_partition_writes': False,
            'public_release': True, 'modem_autostart': False, 'wifi_ap_default': False,
        }, sort_keys=True) + '\n').encode(),
    }
    for n, data in replacements.items():
        m = copy.copy(members[n]) if n in members else tarfile.TarInfo(n)
        m.mode = 0o644 if n.endswith('ui-build.json') else 0o600
        m.uid = m.gid = 0
        m.size = len(data)
        members[n], contents[n] = m, data
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:', format=tarfile.PAX_FORMAT) as target:
        for n, m in members.items():
            target.addfile(m, io.BytesIO(contents[n]) if m.isfile() else None)
    new_userspace = lzma.compress(out.getvalue(), preset=6)
    for n, m in old_meta.items():
        if n not in replacements and (metadata(members[n]) != m or contents[n] != old_contents[n]):
            raise RuntimeError('Unscoped userspace change: ' + n)
    firmware = inspect_archive(lzma.decompress(old['payload.cpio.xz']))
    if firmware['full206/userspace.tar.xz'] != old['userspace.tar.xz']:
        raise RuntimeError('Private payload association mismatch')
    firmware['full206/userspace.tar.xz'] = new_userspace
    new_payload = lzma.compress(newc_archive(firmware), preset=6)
    usb = bootstrap['etc/init.d/m3100-usb']['payload']
    for before, after in [(b'76fe1623-ram', b'm3100-public-ram'),
                           (b'02:76:fe:16:23:01', b'02:00:00:31:00:01'),
                           (b'02:76:fe:16:23:02', b'02:00:00:31:00:02')]:
        if usb.count(before) != 1:
            raise RuntimeError('Unexpected USB identity shape')
        usb = usb.replace(before, after)
    network = bootstrap['usr/libexec/full206-network-start']['payload']
    anchor = b"uci set wireless.full206.mode='ap'\n"
    if network.count(anchor) != 1:
        raise RuntimeError('Unexpected AP setup shape')
    network = network.replace(anchor, anchor + b"# Public test image: configure the AP only after the operator enables it.\nuci set wireless.full206.disabled='1'\n")
    modem = bootstrap['etc/init.d/m3100-modem']['payload']
    anchor = b'start_service() {\n'
    if modem.count(anchor) != 1:
        raise RuntimeError('Unexpected modem service shape')
    modem = modem.replace(anchor, anchor + b'\t# Public UI test profile holds the unqualified IPA/MPSS path offline.\n\t[ "$(cat /etc/full206/modem-autostart 2>/dev/null)" = 1 ] || return 0\n')
    patched = {
        'etc/dropbear/authorized_keys': b'',
        'etc/init.d/m3100-usb': usb,
        'etc/init.d/m3100-modem': modem,
        'usr/libexec/full206-network-start': network,
        'etc/m3100-payload.sha256': sha(new_payload).encode() + b'  /tmp/m3100-firmware.cpio.xz\n',
        'etc/full206-userspace.sha256': sha(new_userspace).encode() + b'  full206/userspace.tar.xz\n',
    }
    new_bootstrap = replace_payloads(old['bootstrap.cpio'], patched)
    # Scan decompressed payloads as well as the final image's outer layers.
    for n, b in [('bootstrap', new_bootstrap), ('userspace', out.getvalue())] + list(firmware.items()):
        if private_wifi in b or private_key in b or b'-----BEGIN OPENSSH PRIVATE KEY-----' in b:
            raise RuntimeError('Private provisioning retained: ' + n)
    original_firmware = inspect_archive(lzma.decompress((BASE / 'payload.cpio.xz').read_bytes()))
    preserved = sum(firmware[n] == b for n, b in original_firmware.items() if n != 'full206/userspace.tar.xz')
    if preserved != 545:
        raise RuntimeError('Original firmware changed')
    final_bootstrap = {e['name']: e for e in cpio148.parse(new_bootstrap)}
    if list(final_bootstrap) != list(base_bootstrap):
        raise RuntimeError('Bootstrap entry order/set changed')
    report = dict(original_report)
    report.pop('private_admin_input', None)
    report.update(
        public_release=True, release_version='2026.11.09.1-public',
        bootstrap_sha256=sha(new_bootstrap), userspace_sha256=sha(new_userspace), payload_sha256=sha(new_payload),
        bootstrap_changed_entries=sorted(n for n in final_bootstrap if final_bootstrap[n]['raw'] != base_bootstrap[n]['raw']),
        public_changes_from_private_bootstrap=sorted(patched),
        public_userspace_changes=sorted(n for n in replacements if n in old_contents and contents[n] != old_contents[n]),
        public_userspace_additions=sorted(set(members) - set(old_meta)),
        original_firmware_files_byte_identical=preserved,
        public_credentials={'admin': '12345', 'wifi_example': 'M3100-ram-test', 'wifi_ap_enabled': False,
                            'ssh_authorized_keys': 0, 'ssh_password_authentication': False},
        private_wifi_and_ssh_key_absent=True, modem_autostart=False, hardware_boot_tested=False,
        source_private_image_sha256='1ff176c239b593ae4943691ce0018a9f9d73e931533dc7bb2fd8a2504a271b76',
    )
    for name, data in [('bootstrap.cpio', new_bootstrap), ('userspace.tar.xz', new_userspace), ('payload.cpio.xz', new_payload)]:
        with (HERE / name).open('xb') as f:
            f.write(data)
    (HERE / 'composition.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['bootstrap_sha256', 'userspace_sha256', 'payload_sha256',
                                            'private_wifi_and_ssh_key_absent', 'modem_autostart']}))


if __name__ == '__main__':
    main()
