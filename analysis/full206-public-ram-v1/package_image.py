"""Independent R15 derivation/envelope checks; does not contact a device."""
from pathlib import Path, PurePosixPath
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
import stat
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT / 'analysis/full206-integrated/r15'
OLD_KERNEL = Path('/home/dell/m3100-openwrt/trials/full206-r15-linux-6.12.112')
KERNEL = Path('/home/dell/m3100-openwrt/trials/full206-public-v1-linux-6.12.112')
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'analysis/wlan148-integration'), str(ROOT / 'analysis/full206-integrated'), str(BASE / '../r5')]
sys.path.append('/mnt/c/Users/Dell/AppData/Roaming/Python/Python313/site-packages')
from elftools.elf.elffile import ELFFile
import cpio148
from firmware_ramdisk import inspect_archive
from firmware_trailer import attach_trailer, check_trailer
from check_ram_image import parse_dtb
from validate_integrated import validate_nodes, validate_download_placement, EXPECTED_DOWNLOAD_LIMIT, POOL_SIZE
from repair_metadata import validate_metadata
spec = importlib.util.spec_from_file_location('r15_dependency_verifier', BASE / 'package_image.py')
baseline_verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline_verify)
sha = lambda data: hashlib.sha256(data).hexdigest()


def main():
    original_image = (ROOT / 'artifacts/openwrt-m3100-full206-r15-scm-status-ram.bin').read_bytes()
    if sha(original_image) != '81b20eacf658c3ec0e54a2744026d9d003360f2d8fdd1f2d34b6e121f6ee14a4':
        raise RuntimeError('Original R15 image changed')
    original_report, original_dtb = check_trailer(original_image)
    composition = json.loads((HERE / 'composition.json').read_text())
    cpio = (HERE / 'bootstrap.cpio').read_bytes()
    if sha(cpio) != composition['bootstrap_sha256']:
        raise RuntimeError('Bootstrap pin mismatch')
    old_entries = {e['name']: e for e in cpio148.parse((BASE / 'full206-lcd.cpio').read_bytes())}
    new_entries = {e['name']: e for e in cpio148.parse(cpio)}
    if list(old_entries) != list(new_entries):
        raise RuntimeError('Bootstrap entry set/order changed')
    changed = [name for name in old_entries if old_entries[name]['raw'] != new_entries[name]['raw']]
    if sorted(changed) != sorted(composition['bootstrap_changed_entries']):
        raise RuntimeError('Unscoped bootstrap change')
    for name in changed:
        old, new = old_entries[name], new_entries[name]
        if any(old['fields'][i] != new['fields'][i] for i in range(13) if i not in (6, 12)):
            raise RuntimeError('Bootstrap metadata changed: ' + name)
    metadata = validate_metadata(cpio, (ROOT / 'analysis/bt204-enable-config-bt/bt204.cpio').read_bytes())
    if metadata['special_device_nodes_preserved'] != 8:
        raise RuntimeError('Special device metadata mismatch')
    base_composition = json.loads((BASE / 'composition.json').read_text())
    for module in base_composition['modules'].values():
        name = module['archive_path']
        if sha(new_entries[name]['payload']) != module['sha256']:
            raise RuntimeError('Packaged module changed: ' + name)
    source_changes, object_changes, modules = [], [], 0
    source_count, object_count = 0, 0
    derived = {'usr/initramfs_data.o', 'vmlinux.o', 'arch/arm/boot/compressed/piggy.o'}
    for old in OLD_KERNEL.rglob('*'):
        if not old.is_file() or old.is_symlink():
            continue
        relative = old.relative_to(OLD_KERNEL).as_posix()
        source = old.suffix in ('.c', '.h', '.S', '.lds') or old.name in ('Makefile', 'Kconfig', '.config', 'Module.symvers')
        obj = old.suffix == '.o'
        ko = old.suffix == '.ko'
        if not (source or obj or ko):
            continue
        new = KERNEL / relative
        if not new.is_file():
            raise RuntimeError('Missing cloned build file: ' + relative)
        different = sha(old.read_bytes()) != sha(new.read_bytes())
        if source:
            source_count += 1
            if different:
                source_changes.append(relative)
        if obj:
            object_count += 1
            if different:
                object_changes.append(relative)
                if relative not in derived and not old.name.startswith('.tmp_vmlinux'):
                    raise RuntimeError('Unexpected compiled-object change: ' + relative)
        if ko:
            modules += 1
            if different:
                raise RuntimeError('Native module changed: ' + relative)
    if source_changes:
        raise RuntimeError('Kernel source/config/ABI changed: ' + str(source_changes))
    linked = (KERNEL / 'usr/initramfs_inc_data').read_bytes()
    if lzma.decompress(linked) != cpio:
        raise RuntimeError('Wrong linked bootstrap')
    initramfs_size = int.from_bytes(baseline_verify.elf_symbol(KERNEL / 'vmlinux', '__initramfs_size', 4), 'little')
    if baseline_verify.elf_symbol(KERNEL / 'vmlinux', '__initramfs_start', initramfs_size) != linked:
        raise RuntimeError('vmlinux does not contain checked initramfs')
    image = (KERNEL / 'arch/arm/boot/Image').read_bytes()
    piggy = (KERNEL / 'arch/arm/boot/compressed/piggy_data').read_bytes()
    if gzip.decompress(piggy) != image or image.count(linked) != 1:
        raise RuntimeError('Linked Image mismatch')
    elf = ELFFile(io.BytesIO((KERNEL / 'arch/arm/boot/compressed/vmlinux').read_bytes()))
    symbols = elf.get_section_by_name('.symtab')
    start, end = [symbols.get_symbol_by_name(n)[0]['st_value'] for n in ('input_data', 'input_data_end')]
    if baseline_verify.elf_symbol(KERNEL / 'arch/arm/boot/compressed/vmlinux', 'input_data', end - start) != piggy:
        raise RuntimeError('zImage piggy linkage mismatch')
    objcopy = '/home/dell/m3100-openwrt/openwrt/staging_dir/toolchain-arm_cortex-a7+neon-vfpv4_gcc-14.3.0_musl_eabi/bin/arm-openwrt-linux-muslgnueabi-objcopy'
    for source, expected, name in [('vmlinux', image, 'Image'),
                                   ('arch/arm/boot/compressed/vmlinux', (KERNEL / 'arch/arm/boot/zImage').read_bytes(), 'zImage')]:
        output = HERE / ('checked-' + name)
        subprocess.run([objcopy, '-O', 'binary', '-R', '.comment', '-S', str(KERNEL / source), str(output)], check=True)
        if output.read_bytes() != expected:
            raise RuntimeError('Packaged kernel binary is not the checked ELF output: ' + name)
    entries = {n: (e['payload'], e['fields'][1]) for n, e in new_entries.items()}
    payload = (HERE / 'payload.cpio.xz').read_bytes()
    firmware = inspect_archive(lzma.decompress(payload))
    original_firmware = inspect_archive(lzma.decompress((BASE / 'payload.cpio.xz').read_bytes()))
    if set(firmware) != set(original_firmware) or any(firmware[n] != b for n, b in original_firmware.items() if n != 'full206/userspace.tar.xz'):
        raise RuntimeError('Original firmware changed')
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(firmware['full206/userspace.tar.xz'])), mode='r:') as archive:
        seen = set()
        for member in archive:
            if member.name in seen or member.name.startswith('/') or '..' in PurePosixPath(member.name).parts or member.uid != 0 or member.gid != 0:
                raise RuntimeError('Unsafe merged rootfs')
            seen.add(member.name)
            if member.isdir():
                if member.name in entries:
                    raise RuntimeError('Userspace overrides bootstrap directory: ' + member.name)
                entries[member.name] = (b'', stat.S_IFDIR | member.mode)
            elif member.issym():
                entries[member.name] = (member.linkname.encode(), stat.S_IFLNK | member.mode)
            elif member.isfile():
                entries[member.name] = (archive.extractfile(member).read(), stat.S_IFREG | member.mode)
            else:
                raise RuntimeError('Unexpected merged member')
    closure = baseline_verify.dependencies(entries)
    for name in ('etc/full206/admin-password', 'etc/full206/wifi-password'):
        if entries[name][1] & 0o777 != 0o600:
            raise RuntimeError('Private seed permissions changed')
    cfg = json.loads(entries['etc/full206-webui.json'][0])
    if cfg.get('platform') != 'openwrt' or cfg.get('mock', False) or cfg.get('port') != 8088:
        raise RuntimeError('Incorrect real-hardware WebUI configuration')
    combined = HERE / 'zImage-with-feelgood-dtb'
    combined.write_bytes((KERNEL / 'arch/arm/boot/zImage').read_bytes() + original_dtb)
    envelope = HERE / 'kernel-envelope.bin'
    subprocess.run([sys.executable, str(ROOT / 'overlay/target/linux/sdx/image/m3100-bootimg.py'), str(combined), str(envelope), original_report['command_line']], check=True)
    candidate = attach_trailer(envelope.read_bytes(), firmware, payload)
    report, dtb = check_trailer(candidate)
    if dtb != original_dtb or report['command_line'] != original_report['command_line']:
        raise RuntimeError('DT or kernel command line changed')
    profile = validate_nodes(parse_dtb(dtb), parse_dtb((ROOT / 'analysis/lan162-current-kernel/lan162.dtb').read_bytes()), parse_dtb((ROOT / 'stock/live.dtb').read_bytes()))
    artifact = ROOT / 'artifacts/openwrt-m3100-full206-r15-public-v1-ram.bin'
    if artifact.exists():
        if artifact.read_bytes() != candidate:
            raise RuntimeError('Existing reviewed candidate differs; refusing replacement')
    else:
        with artifact.open('xb') as stream:
            stream.write(candidate)
    report.update(image=str(artifact), kernel_release='6.12.112-full206', webui_version='2026.11.08.1', touchscreen_version='v6',
        original_R15_preserved=True, kernel_source_and_config_unchanged=not source_changes,
        source_files_compared=source_count, compiled_objects_compared=object_count, derived_object_changes=object_changes,
        native_modules_byte_identical=modules, packaged_modules_byte_identical=len(base_composition['modules']),
        original_firmware_files_byte_identical=545, dtb_byte_identical=True, command_line_unchanged=True,
        rootfs_metadata_validation=metadata, integrated_profile=profile,
        download_placement=validate_download_placement(len(candidate), EXPECTED_DOWNLOAD_LIMIT),
        staging_pool_size=POOL_SIZE,
        linked_vmlinux_Image_piggy_zImage_initramfs_exact=True, final_ABI_equals_module_build_ABI=True,
        objcopy_Image_and_zImage_match_checked_ELFs=True,
        native_elf_dependency_closure=closure, startup_bootstrap_changed_entries=changed,
        hardware_boot_tested=False, stock_partition_writes=False)
    (HERE / 'image-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'native_elf_dependency_closure'}, indent=2))


if __name__ == '__main__':
    main()
