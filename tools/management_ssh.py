"""Run a command over USB management SSH using the locally pinned host key.

Host keys change after a RAM reboot. Verify the new key before updating the
ignored analysis/keys/known_hosts file; this tool never auto-accepts a new key.
"""
from pathlib import Path
import argparse
import sys
import paramiko


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=30,
                        help='Command I/O timeout in seconds (1..360, default 30)')
    parser.add_argument('command', nargs='?', default='uname -a; uptime')
    args = parser.parse_args()
    if not 1 <= args.timeout <= 360:
        parser.error('--timeout must be between 1 and 360 seconds')
    root = Path(__file__).resolve().parents[1]
    client = paramiko.SSHClient()
    client.load_host_keys(str(root / 'analysis/keys/known_hosts'))
    try:
        client.connect('192.168.77.1', username='root',
                       key_filename=str(root / 'analysis/keys/m3100-management'),
                       timeout=10, auth_timeout=10, banner_timeout=10,
                       allow_agent=False, look_for_keys=False)
        _, stdout, stderr = client.exec_command(
            args.command, timeout=args.timeout)
        sys.stdout.buffer.write(stdout.read())
        sys.stderr.buffer.write(stderr.read())
        return stdout.channel.recv_exit_status()
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
