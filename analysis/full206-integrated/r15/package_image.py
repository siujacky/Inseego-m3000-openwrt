"""Verify the linked full206 kernel, native dependency closure and RAM envelope."""
from pathlib import Path, PurePosixPath
from io import BytesIO
import gzip
import hashlib
import importlib.util
import json
import lzma
import os
import stat
import struct
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
KERNEL = Path('/home/dell/m3100-openwrt/trials/full206-r15-linux-6.12.112')
sys.path[:0] = [str(ROOT / 'tools'), str(HERE), str(HERE.parent / 'r5'), str(HERE.parent), str(ROOT / 'analysis/wlan148-integration')]
sys.path.append('/mnt/c/Users/Dell/AppData/Roaming/Python/Python313/site-packages')
from elftools.elf.elffile import ELFFile
import cpio148
from check_ram_image import check_image, parse_dtb
from firmware_ramdisk import inspect_archive
from firmware_trailer import attach_trailer, check_trailer
from repair_metadata import validate_metadata
from validate_integrated import validate_nodes, validate_download_placement, POOL_SIZE, EXPECTED_DOWNLOAD_LIMIT
from same_payload import verify_lineage
sha = lambda data: hashlib.sha256(data).hexdigest()


def elf_symbol(path, symbol, size):
    elf = ELFFile(BytesIO(path.read_bytes()))
    entry = elf.get_section_by_name('.symtab').get_symbol_by_name(symbol)[0]
    section = elf.get_section(entry['st_shndx'])
    offset = entry['st_value'] - section['sh_addr']
    return section.data()[offset:offset + size]


def dependencies(entries):
    def resolve(name):
        for _ in range(12):
            if name not in entries:
                return None
            data, mode = entries[name]
            if not stat.S_ISLNK(mode):
                return data
            target = data.rstrip(b'\0').decode()
            value = target.lstrip('/') if target.startswith('/') else str(PurePosixPath(name).parent / target)
            parts = []
            for part in value.split('/'):
                if part == '..':
                    if not parts:
                        raise ValueError('escaping symlink')
                    parts.pop()
                elif part not in ('', '.'):
                    parts.append(part)
            name = '/'.join(parts)
        raise ValueError('symlink loop')
    rows = []
    for name, (data, mode) in sorted(entries.items()):
        if not stat.S_ISREG(mode) or data[:4] != b'\x7fELF' or name.endswith('.ko') or name.startswith('lib/firmware/'):
            continue
        elf = ELFFile(BytesIO(data))
        assert elf.elfclass == 32 and elf.little_endian and elf['e_machine'] == 'EM_ARM', name
        needed = []
        interpreter = None
        for segment in elf.iter_segments():
            if segment['p_type'] == 'PT_INTERP':
                interpreter = segment.get_interp_name().lstrip('/')
                assert resolve(interpreter) is not None, (name, interpreter)
        # OpenWrt sstrip removes section headers; dynamic segments remain authoritative.
        for segment in elf.iter_segments():
            if segment['p_type'] == 'PT_DYNAMIC':
                needed.extend(tag.needed for tag in segment.iter_tags() if tag.entry.d_tag == 'DT_NEEDED')
        for dependency in needed:
            assert any(resolve(folder + '/' + dependency) is not None for folder in ('lib', 'usr/lib', str(PurePosixPath(name).parent))), (name, dependency)
        rows.append({'path': name, 'needed': needed, 'interpreter': interpreter})
    return rows


def main():
    composition = json.loads((HERE / 'composition.json').read_text())
    cpio = (HERE / composition['bootstrap_file']).read_bytes()
    assert sha(cpio) == composition['bootstrap_sha256']
    lineage = verify_lineage(cpio, composition)
    trusted_baseline = (ROOT / 'analysis/bt204-enable-config-bt/bt204.cpio').read_bytes()
    assert sha(trusted_baseline) == '2745aa1b73ebb876439003e8fbd3144662e7c7837f56c34f0f039050683f470e'
    metadata = validate_metadata(cpio, trusted_baseline)
    assert metadata['special_device_nodes_preserved'] == 8
    initial = json.loads((HERE / 'kernel-pins.json').read_text())
    assert sha((KERNEL / '.config').read_bytes()) == initial['files']['.config']['sha256']
    assert sha((KERNEL / 'Module.symvers').read_bytes()) == initial['files']['Module.symvers']['sha256']
    entries = {e['name']: (e['payload'], e['fields'][1]) for e in cpio148.parse(cpio)}
    assert b'CONFIG_QCOM_IPA=m\n' in (KERNEL / '.config').read_bytes()
    assert len(composition['modules']) == 64
    for module in composition['modules'].values():
        assert sha(entries[module['archive_path']][0]) == module['sha256']
    assert composition['modules']['ipa']['archive_path'] == 'usr/lib/m3100-ipa/ipa.ko'
    assert 'lib/modules/6.12.112-full206/ipa.ko' not in entries
    alias = entries['lib/modules/6.12.112-full206/modules.alias'][0].decode().splitlines()
    assert not any(line.split()[-1] == 'ipa' for line in alias if line.split())
    assert b'ipa.ko' not in entries['lib/modules/6.12.112-full206/modules.dep'][0]
    assert b'ipa.ko' not in entries['lib/modules/6.12.112-full206/modules.order'][0]
    for name, (data, mode) in entries.items():
        if name.startswith(('etc/modules.d/', 'etc/modules-boot.d/')) and stat.S_ISREG(mode):
            assert 'ipa' not in data.decode().splitlines()
    modem_start = entries['usr/libexec/m3100-modem-start'][0]
    assert modem_start.index(b'/usr/libexec/m3100-ipa-start') < modem_start.index(b'echo start > "$r/state"')
    archive = (HERE / 'payload.cpio.xz').read_bytes()
    assert sha(archive) == composition['payload_sha256']
    firmware = inspect_archive(lzma.decompress(archive))
    assert len(firmware) == 546
    userspace = firmware['full206/userspace.tar.xz']
    assert userspace == (HERE / 'userspace.tar.xz').read_bytes()
    with tarfile.open(fileobj=BytesIO(lzma.decompress(userspace)), mode='r:') as tar:
        names = set()
        for member in tar:
            assert member.name not in names
            names.add(member.name)
            assert not member.name.startswith('/') and '..' not in PurePosixPath(member.name).parts
            assert member.uid == member.gid == 0
            if member.isdir():
                assert member.name not in entries, 'Directory must not override bootstrap metadata'
                expected_mode = 0o700 if member.name == 'etc/full206' or member.name.startswith('etc/full206/') else 0o755
                assert member.mode == expected_mode, member.name
                entries[member.name] = (b'', stat.S_IFDIR | member.mode)
            elif member.issym():
                entries[member.name] = (member.linkname.encode(), stat.S_IFLNK | member.mode)
            elif member.isfile():
                entries[member.name] = (tar.extractfile(member).read(), stat.S_IFREG | member.mode)
            else:
                raise ValueError('Unexpected userspace member type')
    needed = dependencies(entries)
    for path in ('usr/bin/python3', 'usr/bin/mmcli', 'usr/sbin/ModemManager', 'sbin/rpcd', 'usr/bin/dbus-daemon', 'usr/bin/m3100-modem'):
        assert path in entries, path
    assert entries['etc/full206/admin-password'][1] & 0o777 == 0o600
    assert entries['etc/full206/wifi-password'][1] & 0o777 == 0o600
    assert b'"platform": "openwrt"' in entries['etc/m3100-webui.json'][0]
    linked = (KERNEL / 'usr/initramfs_inc_data').read_bytes()
    assert lzma.decompress(linked) == cpio
    size = struct.unpack('<I', elf_symbol(KERNEL / 'vmlinux', '__initramfs_size', 4))[0]
    assert elf_symbol(KERNEL / 'vmlinux', '__initramfs_start', size) == linked
    image = (KERNEL / 'arch/arm/boot/Image').read_bytes()
    piggy = (KERNEL / 'arch/arm/boot/compressed/piggy_data').read_bytes()
    zimage = (KERNEL / 'arch/arm/boot/zImage').read_bytes()
    assert gzip.decompress(piggy) == image and image.count(linked) == 1
    compressed_elf = ELFFile(BytesIO((KERNEL / 'arch/arm/boot/compressed/vmlinux').read_bytes()))
    symbols = compressed_elf.get_section_by_name('.symtab')
    start = symbols.get_symbol_by_name('input_data')[0]['st_value']
    end = symbols.get_symbol_by_name('input_data_end')[0]['st_value']
    assert elf_symbol(KERNEL / 'arch/arm/boot/compressed/vmlinux', 'input_data', end-start) == piggy
    objcopy = '/home/dell/m3100-openwrt/openwrt/staging_dir/toolchain-arm_cortex-a7+neon-vfpv4_gcc-14.3.0_musl_eabi/bin/arm-openwrt-linux-muslgnueabi-objcopy'
    for source, expected, name in [('vmlinux', image, 'Image'), ('arch/arm/boot/compressed/vmlinux', zimage, 'zImage')]:
        output = HERE / ('linked-r6-' + name)
        if not output.exists():
            subprocess.run([objcopy, '-O', 'binary', '-R', '.comment', '-S', str(KERNEL / source), str(output)], check=True)
        assert output.read_bytes() == expected
    dtb = (HERE.parent / 'full206-r3.dtb').read_bytes()
    baseline = (ROOT / 'analysis/lan162-current-kernel/lan162.dtb').read_bytes()
    stock = (ROOT / 'stock/live.dtb').read_bytes()
    profile = validate_nodes(parse_dtb(dtb), parse_dtb(baseline), parse_dtb(stock))
    base_info, _ = check_image((ROOT / 'artifacts/openwrt-m3100-bt204-config-bt-ram.bin').read_bytes())
    combined = HERE / 'zImage-with-full206-r15-dtb'
    combined.write_bytes(zimage + dtb)
    base_path = HERE / 'kernel-envelope-r6.bin'
    if not base_path.exists():
        subprocess.run([sys.executable, str(ROOT / 'overlay/target/linux/sdx/image/m3100-bootimg.py'), str(combined), str(base_path), base_info['command_line']], check=True)
    envelope, checked_dtb = check_image(base_path.read_bytes())
    assert base_path.read_bytes()[4096:4096+len(zimage)] == zimage
    assert parse_dtb(checked_dtb) == parse_dtb(dtb)
    assert envelope['command_line'] == base_info['command_line']
    result = attach_trailer(base_path.read_bytes(), firmware, archive)
    report, actual_dtb = check_trailer(result)
    assert parse_dtb(actual_dtb) == parse_dtb(dtb)
    artifact = ROOT / 'artifacts/openwrt-m3100-full206-r15-scm-status-ram.bin'
    if artifact.exists():
        assert artifact.read_bytes() == result, 'Existing artifact differs; refuse replacement'
    else:
        with artifact.open('xb') as stream:
            stream.write(result)
    report.update(rootfs_metadata_validation=metadata, image=str(artifact), kernel_release='6.12.112-full206', integrated_profile=profile,
        R15_payload_lineage= lineage,
        manual_IPA_and_order_validated=True, explicit_userspace_directory_modes_validated=True,
        staging_pool_size=POOL_SIZE, download_placement=validate_download_placement(len(result), EXPECTED_DOWNLOAD_LIMIT),
        linked_vmlinux_Image_piggy_zImage_initramfs_exact=True, final_ABI_equals_module_build_ABI=True,
        native_elf_dependency_closure=needed, hardware_boot_tested=False, stock_partition_writes=False,
        support_limit='Bluetooth controller, GNSS fix, battery capacity/charging and integrated runtime qualification remain outstanding.')
    (HERE / 'image-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'native_elf_dependency_closure'}, indent=2))


if __name__ == '__main__':
    main()
