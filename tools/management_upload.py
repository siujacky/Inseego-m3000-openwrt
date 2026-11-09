"""Copy a file to /tmp over the pinned USB SSH link and verify SHA256."""
import hashlib
from pathlib import Path, PurePosixPath
import shlex
import sys
import paramiko

root = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1])
destination = PurePosixPath(sys.argv[2])
if not destination.is_absolute() or destination.parent != PurePosixPath('/tmp'):
    raise ValueError('Upload destination must be a direct child of /tmp')
data = source.read_bytes()
client = paramiko.SSHClient()
client.load_host_keys(str(root / 'analysis/keys/known_hosts'))
try:
    client.connect('192.168.77.1', username='root',
                   key_filename=str(root / 'analysis/keys/m3100-management'),
                   timeout=10, allow_agent=False, look_for_keys=False)
    remote = shlex.quote(str(destination))
    stdin, stdout, stderr = client.exec_command('umask 077; cat > ' + remote, timeout=30)
    stdin.channel.sendall(data)
    stdin.channel.shutdown_write()
    if stdout.channel.recv_exit_status():
        raise RuntimeError(stderr.read().decode())
    _, stdout, stderr = client.exec_command('sha256sum ' + remote, timeout=30)
    digest = stdout.read().decode().split()[0]
    if stdout.channel.recv_exit_status() or digest != hashlib.sha256(data).hexdigest():
        raise RuntimeError('Remote upload hash mismatch')
    print(digest + '  ' + str(destination))
finally:
    client.close()
