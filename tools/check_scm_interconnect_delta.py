"""Require two compiled DTBs to differ only in the intended SCM ICC property."""
import argparse
import json
import struct
from pathlib import Path


def parse(path):
    data = Path(path).read_bytes()
    header = struct.unpack_from('>10I', data)
    if header[0] != 0xd00dfeed or header[1] > len(data):
        raise ValueError('invalid FDT header')
    _, _, off, strings_off, reserve_off, _, _, _, strings_size, structure_size = header
    strings = data[strings_off:strings_off + strings_size]
    end = off + structure_size
    nodes, stack = {}, []
    while off < end:
        token = struct.unpack_from('>I', data, off)[0]
        off += 4
        if token == 1:
            nul = data.index(b'\0', off, end)
            stack.append(data[off:nul].decode())
            off = (nul + 4) & ~3
            nodes['/' + '/'.join(stack[1:])] = {}
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, nameoff = struct.unpack_from('>II', data, off)
            off += 8
            name = strings[nameoff:strings.index(b'\0', nameoff)].decode()
            nodes['/' + '/'.join(stack[1:])][name] = data[off:off + length]
            off = (off + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            break
        else:
            raise ValueError(f'invalid FDT token {token}')
    reservations = []
    while True:
        address, size = struct.unpack_from('>QQ', data, reserve_off)
        reserve_off += 16
        if address == size == 0:
            break
        reservations.append((address, size))
    return nodes, reservations


def check(before_path, after_path):
    before, before_reservations = parse(before_path)
    after, after_reservations = parse(after_path)
    diffs = []
    for path in sorted(set(before) | set(after)):
        if path not in before or path not in after:
            diffs.append(path + ' [node]')
            continue
        for key in sorted(set(before[path]) | set(after[path])):
            if before[path].get(key) != after[path].get(key):
                diffs.append(path + ':' + key)
    target = '/firmware/scm'
    actual = after[target].get('interconnects', b'')
    system = struct.unpack('>I', after['/soc/interconnect@1620000']['phandle'])[0]
    mc = struct.unpack('>I', after['/interconnect-mc-virt']['phandle'])[0]
    expected = struct.pack('>4I', system, 10, mc, 1)
    passed = (diffs == [target + ':interconnects'] and actual == expected
              and 'interconnects' not in before[target]
              and before_reservations == after_reservations)
    return {'passed': passed, 'differences': diffs,
            'reserved_map_unchanged': before_reservations == after_reservations,
            'expected_provider_local_indexes': {'system_noc_MASTER_CRYPTO': 10,
                                                'mc_virt_SLAVE_EBI1': 1},
            'interconnect_property_matches': actual == expected}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before_dtb')
    parser.add_argument('after_dtb')
    args = parser.parse_args()
    result = check(args.before_dtb, args.after_dtb)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
