"""Capture only the pinned KCON127 CDC console; optionally run read-only shell diagnostics.

No port is opened by --selftest. Existing capture_ram_trial.py is unchanged.
"""
import argparse
import datetime
import io
import json
from pathlib import Path
import re
import secrets
from types import SimpleNamespace
import time

VID, PID, USB_SERIAL = 0x0525, 0xA4A7, '76fe1623-KCON127'
ROOT_PROMPT = re.compile(rb'(?:^|\n)root@[^\r\n :]{1,80}:[^\r\n]{0,160}# ?$')
ASKFIRST = b'Please press Enter to activate this console.'


def matching_ports(ports):
    matches = [p for p in ports if p.vid == VID and p.pid == PID
               and p.serial_number == USB_SERIAL]
    if len(matches) > 1:
        raise RuntimeError('Ambiguous KCON127 identity; no port will be opened')
    return matches


def wait_for_port(enumerate_ports, opener, clock, sleep, wait_seconds):
    deadline = clock() + wait_seconds
    attempts, last_error = 0, None
    while clock() < deadline:
        matches = matching_ports(enumerate_ports())
        if matches:
            attempts += 1
            try:
                return opener(matches[0].device), matches[0].device, attempts
            except (OSError, RuntimeError) as error:
                last_error = str(error)
        sleep(min(1.0, max(0, deadline - clock())))
    raise RuntimeError(f'Exact KCON127 did not become usable within {wait_seconds}s; '
                       f'open attempts={attempts}, last_error={last_error}')


class ShellDiagnostics:
    """Bounded allowlist. Never send commands based merely on elapsed time."""
    def __init__(self, enabled, token, backlog_seconds=8):
        self.enabled = enabled
        self.token = token
        self.backlog_seconds = backlog_seconds
        self.tail = b''
        self.activation_sent = False
        self.phase = 'kernel'
        self.pending = None
        self.sent_at = None
        self.kernel_done = False
        self.key_attempts = 0
        self.retry_at = None
        self.blocked = False
        self.actions = []

    def marker(self, label):
        return f'__KCON127_{self.token}_{label}__'.encode()

    def has_line(self, marker):
        return re.search(rb'(?:^|\n)' + re.escape(marker) + rb'\r?(?:\n|$)', self.tail) is not None

    def update(self, data, elapsed):
        self.tail = (self.tail + data)[-16384:]
        if not self.enabled or self.blocked or elapsed < self.backlog_seconds:
            return None
        prompt = ROOT_PROMPT.search(self.tail) is not None
        if self.pending:
            if self.has_line(self.marker(self.pending)) and prompt:
                if self.pending == 'KERNEL_DONE':
                    self.kernel_done = True
                    self.phase = 'key'
                elif self.has_line(self.marker('KEY_PENDING')) and self.key_attempts < 2:
                    self.phase = 'retry'
                    self.retry_at = elapsed + 30
                else:
                    self.phase = 'done'
                self.pending = None
            elif elapsed - self.sent_at >= 20:
                self.blocked = True
                self.actions.append({'elapsed': elapsed, 'action': 'command_response_timeout'})
                return None
            else:
                return None
        if not prompt:
            if not self.activation_sent and ASKFIRST in self.tail:
                self.activation_sent = True
                self.tail = b''
                self.actions.append({'elapsed': elapsed, 'action': 'askfirst_enter'})
                return b'\r'
            return None
        if self.phase == 'retry':
            if elapsed < self.retry_at:
                return None
            self.phase = 'key'
        if self.phase == 'done':
            return None
        if self.phase == 'kernel':
            done = 'KERNEL_DONE'
            command = ("uname -a; cat /proc/consoles; printf '%s\\n' "
                       + self.marker(done).decode())
        else:
            done = 'KEY_DONE'
            self.key_attempts += 1
            command = ("if [ -r /etc/dropbear/dropbear_ed25519_host_key ]; then "
                       "dropbearkey -y -f /etc/dropbear/dropbear_ed25519_host_key; "
                       "else printf '%s\\n' " + self.marker('KEY_PENDING').decode()
                       + "; fi; printf '%s\\n' " + self.marker(done).decode())
        self.pending = done
        self.sent_at = elapsed
        self.tail = b''
        self.actions.append({'elapsed': elapsed, 'action': done,
                             'key_attempt': self.key_attempts})
        return command.encode('ascii') + b'\r'


def capture(port, log, seconds, diagnostics, clock, display):
    start = clock()
    report = {'bytes': 0, 'disconnected': False}
    while clock() - start < seconds:
        try:
            chunk = port.read(65536)
            if chunk:
                log.write(chunk)
                log.flush()
                report['bytes'] += len(chunk)
                display(chunk)
            write = diagnostics.update(chunk, clock() - start)
            if write is not None:
                # ASCII allowlist/newline only; no debug escapes or binary writes.
                if port.write(write) != len(write):
                    raise OSError('short diagnostic serial write')
        except OSError as error:
            report.update(disconnected=True, error=str(error))
            break
    report['capture_elapsed_seconds'] = round(clock() - start, 3)
    report['shell_actions'] = diagnostics.actions
    report['shell_diagnostics_blocked'] = diagnostics.blocked
    return report


def selftest():
    def p(name='COM127', vid=VID, pid=PID, number=USB_SERIAL):
        return SimpleNamespace(device=name, vid=vid, pid=pid, serial_number=number)
    assert matching_ports([p(number='stock'), p(vid=0x1D6B), p()])[0].device == 'COM127'
    assert matching_ports([p(number=None)]) == []
    try:
        matching_ports([p(), p('COM128')])
        raise AssertionError('ambiguous identity accepted')
    except RuntimeError:
        pass
    now = [0.0]
    opened = []
    try:
        wait_for_port(lambda: [p(number='wrong')], lambda x: opened.append(x),
                      lambda: now[0], lambda n: now.__setitem__(0, now[0]+n), 3)
        raise AssertionError('wait did not expire')
    except RuntimeError:
        assert now[0] == 3 and not opened
    d = ShellDiagnostics(True, 'TEST')
    assert d.update(ASKFIRST, 2) is None
    assert d.update(b'', 8) == b'\r'
    assert d.update(b'boot text\n', 9) is None
    command = d.update(b'root@OpenWrt:~# ', 10)
    assert b'uname -a' in command and b'dropbearkey' not in command
    assert d.update(command, 11) is None  # Echoed marker is not completion.
    key = d.update(b'\r\n'+d.marker('KERNEL_DONE')+b'\r\nroot@OpenWrt:~# ', 12)
    assert b'dropbearkey -y -f' in key
    assert d.update(b'\r\n'+d.marker('KEY_PENDING')+b'\r\n'+d.marker('KEY_DONE')
                    +b'\r\nroot@OpenWrt:~# ', 13) is None
    assert d.update(b'', 42) is None
    assert b'dropbearkey' in d.update(b'', 43)
    assert d.update(b'\r\n'+d.marker('KEY_PENDING')+b'\r\n'+d.marker('KEY_DONE')
                    +b'\r\nroot@OpenWrt:~# ', 44) is None
    assert d.update(b'', 100) is None and d.key_attempts == 2
    d = ShellDiagnostics(True, 'TEST', 0)
    assert d.update(b'root@OpenWrt:~# ', 0)
    assert d.update(b'', 20) is None and d.blocked
    assert d.update(b'root@OpenWrt:~# ', 30) is None
    d = ShellDiagnostics(False, 'TEST', 0)
    assert d.update(ASKFIRST+b'\nroot@OpenWrt:~# ', 30) is None
    class FakePort:
        def __init__(self): self.writes = []
        def read(self, size): now[0] += .25; return b'kernel backlog\n'
        def write(self, data): self.writes.append(data); return len(data)
    now[0] = 0
    fake, log = FakePort(), io.BytesIO()
    report = capture(fake, log, 1, d, lambda: now[0], lambda x: None)
    assert report['bytes'] == len(log.getvalue()) and report['bytes'] > 0
    assert not fake.writes and report['capture_elapsed_seconds'] == 1
    print('PASS: exact discovery, ambiguity/timeout, backlog-first prompt gating, '
          'marker echo rejection, bounded public-key retries, response timeout, passive capture')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path)
    ap.add_argument('--wait-seconds', type=int, default=180)
    ap.add_argument('--seconds', type=int, default=180)
    ap.add_argument('--shell-diagnostics', action='store_true',
                    help='After backlog/prompt, send Enter if requested, read kernel info/public host key')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.output or not 1 <= args.wait_seconds <= 180 or not 1 <= args.seconds <= 600:
        ap.error('--output required; wait1..180 seconds; capture1..600 seconds')
    sidecar = args.output.with_suffix('.json')
    if sidecar == args.output or args.output.exists() or sidecar.exists():
        ap.error('output/report must be distinct new files; existing evidence is preserved')
    # Lazy imports: synthetic selftests cannot enumerate/open physical COM ports.
    import serial
    import serial.tools.list_ports
    start = time.monotonic()
    report = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'expected_vid': VID, 'expected_pid': PID, 'expected_serial': USB_SERIAL,
              'wait_seconds': args.wait_seconds, 'requested_capture_seconds': args.seconds,
              'shell_diagnostics_requested': args.shell_diagnostics}
    result = 1
    with args.output.open('xb') as log:
        try:
            def opener(name):
                try:
                    return serial.Serial(name, 115200, timeout=.2, write_timeout=2, exclusive=True)
                except serial.SerialException as error:
                    raise OSError(str(error)) from error
            port, name, attempts = wait_for_port(serial.tools.list_ports.comports, opener,
                                                time.monotonic, time.sleep, args.wait_seconds)
            report.update(port=name, open_attempts=attempts)
            print(f'Capturing exact KCON127 on {name}; backlog first', flush=True)
            with port:
                diag = ShellDiagnostics(args.shell_diagnostics, secrets.token_hex(4))
                report.update(capture(port, log, args.seconds, diag, time.monotonic,
                                      lambda b: print(b.decode(errors='replace'), end='', flush=True)))
            result = 1 if report['disconnected'] else 0
        except (OSError, RuntimeError) as error:
            report['error'] = str(error)
            print(str(error), flush=True)
        except KeyboardInterrupt:
            report['interrupted'] = True
            result = 130
    report['total_elapsed_seconds'] = round(time.monotonic() - start, 3)
    with sidecar.open('x') as out:
        json.dump(report, out, indent=2)
        out.write('\n')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
