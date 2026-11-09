"""Validate and RAM-boot the management image; never flash a partition."""
import datetime
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
from check_ram_image import check_boot_image, check_board
from winusb_fastboot import Fastboot

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--image', type=Path, default=root / 'artifacts/openwrt-sdx-generic-inseego_m3100-ram-usbnet-final-initramfs-kernel.bin')
parser.add_argument('--report', type=Path, default=root / 'analysis/ram-usbnet-attempt.json')
parser.add_argument('--already-bootloader', action='store_true')
parser.add_argument('--peripherals', action='store_true', help='Use the checked modem-disabled PCIe trial')
parser.add_argument('--kernel-only-control', action='store_true',
                    help='Debug the manual-start full kernel without a firmware ramdisk')
args = parser.parse_args()
image = args.image
data = image.read_bytes()
report, dtb = check_boot_image(data)
if args.kernel_only_control and report.get('firmware_files'):
    raise ValueError('Kernel-only control must not contain a firmware ramdisk')
report.update(check_board(dtb, (root / 'stock/live.dtb').read_bytes(),
                          allow_modem=bool(report.get('modem_load_segments')) or args.kernel_only_control,
                          require_staging=bool(report.get('ram_payload')),
                          allow_pcie=args.peripherals))
report['peripheral_trial'] = args.peripherals
report['kernel_only_control'] = args.kernel_only_control
report['utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
report['sha256'] = hashlib.sha256(data).hexdigest()
report['image'] = image.name
if not args.already_bootloader:
    identity = subprocess.check_output(['adb', '-s', '76fe1623', 'shell', 'id'], timeout=10)
    if b'uid=0(root)' not in identity:
        raise RuntimeError('Root ADB required: run adb root and verify shell id first')
    subprocess.run(['adb', '-s', '76fe1623', 'reboot', 'bootloader'], check=True, timeout=15)
for attempt in range(40):
    try:
        fb = Fastboot()
        break
    except OSError:
        time.sleep(2)
else:
    raise RuntimeError('Bootloader USB did not appear')
try:
    assert fb.command('getvar:serialno')['value'] == '76fe1623'
    assert fb.command('getvar:unlocked')['value'] == 'yes'
    maximum = fb.command('getvar:max-download-size')
    assert maximum['status'] == 'OKAY'
    value = maximum['value']
    limit = int(value, 16 if value.lower().startswith('0x') else 10)
    if len(data) > limit:
        raise RuntimeError('Image exceeds this bootloader RAM-download limit')
    report['bootloader_max_download'] = limit
    report['download'] = fb.download_ram_image(data)
    report['boot'] = fb.command('boot')
finally:
    fb.close()
args.report.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2), flush=True)
