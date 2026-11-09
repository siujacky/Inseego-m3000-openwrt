"""Pack verified local firmware into an external RAM archive in one boot image.

The kernel stays below 16 MiB. Firmware loads at 0xa1000000, above the stock
MPSS and ramoops reservations, inside the second 512 MiB RAM bank.
"""
import argparse
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import stat
import struct

PREFIX = 'lib/firmware/m3100/'
MANIFEST = 'etc/m3100-firmware-manifest.json'
HASHLIST = 'etc/m3100-firmware.sha256'
MAX_ARCHIVE = 192 * 1024 * 1024
RAMDISK_ADDRESS = 0xa1000000
PROBE_FILES = {'ramdisk-probe.txt': b'M3100 external ramdisk probe\n'}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def safe_name(name):
    need(isinstance(name, str) and name and '\\' not in name and '\0' not in name,
         'invalid firmware path')
    p = PurePosixPath(name)
    need(not p.is_absolute() and all(x not in ('', '.', '..') for x in name.split('/')),
         'unsafe firmware path')
    return name


def entry(name, data, mode, ino):
    name = name.encode() + b'\0'
    fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name), 0]
    out = b'070701' + ''.join('%08x' % n for n in fields).encode() + name
    out += bytes(-len(out) % 4)
    return out + data + bytes(-len(data) % 4)


def newc_archive(files):
    need(files, 'empty firmware set')
    files = {safe_name(n): b for n, b in files.items()}
    need(all(isinstance(b, bytes) for b in files.values()), 'firmware must be bytes')
    manifest = json.dumps({'version': 1, 'files': {
        n: hashlib.sha256(b).hexdigest() for n, b in sorted(files.items())
    }}, sort_keys=True).encode() + b'\n'
    paths = {PREFIX + n: b for n, b in files.items()}
    paths[MANIFEST] = manifest
    paths[HASHLIST] = ''.join(hashlib.sha256(b).hexdigest() + '  ' + n + '\n'
                             for n, b in sorted(files.items())).encode()
    dirs = {'lib', 'lib/firmware', 'lib/firmware/m3100', 'etc'}
    for n in paths:
        dirs.update(str(p) for p in PurePosixPath(n).parents if str(p) != '.')
    out = bytearray()
    for n in sorted(dirs):
        out.extend(entry(n, b'', stat.S_IFDIR | 0o755, len(out) + 1))
    for n, b in sorted(paths.items()):
        out.extend(entry(n, b, stat.S_IFREG | 0o644, len(out) + 1))
    out.extend(entry('TRAILER!!!', b'', 0, len(out) + 1))
    need(len(out) <= MAX_ARCHIVE, 'firmware archive exceeds memory budget')
    return bytes(out)


def inspect_archive(blob):
    need(len(blob) <= MAX_ARCHIVE, 'archive too large')
    pos, files, manifest, hashlist, seen = 0, {}, None, None, set()
    while True:
        need(pos + 110 <= len(blob) and blob[pos:pos + 6] == b'070701', 'invalid newc header')
        try:
            f = [int(blob[pos + 6 + i * 8:pos + 14 + i * 8], 16) for i in range(13)]
        except ValueError:
            raise ValueError('non-hex newc header') from None
        _, mode, uid, gid, _, _, size, _, _, _, _, namesize, _ = f
        need(uid == gid == 0 and 0 < namesize <= 4096, 'invalid ownership/name length')
        pos += 110
        need(pos + namesize <= len(blob) and blob[pos + namesize - 1] == 0, 'truncated name')
        name = blob[pos:pos + namesize - 1].decode()
        pos = (pos + namesize + 3) & ~3
        need(pos + size <= len(blob), 'truncated file')
        data = blob[pos:pos + size]
        pos = (pos + size + 3) & ~3
        if name == 'TRAILER!!!':
            need(size == 0 and not any(blob[pos:]), 'invalid archive trailer')
            break
        safe_name(name)
        need(name not in seen, 'duplicate archive path')
        seen.add(name)
        if stat.S_ISDIR(mode):
            need(size == 0 and (name == 'etc' or name == 'lib' or
                 name == 'lib/firmware' or name == PREFIX.rstrip('/') or
                 name.startswith(PREFIX)), 'unexpected directory')
            continue
        need(stat.S_ISREG(mode), 'archive contains non-regular file')
        if name == MANIFEST:
            manifest = json.loads(data)
        elif name == HASHLIST:
            hashlist = data
        else:
            need(name.startswith(PREFIX), 'archive overwrites non-firmware path')
            files[safe_name(name[len(PREFIX):])] = data
    expected = {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
    need(manifest == {'version': 1, 'files': expected}, 'firmware manifest mismatch')
    need(hashlist == ''.join(h + '  ' + n + '\n' for n, h in sorted(expected.items())).encode(),
         'firmware startup hash list mismatch')
    return files


def check_ramdisk_layout(address, size):
    need(address == RAMDISK_ADDRESS and 0 < size <= 160 * 1024 * 1024,
         'unexpected ramdisk address/size')
    need(address + size <= 0xc0000000, 'ramdisk exceeds physical RAM')


def check_modem_segments(files):
    mdt = files.get('modem.mdt', b'')
    need(len(mdt) >= 52 and mdt[:6] == b'\x7fELF\x01\x01', 'expected ELF32 little-endian modem MDT')
    need(struct.unpack_from('<H', mdt, 18)[0] == 164, 'expected Qualcomm Hexagon firmware')
    offset = struct.unpack_from('<I', mdt, 28)[0]
    size, count = struct.unpack_from('<HH', mdt, 42)
    need(size == 32 and 0 < count <= 128 and offset + size * count <= len(mdt),
         'invalid modem program headers')
    segments = 0
    for i in range(count):
        kind, _, _, addr, fsize, msize, _, _ = struct.unpack_from('<8I', mdt, offset + i * size)
        if kind != 1:
            continue
        need(0x90800000 <= addr < addr + msize <= 0xa0800000,
             'modem segment outside exact stock MPSS carveout')
        if fsize:
            need(len(files.get('modem.b%02d' % i, b'')) == fsize, 'missing/truncated modem segment')
        segments += 1
    need(segments > 0, 'no loadable modem segments')
    return segments


def check_abl_relocation(kernel_size, ramdisk_size):
    # Measured with the marker image, and matched against this unit's ABL
    # UpdateBootParams implementation: KernelEndAddr is 0x82300000.
    start = 0x82300000 - ((ramdisk_size + 4095) & ~4095) - 4096
    dtb = start - 2 * 1024 * 1024 - 4096
    need(dtb >= 0x80008000 + kernel_size, 'external ramdisk exceeds ABL relocation window')
    return start


def check_combined_image(image, *, enforce_bootloader_window=True):
    from check_ram_image import check_image
    need(4096 < len(image) <= MAX_ARCHIVE, 'combined image too large')
    need(image[:8] == b'ANDROID!', 'invalid Android magic')
    ks, _, rs, ra, second = struct.unpack_from('<5I', image, 8)
    check_ramdisk_layout(ra, rs)
    need(second == 0 and 104 <= ks <= 16 * 1024 * 1024, 'invalid kernel/second size')
    if enforce_bootloader_window:
        check_abl_relocation(ks, rs)
    ro = 4096 + ((ks + 4095) & ~4095)
    need(len(image) == ro + ((rs + 4095) & ~4095), 'combined image length mismatch')
    kernel, ramdisk = image[4096:4096 + ks], image[ro:ro + rs]
    need(not any(image[4096 + ks:ro]) and not any(image[ro + rs:]), 'nonzero padding')
    digest = hashlib.sha1(kernel + struct.pack('<I', ks) + ramdisk + struct.pack('<II', rs, 0)).digest()
    need(image[576:596] == digest, 'combined Android hash mismatch')
    base = bytearray(image[:ro])
    struct.pack_into('<II', base, 16, 0, 0x81100000)
    base[576:596] = hashlib.sha1(kernel + struct.pack('<I', ks) + bytes(8)).digest()
    result, dtb = check_image(bytes(base))
    need([t for t in result['command_line'].split() if t.startswith('vmalloc=')] ==
         ['vmalloc=384M'], 'missing modem virtual mapping budget')
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=32 * 1024 * 1024)
    archive = dec.decompress(ramdisk, max_length=MAX_ARCHIVE + 1)
    need(dec.eof and not dec.unused_data and len(archive) <= MAX_ARCHIVE, 'invalid or oversized XZ archive')
    files = inspect_archive(archive)
    probe = files == PROBE_FILES
    segments = 0 if probe else check_modem_segments(files)
    result.update(sha256=hashlib.sha256(image).hexdigest(), size=len(image),
                  firmware_ramdisk_size=rs, firmware_unpacked_size=len(archive),
                  requested_ramdisk_address=hex(ra), firmware_files=len(files),
                  modem_load_segments=segments, ramdisk_probe=probe,
                  firmware_manifest_sha256=hashlib.sha256(json.dumps(
                      {'version': 1, 'files': {n: hashlib.sha256(b).hexdigest()
                                             for n, b in sorted(files.items())}},
                      sort_keys=True).encode() + b'\n').hexdigest())
    return result, dtb


def combine(base, files, ramdisk=None, *, enforce_bootloader_window=True):
    from check_ram_image import check_image
    original, _ = check_image(base)
    if ramdisk is None:
        archive = newc_archive(files)
        ramdisk = lzma.compress(archive, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32, preset=6)
    check_ramdisk_layout(RAMDISK_ADDRESS, len(ramdisk))
    ks = struct.unpack_from('<I', base, 8)[0]
    kernel = base[4096:4096 + ks]
    out = bytearray(base)
    # ARM 3G/1G split needs room for the driver's 256 MiB ioremap plus
    # firmware loading buffers. This preserves the physical reservations.
    tokens = original['command_line'].split()
    need(not any(t.startswith('vmalloc=') and t != 'vmalloc=384M' for t in tokens),
         'conflicting virtual mapping budget')
    if 'vmalloc=384M' not in tokens:
        tokens.append('vmalloc=384M')
    command = ' '.join(tokens).encode()
    need(len(command) < 1536, 'command line too long')
    out[64:576] = command[:512].ljust(512, b'\0')
    out[608:1632] = command[512:].ljust(1024, b'\0')
    struct.pack_into('<II', out, 16, len(ramdisk), RAMDISK_ADDRESS)
    out[576:596] = hashlib.sha1(kernel + struct.pack('<I', ks) + ramdisk + struct.pack('<II', len(ramdisk), 0)).digest()
    out.extend(ramdisk)
    out.extend(bytes(-len(ramdisk) % 4096))
    result, _ = check_combined_image(bytes(out), enforce_bootloader_window=enforce_bootloader_window)
    expected = hashlib.sha256(json.dumps({'version': 1, 'files': {
        n: hashlib.sha256(b).hexdigest() for n, b in sorted(files.items())
    }}, sort_keys=True).encode() + b'\n').hexdigest()
    need(result['firmware_manifest_sha256'] == expected, 'cached ramdisk differs from verified sources')
    return bytes(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('firmware_directory', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--source-manifest', required=True, type=Path)
    parser.add_argument('--ramdisk', type=Path, help='Reuse a verified precompressed archive')
    args = parser.parse_args()
    files = {f.relative_to(args.firmware_directory).as_posix(): f.read_bytes()
             for f in args.firmware_directory.rglob('*') if f.is_file()}
    trusted = {r['file']: r['sha256'] for r in json.loads(args.source_manifest.read_text())
               if r['verified']}
    need(trusted == {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
         'source files differ from device-verified manifest')
    image = combine(args.base.read_bytes(), files,
                    args.ramdisk.read_bytes() if args.ramdisk else None)
    args.output.write_bytes(image)
    result, _ = check_combined_image(image)
    print(json.dumps(result, indent=2))
