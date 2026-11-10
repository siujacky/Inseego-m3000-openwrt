"""Host QEMU validation, with native commands simulated and no hardware access."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/home/dell/m3100-openwrt/trials/full206-public-v1-qemu-root')
HERE = Path(__file__).resolve().parent


def run(args, expected=0):
    def isolate():
        os.chroot(ROOT)
        os.chdir('/')
    env = dict(os.environ, PYTHONPATH='/opt/m3100-webui/server:/opt/m3100-touchscreen:/verification',
               PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run(['/usr/bin/qemu-arm-static'] + args, env=env,
                            preexec_fn=isolate, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = result.stdout.decode('utf-8', 'replace')
    print(output, end='', flush=True)
    if result.returncode != expected:
        raise RuntimeError('ARM validation failed: ' + str(args) + ': ' + str(result.returncode))
    return output


def main():
    if os.geteuid() != 0 or not (ROOT / 'usr/bin/qemu-arm-static').exists():
        raise RuntimeError('Use the prepared isolated root under host root')
    run(['/usr/bin/python3', '-B', '/verification/arm_smoke.py'])
    for script in ['etc/init.d/m3100-modem', 'etc/init.d/m3100-usb',
                   'usr/libexec/full206-network-start', 'usr/libexec/full206-ui-ready']:
        run(['/bin/busybox', 'sh', '-n', '/' + script])
    # The following are explicit synthetic fixtures, never image inputs.
    board = ROOT / 'tmp/sysinfo/board_name'
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text('inseego,m3100\n')
    (ROOT / 'sys/class/remoteproc').mkdir(parents=True, exist_ok=True)
    gate = ROOT / 'etc/full206/modem-autostart'
    saved = gate.read_bytes()
    # Without binfmt registration, every ARM subprocess also needs explicit QEMU.
    # This cat wrapper reads the actual fixture files through the actual ARM applet.
    command = 'cat() { /usr/bin/qemu-arm-static /bin/busybox cat "$@"; }; procd_open_instance() { exit 99; }; . /etc/init.d/m3100-modem; start_service'
    try:
        assert saved == b'0\n'
        run(['/bin/busybox', 'sh', '-c', command])
        gate.unlink()
        run(['/bin/busybox', 'sh', '-c', command])
        gate.write_text('1\n')
        run(['/bin/busybox', 'sh', '-c', command], expected=99)
    finally:
        gate.write_bytes(saved)
        gate.chmod(0o600)
    print('ARM modem gate: disabled/missing hold; explicit 1 reaches stub only', flush=True)
    run(['/usr/bin/python3', '-B', '-m', 'unittest'] + [
        'touchscreen.tests.test_' + name for name in
        ['api', 'catalog', 'controller', 'input', 'keyboard', 'lcd', 'render', 'touchscreen_backend', 'ui_sweep']])
    print(json.dumps({'public_admin_authentication_smoke': True, 'selected_touchscreen_tests': 148,
                      'modem_gate_behavior': True, 'changed_shell_scripts_syntax': True,
                      'physical_boot_tested': False, 'native_commands_simulated': True}))


if __name__ == '__main__':
    main()
