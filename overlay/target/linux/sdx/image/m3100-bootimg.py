#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""M3100 experimental RAM Android boot header v0; no external ramdisk, DTB appended to zImage."""
import hashlib
import pathlib
import struct
import sys

def bootloader_node_order(blob):
    """Keep aliases/chosen ahead of memory edits in ABL's cached FDT walk."""
    header = struct.unpack_from('>10I', blob)
    pos, depth, prefix_end = header[2], 0, None
    children = []
    while True:
        begin = pos
        token, = struct.unpack_from('>I', blob, pos)
        pos += 4
        if token == 1:
            end = blob.index(0, pos)
            if depth == 1:
                if prefix_end is None:
                    prefix_end = begin
                current = (blob[pos:end].decode('ascii'), begin)
            depth += 1
            pos = (end + 4) & ~3
        elif token == 2:
            depth -= 1
            if depth == 1:
                children.append((current[0], blob[current[1]:pos]))
            elif depth == 0:
                tail = blob[begin:header[2] + header[9]]
                break
        elif token == 3:
            length, = struct.unpack_from('>I', blob, pos)
            pos = (pos + 8 + length + 3) & ~3
        elif token != 4:
            raise ValueError('Invalid FDT token')
    first = ('aliases', 'chosen')
    assert all(sum(name == wanted for name, _ in children) == 1 for wanted in first)
    ordered = [chunk for wanted in first for name, chunk in children if name == wanted]
    ordered += [chunk for name, chunk in children if name not in first]
    structure = blob[header[2]:prefix_end] + b''.join(ordered) + tail
    assert len(structure) == header[9]
    result = blob[:header[2]] + structure + blob[header[2] + header[9]:]
    assert len(result) == len(blob)
    return result

source, destination, cmdline = sys.argv[1:]
kernel = pathlib.Path(source).read_bytes()
assert struct.unpack_from('<I', kernel, 0x24)[0] == 0x016f2818
zsize = struct.unpack_from('<I', kernel, 0x2c)[0]
assert struct.unpack_from('>I', kernel, zsize)[0] == 0xd00dfeed
assert zsize + struct.unpack_from('>I', kernel, zsize + 4)[0] == len(kernel)
kernel = kernel[:zsize] + bootloader_node_order(kernel[zsize:])
command = cmdline.encode('ascii')
assert len(command) < 1536
header = bytearray(4096)
header[:8] = b'ANDROID!'
struct.pack_into('<10I', header, 8, len(kernel), 0x80008000, 0, 0x81100000,
                 0, 0x80f00000, 0x82000000, 4096, 0, 0)
header[48:64] = b'M3100-RAM'.ljust(16, b'\0')
header[64:576] = command[:512].ljust(512, b'\0')
header[608:1632] = command[512:].ljust(1024, b'\0')
header[576:596] = hashlib.sha1(kernel + struct.pack('<I', len(kernel)) + bytes(8)).digest()
image = header + kernel + bytes((-len(kernel)) % 4096)
assert len(image) <= 16 * 1024 * 1024
pathlib.Path(destination).write_bytes(image)
