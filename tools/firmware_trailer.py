"""Single RAM download with firmware outside ABL's small relocation window.

The Android envelope has no external ramdisk. A verified firmware archive follows
it and remains in the fastboot download buffer, protected by a DT staging pool.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from firmware_ramdisk import combine, check_combined_image, need

MAGIC = b'M3100_FW_RAM_TRAILER_V1'.ljust(32, b'\0')
POOL_START, POOL_SIZE = 0xa1000000, 0x09000000


def envelope_end(image):
    need(len(image) >= 4096 and image[:8] == b'ANDROID!', 'invalid Android envelope')
    ks = struct.unpack_from('<I', image, 8)[0]
    need(104 <= ks <= 16 * 1024 * 1024 and image[16:20] == bytes(4), 'invalid kernel-only envelope')
    return 4096 + ((ks + 4095) & ~4095)


def check_trailer(image):
    end = envelope_end(image)
    need(len(image) >= end + 4096, 'trailer missing')
    h = image[end:end + 4096]
    need(h[:32] == MAGIC, 'trailer magic mismatch')
    version, header_size, size, unpacked = struct.unpack_from('<IIQQ', h, 32)
    need(version == 1 and header_size == 4096 and 0 < size <= 96 * 1024 * 1024,
         'invalid trailer header')
    need(len(image) == end + 4096 + ((size + 4095) & ~4095), 'trailer length mismatch')
    data = image[end + 4096:end + 4096 + size]
    need(hashlib.sha256(data).digest() == h[56:88], 'trailer payload hash mismatch')
    need(not any(h[124:]) and not any(image[end + 4096 + size:]), 'nonzero trailer padding')
    # Reuse the bounded archive, ELF and manifest checks of the external format.
    temp = bytearray(image[:end])
    ks = struct.unpack_from('<I', temp, 8)[0]
    struct.pack_into('<II', temp, 16, size, 0xa1000000)
    temp[576:596] = hashlib.sha1(temp[4096:4096 + ks] + struct.pack('<I', ks) +
                                data + struct.pack('<II', size, 0)).digest()
    temp += data + bytes(-size % 4096)
    result, dtb = check_combined_image(bytes(temp), enforce_bootloader_window=False)
    consoles = [t for t in result['command_line'].split() if t.startswith('console=')]
    need(consoles and consoles[0] == 'console=ttyGS0,115200n8' and
         consoles[-1] == 'console=ttyMSM0,115200n8', 'USB log console must preserve UART primary')
    need(not result['ramdisk_probe'], 'diagnostic marker is not a firmware trailer')
    need(result['firmware_unpacked_size'] == unpacked and
         bytes.fromhex(result['firmware_manifest_sha256']) == h[88:120] and
         result['firmware_files'] == struct.unpack_from('<I', h, 120)[0], 'trailer metadata mismatch')
    result.update(sha256=hashlib.sha256(image).hexdigest(), size=len(image),
                  ram_payload=True, firmware_payload_size=size,
                  staging_pool_start=hex(POOL_START), staging_pool_size=POOL_SIZE)
    result.pop('requested_ramdisk_address', None)
    result.pop('firmware_ramdisk_size', None)
    return result, dtb


def attach_trailer(base, files, archive=None):
    external = combine(base, files, archive, enforce_bootloader_window=False)
    info, _ = check_combined_image(external, enforce_bootloader_window=False)
    ks, _, size = struct.unpack_from('<III', external, 8)
    end = 4096 + ((ks + 4095) & ~4095)
    data = external[end:end + size]
    out = bytearray(external[:end])
    command = info['command_line']
    if 'console=ttyGS0,115200n8' not in command.split():
        command = command.replace('console=ttyMSM0,115200n8',
                                  'console=ttyGS0,115200n8 console=ttyMSM0,115200n8', 1)
    cmd = command.encode()
    need(len(cmd) < 1536, 'command line too long')
    out[64:576] = cmd[:512].ljust(512, b'\0')
    out[608:1632] = cmd[512:].ljust(1024, b'\0')
    struct.pack_into('<II', out, 16, 0, 0x81100000)
    out[576:596] = hashlib.sha1(out[4096:4096 + ks] + struct.pack('<I', ks) + bytes(8)).digest()
    h = bytearray(4096)
    h[:32] = MAGIC
    struct.pack_into('<IIQQ', h, 32, 1, 4096, len(data), info['firmware_unpacked_size'])
    h[56:88] = hashlib.sha256(data).digest()
    h[88:120] = bytes.fromhex(info['firmware_manifest_sha256'])
    struct.pack_into('<I', h, 120, info['firmware_files'])
    out += h + data + bytes(-len(data) % 4096)
    check_trailer(bytes(out))
    return bytes(out)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('kernel', type=Path)
    p.add_argument('firmware', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--source-manifest', required=True, type=Path)
    p.add_argument('--archive', required=True, type=Path)
    a = p.parse_args()
    files = {f.relative_to(a.firmware).as_posix(): f.read_bytes()
             for f in a.firmware.rglob('*') if f.is_file()}
    trusted = {r['file']: r['sha256'] for r in json.loads(a.source_manifest.read_text()) if r['verified']}
    need(trusted == {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}, 'unverified firmware source')
    image = attach_trailer(a.kernel.read_bytes(), files, a.archive.read_bytes())
    a.output.write_bytes(image)
    report, _ = check_trailer(image)
    print(json.dumps(report, indent=2))
