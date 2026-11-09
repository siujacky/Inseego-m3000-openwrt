"""Native OpenWrt command boundary. No vendor binaries, shell, or remote transport.

All configuration uses typed ubus UCI requests, never interpolated UCI shell text.
One lock covers staging/commit/reload to prevent interleaved HTTP writers.
"""
import glob
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import signal
import math

from ..errors import ApiError, unavailable
from .system import SystemInfo


class NativeSystemInfo(SystemInfo):
    """Native numeric telemetry keeps absent/corrupt sources distinct from zero.

    Stock SystemInfo behavior is retained for the stock adapters.
    """
    def _text(self, path):
        try:
            with open(self.p(path), 'r', encoding='utf-8') as f:
                value = f.read(65537)
            return value if len(value) <= 65536 else None
        except (OSError, UnicodeError):
            return None

    def uptime(self):
        try:
            value = float((self._text('/proc/uptime') or '').split()[0])
            return value if math.isfinite(value) and value >= 0 else None
        except (ValueError, IndexError):
            return None

    def loadavg(self):
        try:
            fields = (self._text('/proc/loadavg') or '').split()[:3]
            values = [float(value) for value in fields]
            return values if len(values) == 3 and all(math.isfinite(x) and x >= 0 for x in values) else None
        except ValueError:
            return None

    def meminfo(self):
        values = {}
        for line in (self._text('/proc/meminfo') or '').splitlines():
            match = re.fullmatch(r'(\w+):\s+(\d{1,20})\s+kB\s*', line)
            if match:
                values[match.group(1)] = int(match.group(2)) * 1024
        return {'total': values.get('MemTotal'), 'available': values.get('MemAvailable', values.get('MemFree')),
                'free': values.get('MemFree'), 'swap_total': values.get('SwapTotal'), 'swap_free': values.get('SwapFree')}

    def dhcp_leases(self):
        for path in ('/tmp/dhcp.leases', '/tmp/dnsmasq.leases', '/var/run/data/dnsmasq.leases',
                     '/data/dnsmasq.leases', '/var/lib/misc/dnsmasq.leases'):
            content = self._text(path)
            if content is None:
                continue
            leases = []
            for line in content.splitlines():
                fields = line.split()
                if not fields or fields[0] == 'duid':
                    continue
                if len(fields) < 4 or not re.fullmatch(r'\d{1,20}', fields[0]):
                    return None
                if not re.fullmatch(r'[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}', fields[1]):
                    return None
                leases.append({'expires': int(fields[0]), 'mac': fields[1], 'ip': fields[2],
                               'hostname': '' if fields[3] == '*' else fields[3]})
            return leases  # an existing readable empty file is a measured zero leases
        return None


class _WindowsProcessGroup:
    """Assign the suspended child to a Job before its code can spawn descendants."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes as w
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
                ('CreateJobObjectW', [w.LPVOID, w.LPCWSTR], w.HANDLE),
                ('AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
                ('TerminateJobObject', [w.HANDLE, w.UINT], w.BOOL),
                ('CloseHandle', [w.HANDLE], w.BOOL),
                ('CreateToolhelp32Snapshot', [w.DWORD, w.DWORD], w.HANDLE),
                ('OpenThread', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
                ('ResumeThread', [w.HANDLE], w.DWORD)):
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError('Cannot create command job')
        try:
            if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise OSError('Cannot assign command job')
            class Entry(ctypes.Structure):
                _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('thread_id', w.DWORD),
                            ('owner_pid', w.DWORD), ('priority', w.LONG), ('delta', w.LONG), ('flags', w.DWORD)]
            for name in ('Thread32First', 'Thread32Next'):
                fn = getattr(self.api, name)
                fn.argtypes, fn.restype = [w.HANDLE, ctypes.POINTER(Entry)], w.BOOL
            snapshot = self.api.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
            if snapshot in (None, ctypes.c_void_p(-1).value):
                raise OSError('Cannot enumerate suspended command thread')
            resumed = False
            try:
                entry = Entry()
                entry.dwSize = ctypes.sizeof(entry)
                found = self.api.Thread32First(snapshot, ctypes.byref(entry))
                while found:
                    if entry.owner_pid == process.pid:
                        thread = self.api.OpenThread(0x2, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
                        if not thread:
                            raise OSError('Cannot open suspended command thread')
                        try:
                            if self.api.ResumeThread(thread) == 0xffffffff:
                                raise OSError('Cannot resume command thread')
                            resumed = True
                        finally:
                            self.api.CloseHandle(thread)
                        break
                    found = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            finally:
                self.api.CloseHandle(snapshot)
            if not resumed:
                raise OSError('Suspended command thread not found')
        except Exception:
            self.kill()
            self.close()
            raise

    def kill(self):
        self.api.TerminateJobObject(self.handle, 1)

    def close(self):
        self.api.CloseHandle(self.handle)


class NativePlatform:
    def __init__(self, cfg):
        if cfg.get('mock') or cfg.get('transport', 'local') != 'local':
            raise ValueError('OpenWrt mode requires real local adapters')
        if not cfg.get('password_file') or not os.path.isabs(cfg['password_file']):
            raise ValueError('OpenWrt requires an absolute password_file')
        self.cfg = cfg
        self.lock = threading.RLock()
        self.system = NativeSystemInfo()
        self.password()  # fail closed at startup, before opening a listener

    def password(self):
        try:
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
            fd = os.open(self.cfg['password_file'], flags)
            with os.fdopen(fd, 'r', encoding='utf-8') as f:
                info = os.fstat(f.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('not a regular file')
                if os.name != 'nt' and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077):
                    raise ValueError('must be root-owned and private')
                password = f.read(1025).rstrip('\r\n')
            if not password or len(password) > 1024 or '\n' in password or '\r' in password or '\x00' in password:
                raise ValueError('invalid password file')
            return password
        except (OSError, ValueError, UnicodeError):
            raise unavailable('Native admin password file is missing, invalid, or not private')

    def _execute(self, argv):
        """One monotonic deadline covers parent exit AND pipe EOF from descendants.

        The pipe is unbuffered and owned/closed solely by its reader thread.
        POSIX sessions and Windows Jobs terminate the complete managed group.
        """
        limit = min(1048576, max(1024, int(self.cfg.get('native_max_bytes', 262144))))
        timeout = min(30, max(1, float(self.cfg.get('native_timeout_s', 10))))
        deadline = time.monotonic() + timeout
        windows_group = None
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, bufsize=0,
                                 start_new_session=os.name != 'nt',
                                 creationflags=0x4 if os.name == 'nt' else 0,  # CREATE_SUSPENDED
                                 env=dict(os.environ, LC_ALL='C',
                                 PATH='/usr/sbin:/usr/bin:/sbin:/bin'))
        except OSError:
            raise unavailable('Native executable unavailable: ' + os.path.basename(argv[0]))
        if os.name == 'nt':
            try:
                windows_group = _WindowsProcessGroup(p)
            except OSError:
                p.kill()
                p.wait(timeout=1)
                p.stdout.close()  # no reader exists yet
                raise ApiError(502, 'native_process_group', 'Cannot isolate native command process group')
        def kill_tree():
            if windows_group:
                windows_group.kill()
            else:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        chunks, exceeded, finished, read_failed = [], threading.Event(), threading.Event(), threading.Event()
        def drain():
            size = 0
            try:
                while True:
                    chunk = p.stdout.read(4096)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        exceeded.set()
                        kill_tree()
                        break
                    chunks.append(chunk)
            except OSError:
                read_failed.set()
            finally:
                p.stdout.close()
                finished.set()
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            if not finished.wait(max(0, deadline - time.monotonic())):
                raise subprocess.TimeoutExpired(argv, timeout)
            p.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            kill_tree()
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            reader.join(timeout=1)  # never close a descriptor from under a blocked reader
            raise ApiError(504, 'native_timeout', 'Native command timed out')
        finally:
            if windows_group:
                windows_group.close()
        if exceeded.is_set():
            raise ApiError(502, 'native_output_limit', 'Native command exceeded output limit')
        if read_failed.is_set():
            raise ApiError(502, 'native_read_failed', 'Native command output could not be read')
        if p.returncode != 0:
            # stdout/argv can contain a Wi-Fi or APN secret; never send them to HTTP or logs.
            raise ApiError(502, 'native_command_failed', 'Native command failed',
                           {'program': os.path.basename(argv[0]), 'rc': p.returncode})
        return b''.join(chunks).decode('utf-8', 'replace').strip()

    def command(self, argv):
        allowed = ('ubus', 'wifi', 'ip', 'logread', 'uqmi', 'bluetoothctl')
        firewall = argv in (['/sbin/fw4', 'check'], ['/usr/sbin/nft', '-j', 'list', 'table', 'inet', 'fw4'])
        helper = self.cfg.get('modem_helper')
        init = argv[0].startswith('/etc/init.d/') if argv else False
        if (not argv or not all(isinstance(x, str) and '\x00' not in x for x in argv)
                or len(argv) > 32 or sum(len(x) for x in argv) > 32768
                or (argv[0] not in allowed and not firewall and not (helper and argv[0] == helper) and not init)):
            raise ApiError(403, 'native_command_refused', 'Native command is not allowed')
        if init and (not re.fullmatch(r'/etc/init.d/[A-Za-z0-9_-]{1,64}', argv[0])
                     or argv[0].split('/')[-1] not in self.cfg.get('native_services', [])
                     or len(argv) != 2 or argv[1] not in ('start', 'stop', 'restart', 'reload')):
            raise ApiError(403, 'native_command_refused', 'Service action is not allowed')
        output = self._execute(argv)
        if not output:
            return {}
        try:
            result = json.loads(output)
        except ValueError:
            if argv[0] in ('ubus', 'uqmi') or (helper and argv[0] == helper):
                raise ApiError(502, 'native_bad_response', 'Native command returned invalid JSON')
            return output
        qmi_data_state = argv[0] == 'uqmi' and '--get-data-status' in argv and result in ('connected', 'disconnected') if isinstance(result, str) else False
        if (argv[0] == 'uqmi' and isinstance(result, str) and not qmi_data_state) or (isinstance(result, dict) and result.get('error')):
            raise ApiError(502, 'native_command_failed', 'Native service rejected the operation')
        return result

    def ubus(self, obj, method, payload=None):
        return self.command(['ubus', 'call', obj, method, json.dumps(payload or {}, separators=(',', ':'))])

    def config(self, package):
        result = self.ubus('uci', 'get', {'config': package})
        if not isinstance(result, dict) or not isinstance(result.get('values'), dict):
            raise ApiError(502, 'native_bad_response', 'UCI returned invalid configuration')
        return result['values']

    def configure(self, package, sections, reload_argv, create_type=None, delete=False):
        with self.lock:
            try:
                for section, values in sections:
                    if create_type:
                        self.ubus('uci', 'add', {'config': package, 'type': create_type, 'name': section, 'values': values})
                    elif delete:
                        self.ubus('uci', 'delete', {'config': package, 'section': section})
                    elif values:
                        self.ubus('uci', 'set', {'config': package, 'section': section, 'values': values})
                self.ubus('uci', 'commit', {'config': package})
            except ApiError:
                try:
                    self.ubus('uci', 'revert', {'config': package})
                except ApiError:
                    pass
                raise
            try:
                self.command(reload_argv)
            except ApiError:
                raise ApiError(502, 'native_apply_failed', 'Configuration saved, but reload failed; inspect runtime state',
                               {'saved': True, 'applied': False})

    def qmi(self, *args):
        device = self.cfg.get('qmi_device', '')
        if not re.fullmatch(r'/dev/cdc-wdm[0-9]+', device) or not os.path.exists(device):
            raise unavailable('No configured USB QMI device; integrated QRTR requires a native modem helper')
        return self.command(['uqmi', '-d', device] + list(args))

    def modem(self, action='status', *args):
        helper = self.cfg.get('modem_helper', '')
        if helper:
            if not os.path.isabs(helper) or not os.path.isfile(helper):
                raise unavailable('Configured native modem helper is unavailable')
            result = self.command([helper, action] + list(args))
            if not isinstance(result, dict):
                raise ApiError(502, 'native_bad_response', 'Native modem helper must return an object')
            if action == 'sms-list' and (not isinstance(result.get('messages'), list)
                                         or not all(isinstance(item, dict) for item in result['messages'])):
                raise ApiError(502, 'native_bad_response', 'Native SMS read must return a messages list')
            if action not in ('status', 'sms-list') and result.get('requested') is not True:
                raise ApiError(502, 'native_command_failed', 'Native modem helper did not accept the request')
            return result
        if action == 'status':
            pin = self.qmi('--get-pin-status')
            serving = self.qmi('--get-serving-system')
            state = self.qmi('--get-data-status')
            return {'available': True, 'transport': 'usb-qmi',
                    'capabilities': {'status': True, 'connect': False, 'mode': False, 'sms': False},
                    'sim': {'status': 'ready' if pin.get('pin1_status') in ('disabled', 'verified') else 'unknown'},
                    'signal': self.qmi('--get-signal-info'),
                    'service': {'status': serving.get('registration', 'unknown'), 'operator': serving.get('plmn_description', ''),
                                'roaming': serving.get('roaming', False)}, 'connection': {'state': state}}
        raise unavailable('This modem action requires a native modem helper; data interfaces can be controlled through netifd')

    def gnss_status(self):
        """A dead producer cannot keep an old fix current by leaving a file behind."""
        try:
            with open('/run/m3100-gnss.json', 'r', encoding='utf-8') as f:
                content = f.read(65537)
            if len(content) > 65536:
                raise ValueError()
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError()
        except (OSError, ValueError):
            raise unavailable('GNSS service has not published a valid status file')
        timestamp = result.get('timestamp')
        try:
            valid_timestamp = isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and math.isfinite(timestamp)
        except OverflowError:
            valid_timestamp = False
        if not valid_timestamp:
            result['timestamp'] = None  # do not serialize NaN/Infinity or an unbounded integer
        age = time.time() - timestamp if valid_timestamp else None
        fresh = age is not None and 0 <= age <= 30
        result.update({'age_s': round(age, 3) if age is not None else None,
                       'fresh': fresh, 'fix_valid': fresh and result.get('fix_valid') is True,
                       'freshness': 'current' if fresh else 'missing_timestamp' if age is None else 'future_timestamp' if age < 0 else 'stale'})
        return result

    def gnss_capability(self):
        try:
            data = self.gnss_status()
            return {'available': data['fresh'], 'data': data,
                    'reason': 'Fresh GNSS observation; inspect fix_valid' if data['fresh'] else 'GNSS observation is not current'}
        except ApiError as error:
            return {'available': False, 'reason': error.message, 'code': error.code}

    def hardware_status(self):
        """Read only ROOT's bounded producer file, never a caller-selected/device path.

        This M3100 integration exposes last-ADC voltage and a custom LCD driver.
        A fresh producer timestamp does not establish ADC sample age or physical
        display/touch validation. Expired reports cannot retain current readings.
        """
        try:
            with open('/run/m3100-hardware.json', 'rb') as f:
                content = f.read(65537)
            if len(content) > 65536:
                raise ValueError()
            source = json.loads(content)
            if not isinstance(source, dict):
                raise ValueError()
        except (OSError, ValueError, RecursionError):
            raise unavailable('Hardware producer has not published a valid status file')

        def number(value, minimum=None):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            try:
                if not math.isfinite(value) or minimum is not None and value < minimum:
                    return None
            except OverflowError:
                return None
            return value

        def part(name):
            value = source.get(name)
            return value if isinstance(value, dict) else {}

        def names(value):
            if not isinstance(value, list):
                return []
            return [item for item in value[:128] if isinstance(item, str) and 0 < len(item) <= 128
                    and all(ord(c) >= 32 for c in item)]

        timestamp = number(source.get('timestamp'))
        age = time.time() - timestamp if timestamp is not None else None
        fresh = age is not None and 0 <= age <= 30
        battery, display, touch, modem, ipa = [part(key) for key in ('battery', 'display', 'touch', 'modem', 'ipa')]
        voltage = number(battery.get('voltage_uv'), 1) if fresh and battery.get('source') == 'pm7250b-last-adc' else None
        registered = display.get('registered') if fresh and display.get('device') == '/dev/m3100-lcd' else None
        events = touch.get('events_seen')
        events = events if fresh and isinstance(events, int) and not isinstance(events, bool) and 0 <= events <= 9007199254740991 else None
        remoteproc = modem.get('remoteproc_state')
        remoteproc = remoteproc if fresh and isinstance(remoteproc, str) and len(remoteproc) <= 64 else None
        return {'timestamp': timestamp, 'age_s': round(age, 3) if age is not None else None, 'fresh': fresh,
                'freshness': 'current' if fresh else 'missing_timestamp' if age is None else 'future_timestamp' if age < 0 else 'stale',
                'battery': {'voltage_uv': voltage, 'source': 'pm7250b-last-adc',
                            'sample_age_s': number(battery.get('sample_age_s'), 0) if fresh else None,
                            'capacity_percent': None, 'charging': None, 'voltage_only': True},
                'display': {'device': '/dev/m3100-lcd',
                            'registered': registered if isinstance(registered, bool) else None,
                            'last_frame_written': number(display.get('last_frame_written'), 0) if fresh else None,
                            'render_ok': display.get('render_ok') if fresh and isinstance(display.get('render_ok'), bool) else None,
                            'physical_confirmed': False},
                'touch': {'event_devices': names(touch.get('event_devices')) if fresh else [],
                          'events_seen': events, 'last_event_timestamp': number(touch.get('last_event_timestamp'), 0) if fresh else None,
                          'physical_confirmed': False},
                'modem': {'remoteproc_state': remoteproc}, 'ipa': {'interfaces': names(ipa.get('interfaces')) if fresh else []},
                'notes': ['Last ADC voltage does not establish capacity, charging state, or ADC sample age.',
                          'Driver registration, frame write success and input events do not establish physical visual/touch proof.']}

    def hardware_capabilities(self):
        try:
            data = self.hardware_status()
        except ApiError as error:
            return {name: {'available': False, 'reason': error.message, 'code': error.code}
                    for name in ('display', 'touch', 'power')}
        fresh = data['fresh']
        expired = 'Hardware producer report is not current'
        return {
            'display': {'available': fresh and data['display']['registered'] is True, 'device': '/dev/m3100-lcd',
                        'physical_confirmed': False, 'data': data['display'],
                        'reason': 'Custom LCD registration reported; frame writes do not prove a visible image' if fresh else expired},
            'touch': {'available': fresh and bool(data['touch']['event_devices']), 'physical_confirmed': False,
                      'data': data['touch'], 'reason': 'Input device/events reported; physical touch validation remains unconfirmed' if fresh else expired},
            'power': {'available': fresh and data['battery']['voltage_uv'] is not None, 'voltage_only': True,
                      'data': data['battery'], 'reason': 'Last ADC voltage only; capacity/charging and ADC sample age are unavailable' if fresh else expired}}

    def capabilities(self):
        def probe(fn):
            try:
                return {'available': True, 'data': fn()}
            except ApiError as e:
                return {'available': False, 'reason': e.message, 'code': e.code}
        output = {'system': probe(lambda: self.ubus('system', 'board')),
                  'network': probe(lambda: self.ubus('network.interface', 'dump')),
                  'wifi': probe(lambda: self.ubus('network.wireless', 'status')),
                  'cellular': probe(self.modem),
                  'services': probe(lambda: self.ubus('service', 'list'))}
        cellular = output['cellular']
        if cellular['available'] and cellular['data'].get('available') is not True:
            cellular.update({'available': False, 'reason': cellular['data'].get('reason', 'Native modem transport is unavailable')})
        # Empty wireless status means no driver registered, not a working radio.
        if output['wifi']['available'] and not output['wifi']['data']:
            output['wifi'] = {'available': False, 'reason': 'No wireless radio is registered with netifd'}
        for name, pattern in (('usb', '/sys/class/udc/*'), ('pcie', '/sys/bus/pci/devices/*'),
                              ('ethernet', '/sys/class/net/eth*'), ('bluetooth', '/sys/class/bluetooth/hci*')):
            nodes = glob.glob(pattern)
            output[name] = {'available': bool(nodes), 'nodes': nodes,
                            'reason': 'Kernel nodes detected; functionality requires runtime validation' if nodes else 'No kernel node detected'}
        output.update(self.hardware_capabilities())
        output['gnss'] = self.gnss_capability()
        return output

    def run(self, *args, **kwargs):
        raise unavailable('Vendor CLI is unavailable in native OpenWrt mode')

    def get(self, path):
        if path == 'DeviceInfo':
            board = self.ubus('system', 'board')
            return {'DeviceInfoModelNumber': board.get('model', ''),
                    'DeviceInfoSwVersion': board.get('release', {}).get('description', ''),
                    'DeviceInfoHwVersion': board.get('board_name', '')}
        raise unavailable('Vendor REST is unavailable in native OpenWrt mode')


def build(cfg):
    from . import Adapters
    native = NativePlatform(cfg)
    bundle = Adapters(native, native, native.system)
    bundle.native = native
    return bundle
