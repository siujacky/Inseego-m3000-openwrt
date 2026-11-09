"""Verify the analysis kernel against the unit's backed-up Android boot image.

Read-only inputs. Output contains hashes, sizes and offsets, never firmware data.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

from elftools.elf.elffile import ELFFile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boot', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--elf', type=Path, required=True)
    parser.add_argument('--expected-boot-sha256', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    boot, image = args.boot.read_bytes(), args.image.read_bytes()
    if sha(boot) != args.expected_boot_sha256.lower():
        raise ValueError('Boot backup does not match the independently verified device hash')
    if boot[:8] != b'ANDROID!':
        raise ValueError('Expected Android boot envelope')
    size = struct.unpack_from('<I', boot, 8)[0]
    page = struct.unpack_from('<I', boot, 36)[0]
    if page not in (2048, 4096, 8192) or size > len(boot) - page:
        raise ValueError('Invalid kernel bounds')
    payload = boot[page:page + size]
    offset, match = 0, None
    while True:
        offset = payload.find(b'\x1f\x8b\x08', offset)
        if offset < 0:
            break
        try:
            decoder = zlib.decompressobj(31)
            candidate = decoder.decompress(payload[offset:], 64 * 1024 * 1024)
            if decoder.eof and candidate == image:
                match = offset
                break
        except zlib.error:
            pass
        offset += 1
    if match is None:
        raise ValueError('No boot kernel GZIP stream exactly matches the analysis Image')
    with args.elf.open('rb') as handle:
        elf = ELFFile(handle)
        section = elf.get_section_by_name('.kernel')
        if section is None or section.data() != image:
            raise ValueError('ELF kernel section is not byte-identical to the verified Image')
        address = section['sh_addr']
    report = {
        'boot_sha256': sha(boot),
        'boot_bytes': len(boot),
        'kernel_payload_bytes': size,
        'gzip_offset_in_kernel': match,
        'gzip_crc_and_end_verified': True,
        'image_bytes': len(image),
        'image_sha256': sha(image),
        'elf_sha256': sha(args.elf.read_bytes()),
        'elf_kernel_virtual_base': hex(address),
        'elf_kernel_section_equals_decompressed_boot_kernel': True,
        'all_checks_passed': True,
        'inputs_modified': False,
    }
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
