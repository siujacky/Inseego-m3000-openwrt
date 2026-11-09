"""Targeted regular-file replacement retaining every other CPIO byte/metadata."""
from pathlib import Path
import importlib.util
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'analysis/wlan148-integration'))
import cpio148
spec = importlib.util.spec_from_file_location('r14_archive_writer', ROOT / 'analysis/bt204-enable-config-bt/package/prepare_archive.py')
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


def replace_payloads(archive, replacements):
    entries = cpio148.parse(archive)
    names = {e['name'] for e in entries}
    if not replacements or not set(replacements) <= names - {'TRAILER!!!'}:
        raise ValueError('Replacement paths must exist and exclude the trailer')
    rows = []
    for entry in entries:
        name, fields = entry['name'], entry['fields']
        if name not in replacements:
            rows.append(entry['raw'])
            continue
        if not stat.S_ISREG(fields[1]) or fields[4] != 1:
            raise ValueError('Replacement must be a single-link regular file: ' + name)
        rows.append(pack.new_entry(name, replacements[name], fields[1], fields[0], entry))
    result = b''.join(rows)
    result += bytes(-len(result) % 512)
    checked = cpio148.parse(result)
    assert [e['name'] for e in checked] == [e['name'] for e in entries]
    for old, new in zip(entries, checked):
        if old['name'] not in replacements:
            assert old['raw'] == new['raw']
        else:
            assert new['payload'] == replacements[old['name']]
            assert all(old['fields'][i] == new['fields'][i] for i in range(13) if i not in (6, 12))
    return result


def replace_manifest_digest(manifest, path, old_digest, new_digest, expected_count):
    if not all(isinstance(x, str) and re.fullmatch('[0-9a-f]{64}', x) for x in (old_digest, new_digest)):
        raise ValueError('Invalid digest')
    lines = manifest.splitlines(keepends=True)
    if len(lines) != expected_count:
        raise ValueError('Unexpected manifest count')
    found, seen, output = False, set(), []
    for line in lines:
        match = re.fullmatch(rb'([0-9a-f]{64})  (/[^\s]+)\n', line)
        if not match:
            raise ValueError('Noncanonical manifest line')
        digest, name = match.groups()
        if name in seen:
            raise ValueError('Duplicate manifest path')
        seen.add(name)
        if name == path.encode():
            if digest.decode() != old_digest:
                raise ValueError('Previous digest mismatch')
            line = new_digest.encode() + line[64:]
            found = True
        output.append(line)
    if not found:
        raise ValueError('Missing manifest path')
    return b''.join(output)
