"""Continuously preserve USB serial output across one RAM boot or crash."""
import argparse
import datetime
import json
from pathlib import Path
import time
import serial
import serial.tools.list_ports

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--seconds', type=int, default=360)
p.add_argument('--append', action='store_true')
p.add_argument('--peripherals', action='store_true', help='Capture PCIe/sensor diagnostics instead of modem progress')
p.add_argument('--hold-modem', action='store_true', help='Stop the deferred modem service as soon as USB appears; leave startup to a later explicit command')
a = p.parse_args()
start = time.monotonic()
port = None
for _ in range(90):
    ports = [x.device for x in serial.tools.list_ports.comports()
             if x.vid == 0x1d6b and x.pid == 0x105
             and (x.serial_number or '').lower() == '76fe1623-ram']
    if len(ports) == 1:
        try:
            port = serial.Serial(ports[0], 115200, timeout=.25, write_timeout=2)
            break
        except (serial.SerialException, OSError) as error:
            # Windows can advertise a COM device before it is openable, or
            # retain the old entry briefly during USB re-enumeration.
            print('Waiting for usable serial: ' + str(error), flush=True)
    time.sleep(2)
else:
    raise RuntimeError('M3100 RAM serial did not enumerate')

report = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'port': ports[0], 'disconnected': False, 'bytes': 0}
commands = {
    10: 'ls -l /dev/watchdog* /sys/class/watchdog/; for w in /sys/class/watchdog/watchdog*; do echo "$w"; cat "$w/identity" "$w/state" "$w/timeout" "$w/bootstatus" 2>/dev/null; done; ps w | grep -E "procd|watchdog"; dmesg | grep -i -E "watchdog|wdt|bark|bite"; echo __WATCHDOG_AUDIT__',
    3: 'echo "8 4 1 7" > /proc/sys/kernel/printk; cat /proc/consoles; dropbearkey -y -f /etc/dropbear/dropbear_ed25519_host_key; uname -r; echo __CAPTURE_KERNEL__',
    45: 'dropbearkey -y -f /etc/dropbear/dropbear_ed25519_host_key; echo __CAPTURE_HOSTKEY__',
    100: 'find /lib/firmware/m3100 -type f 2>/dev/null | wc -l; logread | grep -E "m3100|payload"; echo __FW_PROGRESS__',
    170: 'ls /tmp/m3100-firmware*; tail -2 /tmp/m3100-firmware-check.log; cat /sys/class/remoteproc/remoteproc0/state; echo __FW_STATE__',
    240: 'cat /sys/class/remoteproc/remoteproc0/state; dmesg | tail -30; echo __LATE_STATE__',
}
if a.peripherals:
    commands = {
        3: 'echo "8 4 1 7" > /proc/sys/kernel/printk; uname -a; cat /proc/mtd; dmesg; echo __PERIPHERAL_KERNEL__',
        20: 'for p in /sys/bus/pci/devices/*; do echo "$p"; cat "$p/vendor" "$p/device" "$p/class" 2>/dev/null; readlink "$p/driver"; done; echo __PCI_DEVICES__',
        45: 'dropbearkey -y -f /etc/dropbear/dropbear_ed25519_host_key; echo __CAPTURE_HOSTKEY__',
        65: 'cat /sys/kernel/debug/devices_deferred; cat /sys/kernel/debug/regulator/regulator_summary; cat /sys/kernel/debug/gpio; echo __PERIPHERAL_POWER__',
        90: 'for h in /sys/class/hwmon/hwmon*; do cat "$h/name" "$h"/temp*_label "$h"/temp*_input 2>/dev/null; done; ip addr; dmesg | tail -50; echo __PERIPHERAL_STATE__',
    }
# First-boot key generation/service startup can outlast the early sample.
if a.hold_modem:
    commands[3] = '/etc/init.d/m3100-modem stop; echo __MODEM_SERVICE_HELD__; ' + commands[3]
    commands[45] = '/etc/init.d/m3100-modem stop; ' + commands[45]
commands[110] = 'dropbearkey -y -f /etc/dropbear/dropbear_ed25519_host_key; echo __LATE_CAPTURE_HOSTKEY__'
with port, a.output.open('ab' if a.append else 'wb') as log:
    attached = time.monotonic()
    port.write(b'\r\n')
    print('Capturing ' + ports[0], flush=True)
    while time.monotonic() - attached < a.seconds:
        try:
            chunk = port.read(65536)
            if chunk:
                log.write(chunk)
                log.flush()
                report['bytes'] += len(chunk)
                print(chunk.decode(errors='replace'), end='', flush=True)
            for at, command in list(commands.items()):
                if time.monotonic() - attached >= at:
                    port.write(command.encode() + b'\r\n')
                    del commands[at]
        except serial.SerialException as error:
            report['disconnected'] = True
            report['error'] = str(error)
            print('\nSerial disconnected: ' + str(error), flush=True)
            break
report['elapsed_seconds'] = round(time.monotonic() - start, 1)
a.output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
