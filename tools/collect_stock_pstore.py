"""Wait for this M3100's stock ADB, then preserve pstore and partition hashes.

Never flashes, erases or deletes device files. Mounts pstore if needed.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--seconds', type=int, default=240)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
adb = ['adb', '-s', '76fe1623']
def run(*args, timeout=10):
    return subprocess.run(adb + list(args), capture_output=True, timeout=timeout)

start = time.monotonic()
while time.monotonic() - start < a.seconds:
    try:
        result = run('shell', 'uname -r', timeout=3)
        if result.returncode == 0 and b'5.4.161-perf' in result.stdout:
            break
    except subprocess.TimeoutExpired:
        pass
    time.sleep(3)
else:
    raise RuntimeError('Stock ADB did not return within the collection window')
print('Stock kernel returned; collecting diagnostics', flush=True)
run('root', timeout=15)
for _ in range(30):
    try:
        if b'uid=0(root)' in run('shell', 'id', timeout=3).stdout:
            break
    except subprocess.TimeoutExpired:
        pass
    time.sleep(1)
else:
    raise RuntimeError('Stock root ADB unavailable')
commands = {
    'identity.txt': 'id; uname -a; uptime',
    'pstore-mount.txt': "mount | grep -q ' /sys/fs/pstore ' || mount -t pstore pstore /sys/fs/pstore; ls -l /sys/fs/pstore",
    'reset.txt': 'cat /sys/kernel/debug/tzdbg/reset; dmesg | head -80',
    'boot-recovery-hashes.txt': 'sha256sum /dev/mtd17 /dev/mtd23',
}
for filename, command in commands.items():
    result = run('shell', command, timeout=60)
    (a.output / filename).write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError('Collection command failed: ' + filename)
pull = run('pull', '/sys/fs/pstore', str(a.output / 'pstore'), timeout=60)
(a.output / 'pull.txt').write_bytes(pull.stdout + pull.stderr)
files = list((a.output / 'pstore').glob('*'))
report = {'stock_return_verified': True,
          'elapsed_seconds': round(time.monotonic() - start, 1),
          'pstore_pull_returncode': pull.returncode,
          'pstore_files': [{'name': f.name, 'bytes': f.stat().st_size} for f in files if f.is_file()]}
(a.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2), flush=True)
