"""Run one command on the unique M3100 RAM USB console and preserve output."""
import argparse
from pathlib import Path
import time
import serial
import serial.tools.list_ports

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('command')
p.add_argument('--seconds', type=float, default=5)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
ports = [x.device for x in serial.tools.list_ports.comports()
         if (x.vid, x.pid) == (0x1d6b, 0x105) and
         (x.serial_number or '').lower() == '76fe1623-ram']
if len(ports) != 1:
    raise RuntimeError('Expected exactly one M3100 RAM USB console')
with serial.Serial(ports[0], 115200, timeout=.2, write_timeout=5) as port:
    port.write(b'\r\n' + a.command.encode() + b'\r\n')
    end = time.monotonic() + a.seconds
    with a.output.open('wb') as log:
        while time.monotonic() < end:
            data = port.read(65536)
            if data:
                log.write(data)
                log.flush()
                print(data.decode(errors='replace'), end='', flush=True)
