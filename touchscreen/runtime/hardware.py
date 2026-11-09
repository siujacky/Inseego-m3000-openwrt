#!/usr/bin/python3
"""Publish observed hardware state; a successful write is not physical proof."""
import glob
import json
import math
import os
from pathlib import Path
import select
import struct
import time


def valid_ui_status(value, boot_id, now, max_age=15):
    """Only a bounded, same-boot monotonic observation can attest a UI write."""
    unknown = {'owner': 'touchscreen', 'render_ok': False, 'last_frame_written': None}
    if not isinstance(value, dict) or value.get('boot_id') != boot_id:
        return unknown
    if type(value.get('pid')) is not int or value['pid'] <= 0 or value.get('render_ok') is not True:
        return unknown
    for key in ('monotonic', 'last_frame_monotonic', 'timestamp', 'last_frame_written'):
        item = value.get(key)
        if type(item) not in (int, float) or not math.isfinite(item):
            return unknown
    if not (0 <= now - value['monotonic'] <= max_age and
            0 <= now - value['last_frame_monotonic'] <= max_age and
            value['last_frame_monotonic'] <= value['monotonic']):
        return unknown
    return dict(unknown, render_ok=True, last_frame_written=value['last_frame_written'])


def read_ui_status(path, boot_id, now):
    try:
        with open(path, 'rb') as stream:
            data = stream.read(16385)
        if len(data) > 16384:
            raise ValueError('oversized status')
        value = json.loads(data)
        result = valid_ui_status(value, boot_id, now)
        if result['render_ok']:
            os.kill(value['pid'], 0)
        return result
    except (OSError, ValueError, TypeError):
        return valid_ui_status(None, boot_id, now)


def display_cycle(lock_path, status_path, boot_id, write_static, now=None):
    """Serialize complete static frames with the UI's lifetime flock."""
    import fcntl
    now = time.monotonic() if now is None else now
    failed = {'owner': 'hardware', 'render_ok': False, 'last_frame_written': None}
    try:
        with open(lock_path, 'a+b') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return read_ui_status(status_path, boot_id, now)
            write_static()
            return dict(failed, render_ok=True, last_frame_written=time.time())
    except OSError:
        return failed


def text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def voltage(paths):
    for path in paths:
        try:
            value = int(text(path))
            if 2000000 <= value <= 5000000:
                return value
        except (TypeError, ValueError):
            pass
    return None


def atomic(path, value):
    temp = path + '.tmp'
    with open(temp, 'w') as stream:
        json.dump(value, stream, allow_nan=False)
        stream.write('\n')
    os.replace(temp, path)


def main():
    from lcd_render import render
    touch = {}
    events = 0
    last_event = None
    last_frame = None
    render_ok = False
    boot_id = text('/proc/sys/kernel/random/boot_id')
    while True:
        devices = []
        for device in glob.glob('/sys/class/input/event*'):
            name = text(device + '/device/name') or ''
            if 'cypress' not in name.lower() and 'cyttsp' not in name.lower():
                continue
            devices.append(name)
            path = '/dev/input/' + Path(device).name
            if path not in touch:
                try:
                    touch[path] = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                except OSError:
                    pass
        ready = select.select(list(touch.values()), [], [], 0)[0]
        for fd in ready:
            try:
                data = os.read(fd, struct.calcsize('llHHI') * 128)
                count = len(data) // struct.calcsize('llHHI')
                events += count
                if count:
                    last_event = time.time()
            except OSError:
                pass
        battery = voltage(glob.glob('/sys/bus/platform/devices/*/voltage_now_uv') +
                          glob.glob('/sys/bus/platform/drivers/m3100-qg-telemetry/*/voltage_now_uv'))
        registered = Path('/dev/m3100-lcd').exists()
        owner = 'hardware'
        last_frame = None
        render_ok = False
        if registered:
            def write_static():
                frame = render('192.168.77.1', 'UNKNOWN' if battery is None else '%.3f V' % (battery/1e6), 'RAM WEBUI :8088')
                with open('/dev/m3100-lcd', 'wb', buffering=0) as stream:
                    if stream.write(b'frame\n') != 6 or stream.write(frame) != len(frame):
                        raise OSError('incomplete frame')
            display = display_cycle('/run/m3100-display.lock', '/run/m3100-touchscreen.json',
                                    boot_id, write_static)
            last_frame = display['last_frame_written']
            render_ok = display['render_ok']
            owner = display['owner']
        modem = [text(p) for p in glob.glob('/sys/class/remoteproc/remoteproc*/state')]
        atomic('/run/m3100-hardware.json', {
            'timestamp': time.time(),
            'battery': {'voltage_uv': battery, 'source': 'pm7250b-last-adc', 'sample_age_s': None,
                        'capacity_percent': None, 'charging': None},
            'display': {'device': '/dev/m3100-lcd', 'registered': registered,
                        'last_frame_written': last_frame, 'render_ok': render_ok, 'owner': owner,
                        'physical_confirmed': False},
            'touch': {'event_devices': devices, 'events_seen': events, 'last_event_timestamp': last_event},
            'modem': {'remoteproc_state': modem[0] if len(modem) == 1 else 'unknown'},
            'ipa': {'interfaces': [Path(p).name for p in glob.glob('/sys/class/net/*')
                                    if Path(p).name.startswith(('rmnet', 'wwan', 'ipa'))]}})
        time.sleep(5)


if __name__ == '__main__':
    main()
