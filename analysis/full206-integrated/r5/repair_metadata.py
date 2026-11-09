"""Restore trusted original CPIO metadata without changing any candidate payload."""
from pathlib import Path
import importlib.util
import stat
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'analysis/wlan148-integration'))
import cpio148
spec = importlib.util.spec_from_file_location('cpio_pack', ROOT / 'analysis/bt204-enable-config-bt/package/prepare_archive.py')
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


def original_name(name):
    prefix = 'lib/modules/6.12.112-full206'
    if name == prefix or name.startswith(prefix + '/'):
        return name.replace(prefix, 'lib/modules/6.12.112-bt204', 1)
    return name


def validate_metadata(candidate, baseline):
    expected = {e['name']: e for e in cpio148.parse(baseline)}
    rows = cpio148.parse(candidate)
    critical = {name for name, entry in expected.items()
                if stat.S_ISCHR(entry['fields'][1]) or stat.S_ISBLK(entry['fields'][1])}
    missing = critical - {e['name'] for e in rows}
    if missing:
        raise ValueError('Missing special device: ' + min(missing))
    count = special = times = 0
    for entry in sorted(rows, key=lambda e: (e['name'] not in critical, e['name'])):
        name = entry['name']
        if name == 'TRAILER!!!':
            continue
        original = expected.get(original_name(name))
        if original:
            # Payload and renamed-path sizes/checksum may change; other header fields may not.
            for index in (0, 1, 2, 3, 4, 5, 7, 8, 9, 10):
                if entry['fields'][index] != original['fields'][index]:
                    raise ValueError('Archive metadata mismatch: ' + name + ' field' + str(index))
            count += 1
            special += stat.S_ISCHR(original['fields'][1]) or stat.S_ISBLK(original['fields'][1])
            times += original['fields'][5] != 0
        elif stat.S_ISCHR(entry['fields'][1]) or stat.S_ISBLK(entry['fields'][1]):
            raise ValueError('Unexpected special device: ' + name)
    return {'matching_original_metadata_entries': count, 'special_device_nodes_preserved': special,
            'original_nonzero_timestamps_preserved': times}


def repair(candidate, baseline):
    expected = {e['name']: e for e in cpio148.parse(baseline)}
    rows = cpio148.parse(candidate)
    next_inode = max(e['fields'][0] for e in expected.values()) + 1
    out = []
    for entry in rows:
        name = entry['name']
        if name == 'TRAILER!!!':
            out.append(entry['raw'])
            continue
        original = expected.get(original_name(name))
        if original:
            if entry['fields'][1] != original['fields'][1]:
                raise ValueError('Unexpected mode change: ' + name)
            out.append(pack.new_entry(name, entry['payload'], entry['fields'][1],
                                      original['fields'][0], original))
        else:
            if stat.S_ISCHR(entry['fields'][1]) or stat.S_ISBLK(entry['fields'][1]):
                raise ValueError('Unexpected special device: ' + name)
            # New entries keep explicit candidate metadata and cannot alias an old inode.
            metadata = dict(entry, fields=entry['fields'][:])
            metadata['fields'][0] = next_inode
            next_inode += 1
            out.append(pack.new_entry(name, entry['payload'], entry['fields'][1],
                                      metadata['fields'][0], metadata))
    corrected = b''.join(out)
    corrected += bytes(-len(corrected) % 512)
    before = {e['name']: e['payload'] for e in rows}
    after = {e['name']: e['payload'] for e in cpio148.parse(corrected)}
    if before != after:
        raise ValueError('Metadata repair changed paths or payloads')
    validate_metadata(corrected, baseline)
    return corrected
