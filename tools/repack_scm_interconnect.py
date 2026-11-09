"""Add only the stock SCM crypto DDR path to a verified modem RAM image.

Preserves zImage, firmware trailer bytes, all other DT properties and reserved
memory entries. Builds an artifact only; does not access or boot a device.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

from check_ram_image import check_boot_image, check_board, parse_dtb, require
from check_scm_interconnect_delta import check as check_delta
from firmware_trailer import envelope_end
from repack_peripheral_dtb import linux_path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--work-dir', type=Path, required=True)
    args = p.parse_args()
    require(not args.output.exists(), 'output already exists; choose a new name')
    original = args.image.read_bytes()
    info, dtb = check_boot_image(original)
    require(info.get('ram_payload') is True, 'requires the verified firmware trailer')
    check_board(dtb, (ROOT / 'stock/live.dtb').read_bytes(), allow_modem=True, require_staging=True)
    nodes = parse_dtb(dtb)
    require('interconnects' not in nodes['/firmware/scm'], 'source already has SCM interconnects')
    system = struct.unpack('>I', nodes['/soc/interconnect@1620000']['phandle'])[0]
    mc = struct.unpack('>I', nodes['/interconnect-mc-virt']['phandle'])[0]
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    before, after = work / 'before.dtb', work / 'after.dtb'
    before.write_bytes(dtb)
    after.write_bytes(dtb)
    subprocess.run(['wsl', '-d', 'Ubuntu-24.04', '-e', 'fdtput', '-t', 'x',
                    linux_path(after), '/firmware/scm', 'interconnects',
                    f'{system:x}', 'a', f'{mc:x}', '1'], check=True)
    delta = check_delta(before, after)
    require(delta['passed'], 'unexpected DT change: ' + str(delta))
    size = struct.unpack_from('<I', original, 4096 + 44)[0]
    kernel = original[4096:4096 + size]
    combined = work / 'zImage-with-scm-dtb'
    combined.write_bytes(kernel + after.read_bytes())
    envelope = work / 'envelope.bin'
    subprocess.run([sys.executable, str(ROOT / 'overlay/target/linux/sdx/image/m3100-bootimg.py'),
                    str(combined), str(envelope), info['command_line']], check=True)
    trailer = original[envelope_end(original):]
    result = envelope.read_bytes() + trailer
    new_info, new_dtb = check_boot_image(result)
    new_info.update(check_board(new_dtb, (ROOT / 'stock/live.dtb').read_bytes(),
                                allow_modem=True, require_staging=True))
    (work / 'final.dtb').write_bytes(new_dtb)
    require(check_delta(before, work / 'final.dtb')['passed'], 'boot packaging changed other DT properties')
    require(result[4096:4096 + size] == kernel, 'zImage changed')
    require(result[envelope_end(result):] == trailer, 'firmware trailer changed')
    require(new_info['command_line'] == info['command_line'], 'command line changed')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result)
    new_info.update(source_image=str(args.image.resolve()),
                    source_sha256=hashlib.sha256(original).hexdigest(),
                    zimage_unchanged=True, firmware_trailer_unchanged=True,
                    only_scm_interconnect_changed=True, dt_delta=delta)
    (work / 'report.json').write_text(json.dumps(new_info, indent=2) + '\n')
    print(json.dumps(new_info, indent=2))


if __name__ == '__main__':
    main()
