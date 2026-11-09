"""Make a deterministic RAM-upload archive from pinned public WCN6855 firmware.

No device operation. Entries install under /lib/firmware via tar -xJf FILE -C
/lib/firmware; hw2.1 is the same relative symlink used by OpenWrt's recipe.
"""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'analysis/network-wifi-firmware'
OUTPUT = ROOT / 'analysis/network-wifi-firmware.tar.xz'
BASE_URL = 'https://gitlab.com/kernel-firmware/linux-firmware/-/raw/20260221/'
files = sorted(p for p in SOURCE.rglob('*') if p.is_file())
required = ['amss.bin', 'board-2.bin', 'm3.bin', 'regdb.bin']
folder = SOURCE / 'ath11k/WCN6855/hw2.0'
assert all((folder / name).is_file() for name in required)
assert (folder / 'amss.bin').read_bytes().startswith(b'\x7fELF')
assert (folder / 'board-2.bin').read_bytes().startswith(b'QCA-ATH11K-BOARD\0')
assert (SOURCE / 'ath11k/WCN6855/LICENCE.atheros_firmware').is_file()
manifest = []
with tarfile.open(OUTPUT, 'w:xz') as archive:
    for path in files:
        relative = path.relative_to(SOURCE).as_posix()
        info = archive.gettarinfo(str(path), arcname=relative)
        info.uid = info.gid = info.mtime = 0
        info.uname = info.gname = ''
        info.mode = 0o644
        with path.open('rb') as stream:
            archive.addfile(info, stream)
        source_name = 'LICENCE.atheros_firmware' if path.name.startswith('LICENCE') else relative
        manifest.append({'path': relative, 'bytes': path.stat().st_size,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'source_url': BASE_URL + source_name})
    link = tarfile.TarInfo('ath11k/WCN6855/hw2.1')
    link.type = tarfile.SYMTYPE
    link.linkname = 'hw2.0'
    link.mode = 0o777
    archive.addfile(link)
result = {'release': 'linux-firmware-20260221', 'files': manifest,
          'archive': str(OUTPUT), 'archive_bytes': OUTPUT.stat().st_size,
          'archive_sha256': hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
          'symlink': {'ath11k/WCN6855/hw2.1': 'hw2.0'},
          'device_installation_performed': False, 'hardware_verified': False}
(ROOT / 'analysis/network-wifi-firmware-manifest.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
