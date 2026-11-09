"""Read-only ath11k board-2 TLV inventory and exact stock BDF hash matching."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def tlvs(data):
    offset = 0
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError('truncated TLV header')
        kind, size = struct.unpack_from('<II', data, offset)
        offset += 8
        if offset + size > len(data):
            raise ValueError('truncated TLV payload')
        yield kind, data[offset:offset + size]
        offset += (size + 3) & ~3
    if offset != len(data):
        raise ValueError('truncated TLV padding')


def inventory(data):
    magic = b'QCA-ATH11K-BOARD\0'
    if not data.startswith(magic):
        raise ValueError('not an ath11k board-2 container')
    entries = []
    for outer, payload in tlvs(data[(len(magic) + 3) & ~3:]):
        names = []
        for kind, value in tlvs(payload):
            if kind == 0:
                names.append(value.decode('ascii'))
            elif kind == 1:
                entries.append({'type': 'board' if outer == 0 else 'regdb',
                                'names': names.copy(), 'length': len(value),
                                'sha256': hashlib.sha256(value).hexdigest(),
                                'elf': value.startswith(b'\x7fELF')})
            else:
                raise ValueError('unknown board sub-TLV')
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', type=Path)
    parser.add_argument('--stock-dir', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    entries = inventory(args.board.read_bytes())
    stock = {}
    if args.stock_dir:
        for path in args.stock_dir.glob('bdwlan*'):
            if path.is_file():
                stock.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), []).append(path.name)
    for entry in entries:
        entry['exact_stock_files'] = stock.get(entry['sha256'], [])
    result = {'source': str(args.board),
              'source_sha256': hashlib.sha256(args.board.read_bytes()).hexdigest(),
              'entries': entries,
              'm3100_subsystem_candidates': [e for e in entries if any(
                  'subsystem-vendor=17cb,subsystem-device=0108,' in n for n in e['names'])],
              'exact_stock_matches': [e for e in entries if e['exact_stock_files']]}
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    print(json.dumps({k: v for k, v in result.items() if k != 'entries'}, indent=2))


if __name__ == '__main__':
    main()
