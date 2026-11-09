"""Create an isolated Linux chroot from image bytes; never run init or hardware code."""
from pathlib import Path, PurePosixPath
import io
import lzma
import os
import shutil
import stat
import sys
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEST = Path('/home/dell/m3100-openwrt/trials/full206-feelgood-v1-qemu-root')
sys.path.insert(0, str(ROOT / 'analysis/wlan148-integration'))
import cpio148


def main():
    if os.geteuid() != 0 or DEST.exists():
        raise RuntimeError('Use root and a new isolated smoke root')
    DEST.mkdir(mode=0o700)
    entries = {}
    for entry in cpio148.parse((HERE / 'bootstrap.cpio').read_bytes()):
        if entry['name'] == 'TRAILER!!!':
            continue
        entries[entry['name']] = (entry['fields'][1], entry['payload'])
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress((HERE / 'userspace.tar.xz').read_bytes())), mode='r:') as archive:
        for member in archive:
            mode = (stat.S_IFDIR if member.isdir() else stat.S_IFLNK if member.issym() else stat.S_IFREG) | member.mode
            entries[member.name] = (mode, archive.extractfile(member).read() if member.isfile() else member.linkname.encode() if member.issym() else b'')
    symlinks = []
    for name, (mode, data) in entries.items():
        if name.startswith('/') or '..' in PurePosixPath(name).parts:
            raise RuntimeError('Unsafe chroot member')
        path = DEST / name
        if stat.S_ISLNK(mode):
            symlinks.append((path, data.rstrip(b'\0').decode()))
        elif stat.S_ISDIR(mode):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(stat.S_IMODE(mode))
        elif stat.S_ISREG(mode):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(stat.S_IMODE(mode))
        # Device nodes from the image are verified separately, not recreated on the host.
    for path, link in symlinks:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(link)
    for name, minor in [('null', 3), ('zero', 5), ('random', 8), ('urandom', 9)]:
        path = DEST / 'dev' / name
        if not path.exists():
            os.mknod(path, stat.S_IFCHR | 0o666, os.makedev(1, minor))
    (DEST / 'dev/shm').mkdir(mode=0o1777, exist_ok=True)
    shutil.copyfile('/usr/bin/qemu-arm-static', DEST / 'usr/bin/qemu-arm-static')
    (DEST / 'usr/bin/qemu-arm-static').chmod(0o755)
    verify = DEST / 'verification'
    verify.mkdir()
    shutil.copyfile(HERE / 'arm_smoke.py', verify / 'arm_smoke.py')
    touch = verify / 'touchscreen'
    touch.mkdir()
    shutil.copytree(ROOT / 'touchscreen/tests', touch / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'touchscreen/tools', touch / 'tools', ignore=shutil.ignore_patterns('__pycache__'))
    (touch / '__init__.py').write_text('')
    (touch / 'backend').symlink_to('/opt/m3100-webui/server')
    (touch / 'm3100_touch').symlink_to('/opt/m3100-touchscreen/m3100_touch')
    (touch / 'runtime').mkdir()
    shutil.copyfile(DEST / 'usr/libexec/m3100-hardware.py', touch / 'runtime/hardware.py')
    shutil.copyfile(DEST / 'etc/init.d/m3100-touchscreen', touch / 'runtime/m3100-touchscreen.init')
    # Explicit test fixtures, not claims about a live device or emulated kernel.
    boot = DEST / 'proc/sys/kernel/random/boot_id'
    boot.parent.mkdir(parents=True)
    boot.write_text('00000000-0000-4000-8000-000000000001\n')
    reference = verify / 'analysis/full206-integrated/m3000-screen-reference'
    reference.mkdir(parents=True)
    shutil.copyfile(ROOT / 'analysis/full206-integrated/m3000-screen-reference/qml-recovery.json', reference / 'qml-recovery.json')
    routing = verify / 'analysis/full206-integrated/r15'
    routing.mkdir(parents=True)
    shutil.copyfile(ROOT / 'analysis/full206-integrated/r15/touch-current-routing.json', routing / 'touch-current-routing.json')
    print('Isolated ARM test root prepared:', DEST)


if __name__ == '__main__':
    main()
