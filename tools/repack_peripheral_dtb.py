#!/usr/bin/env python3
"""Recompile only an M3100 DTB and preserve an existing boot image's zImage.

Default is a console control: PCIe RC, PCIe PHY and SMMU disabled. No boot,
flash, kernel make, or shared source mutation. Run from Windows with WSL tools.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

from check_ram_image import check_image, check_board, parse_dtb, require

ROOT = Path(__file__).resolve().parents[1]
DT_DIR = ROOT / 'overlay/target/linux/sdx/files-6.12/arch/arm/boot/dts/qcom'
DEFAULT_KERNEL = '/home/dell/m3100-openwrt/openwrt/build_dir/target-arm_cortex-a7+neon-vfpv4_musl_eabi/linux-sdx_generic/linux-6.12.112'


def linux_path(path):
    value = str(Path(path).resolve()).replace('\\', '/')
    if re.match(r'^[A-Za-z]:/', value):
        return '/mnt/' + value[0].lower() + value[2:]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=ROOT / 'artifacts/openwrt-m3100-peripherals-pcie.bin')
    parser.add_argument('--source', type=Path, default=DT_DIR / 'qcom-sdx65-inseego-m3100-peripherals.dts')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/openwrt-m3100-peripherals-console-control.bin')
    parser.add_argument('--work-dir', type=Path, default=ROOT / 'analysis/peripheral-console-repack')
    parser.add_argument('--include', action='append', default=[], help='Optional .dtsi, absolute path or name in the board DT directory')
    parser.add_argument('--kernel-dir', default=DEFAULT_KERNEL)
    parser.add_argument('--distro', default='Ubuntu-24.04')
    parser.add_argument('--usb-bypass-trial', action='store_true',
                        help='Explicit temporary USB physical-DMA trial; retain PCIe/PHY/SMMU, remove USB iommus and allow unclaimed SMMU streams')
    parser.add_argument('--kernel-console-trial', action='store_true',
                        help='Explicit127 isolation: PCIe/PHY/SMMU off, no enabled BT child, add reviewed built-in USB serial console arguments')
    args = parser.parse_args()
    require(not (args.kernel_console_trial and args.usb_bypass_trial),
            'kernel console isolation cannot enable PCIe/SMMU bypass trial')
    require(args.image.resolve() != args.output.resolve(), 'output must differ from source image')
    original = args.image.read_bytes()
    old_info, _ = check_image(original)
    zsize = struct.unpack_from('<I', original, 4096 + 44)[0]
    kernel = original[4096:4096 + zsize]
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source = args.source.resolve()
    includes = [source]
    for name in args.include:
        item = Path(name)
        item = item.resolve() if item.is_absolute() else (DT_DIR / item).resolve()
        require(item.is_file(), 'missing include: ' + str(item))
        # Parent may already have included a resource fragment directly.
        if re.search(r'#include\s+"' + re.escape(item.name) + r'"', source.read_text()):
            continue
        includes.append(item)
    wrapper = ''.join('#include "' + linux_path(p) + '"\n' for p in includes)
    if args.usb_bypass_trial:
        wrapper += '\n&usb_dwc3 { /delete-property/ iommus; };\n'
    else:
        wrapper += '\n&pcie_rc { status = "disabled"; };\n'
        wrapper += '&pcie_phy { status = "disabled"; };\n'
        wrapper += '&apps_smmu { status = "disabled"; };\n'
        # Keep WLAN power off when isolating PCIe from LCD/telemetry.
        wrapper += '&tlmm { /delete-node/ m3100_wlan_enable; };\n'
    (work / 'control.dts').write_text(wrapper)
    k = args.kernel_dir.rstrip('/')
    prefix = ['wsl', '-d', args.distro, '-e'] if sys.platform == 'win32' else []
    cpp = prefix + ['gcc', '-E', '-nostdinc', '-undef', '-D__DTS__', '-x', 'assembler-with-cpp',
                    '-I' + linux_path(DT_DIR), '-I' + k + '/arch/arm/boot/dts/qcom',
                    '-I' + k + '/arch/arm/boot/dts', '-I' + k + '/scripts/dtc/include-prefixes',
                    linux_path(work / 'control.dts')]
    with (work / 'control.pp.dts').open('wb') as out:
        subprocess.run(cpp, stdout=out, check=True)
    with (work / 'dtc.log').open('wb') as log:
        subprocess.run(prefix + [k + '/scripts/dtc/dtc', '-@', '-I', 'dts', '-O', 'dtb',
                       '-o', linux_path(work / 'control.dtb'), linux_path(work / 'control.pp.dts')],
                       stderr=log, check=True)
    dtb = (work / 'control.dtb').read_bytes()
    # Default checker also examines descendants of a disabled RC. Preserve
    # resource nodes but explicitly disable them for the console control.
    descendants = [p for p in parse_dtb(dtb) if p.startswith('/soc/pcie@1c00000/')]
    if descendants and not args.usb_bypass_trial:
        wrapper += ''.join('&{' + p + '} { status = "disabled"; };\n' for p in descendants)
        (work / 'control.dts').write_text(wrapper)
        with (work / 'control.pp.dts').open('wb') as out:
            subprocess.run(cpp, stdout=out, check=True)
        with (work / 'dtc.log').open('wb') as log:
            subprocess.run(prefix + [k + '/scripts/dtc/dtc', '-@', '-I', 'dts', '-O', 'dtb',
                           '-o', linux_path(work / 'control.dtb'), linux_path(work / 'control.pp.dts')],
                           stderr=log, check=True)
        dtb = (work / 'control.dtb').read_bytes()
    check_board(dtb, (ROOT / 'stock/live.dtb').read_bytes(), allow_pcie=args.usb_bypass_trial)
    nodes = parse_dtb(dtb)
    for name in ('/soc/pcie@1c00000', '/soc/phy@1c06000', '/soc/iommu@15000000'):
        enabled = nodes[name].get('status', b'okay\0') != b'disabled\0'
        require(enabled == args.usb_bypass_trial, 'unexpected trial enable state: ' + name)
    command = old_info['command_line']
    if args.kernel_console_trial:
        require(not any(t.startswith(('g_serial.', 'm3100.kernel_console=', 'console=ttyGS'))
                        for t in command.split()), 'source already selects USB console policy')
        require(not any(path.endswith('/bluetooth') and
                        node.get('status', b'okay\0') != b'disabled\0'
                        for path, node in nodes.items()), 'kernel console isolation has enabled Bluetooth child')
        command += (' console=ttyGS0,115200n8 m3100.kernel_console=1'
                    ' g_serial.enable=1 g_serial.use_acm=1 g_serial.n_ports=1'
                    ' g_serial.idVendor=0x0525 g_serial.idProduct=0xa4a7'
                    ' g_serial.iSerialNumber=76fe1623-KCON127'
                    ' g_serial.iProduct=M3100-Kernel-Console')
    if args.usb_bypass_trial:
        usb = nodes['/soc/usb@a6f8800/usb@a600000']
        require('iommus' not in usb, 'USB bypass trial still has an IOMMU binding')
        require(not any(t.startswith(('iommu.passthrough=', 'arm-smmu.disable_bypass='))
                        for t in command.split()), 'source already overrides IOMMU policy')
        command += ' arm-smmu.disable_bypass=0'
    combined = work / 'zImage-with-control-dtb'
    combined.write_bytes(kernel + dtb)
    subprocess.run([sys.executable, str(ROOT / 'overlay/target/linux/sdx/image/m3100-bootimg.py'),
                    str(combined), str(args.output), command], check=True)
    final = args.output.read_bytes()
    result, final_dtb = check_image(final)
    result.update(check_board(final_dtb, (ROOT / 'stock/live.dtb').read_bytes(), allow_pcie=args.usb_bypass_trial))
    final_zsize = struct.unpack_from('<I', final, 4096 + 44)[0]
    require(final[4096:4096 + final_zsize] == kernel, 'kernel bytes changed')
    require(result['command_line'] == command, 'repacked command line differs')
    result.update(source_image=str(args.image.resolve()), output=str(args.output.resolve()),
                  source_image_sha256=hashlib.sha256(original).hexdigest(),
                  zimage_sha256=hashlib.sha256(kernel).hexdigest(), zimage_bytes=len(kernel),
                  zimage_unchanged=True, pcie_phy_smmu_disabled=not args.usb_bypass_trial,
                  usb_bypass_temporary_trial=args.usb_bypass_trial,
                  kernel_console_isolation_trial=args.kernel_console_trial,
                  default_board_checker_passed=not args.usb_bypass_trial,
                  peripheral_board_checker_passed=args.usb_bypass_trial,
                  included_sources=[str(p) for p in includes])
    (work / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
