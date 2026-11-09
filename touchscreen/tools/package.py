#!/usr/bin/env python3
"""Build a closed, reproducible userspace package; never include server snapshots."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

spec = importlib.util.spec_from_file_location('touch_installer', Path(__file__).with_name('install.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
SOURCES = installer.SOURCES


def build(source, destination):
    source = Path(source).resolve()
    payloads = {}
    entries = []
    for name, (target, mode) in sorted(SOURCES.items()):
        path = source / name
        if not path.is_file() or path.is_symlink() or source not in path.resolve().parents:
            raise ValueError('missing or unsafe required source: ' + name)
        data = path.read_bytes()
        if len(data) > installer.MAX_FILE:
            raise ValueError('source file exceeds bound')
        payloads[name] = data
        entries.append(dict(source=name, destination=target, mode=mode, size=len(data),
                            sha256=hashlib.sha256(data).hexdigest()))
    payloads['manifest.json'] = (json.dumps({'version': 1, 'files': entries}, sort_keys=True,
                                          indent=2) + '\n').encode()
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, filename='', mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted(payloads.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o644 if name == 'manifest.json' else SOURCES[name][1]
                info.mtime = 0
                info.uid = info.gid = 0
                archive.addfile(info, io.BytesIO(data))
    installer.atomic(Path(destination), buffer.getvalue(), 0o644)
    installer.validate_archive(destination)
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.source, args.output) + '  ' + str(args.output))


if __name__ == '__main__':
    main()
