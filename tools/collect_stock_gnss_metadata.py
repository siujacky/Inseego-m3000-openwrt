"""Bounded read-only stock GNSS/RMTFS metadata capture; no device file uploads.

Run --dry-run to inspect all serial-bound ADB requests without contacting ADB.
Captures device/sysfs/process metadata and numeric config flags, never NV/EFS
contents, device-memory bytes, location output, process arguments or environment.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

SERIAL = '76fe1623'
STOCK_RELEASE = '5.4.161-perf'
PER_COMMAND_SECONDS = 6
TOTAL_SECONDS = 55
MAX_OUTPUT_BYTES = 16384

QUERIES = (
    ('processes_and_open_device_metadata', r'''
for p in /proc/[0-9]*; do
    [ -r "$p/comm" ] || continue
    n=$(cat "$p/comm" 2>/dev/null)
    case "$n" in rmt*|rfs*|rfsa*|efs*|loc_launcher|gpsd|modem2d)
        echo "PID=${p##*/} NAME=$n"
        readlink "$p/exe"
        for f in "$p"/fd/*; do
            t=$(readlink "$f" 2>/dev/null)
            case "$t" in /dev/uio*|/dev/mtd*|/dev/rmtfs*|socket:*) echo "FD=${f##*/} TARGET=$t";; esac
        done
    ;; esac
done
'''),
    ('uio_metadata', r'''
ls -l /dev/uio* /dev/rmtfs* 2>/dev/null
for u in /sys/class/uio/uio*; do
    [ -d "$u" ] || continue
    echo "$u"
    for a in name version maps/map0/addr maps/map0/size; do
        [ -r "$u/$a" ] || continue
        echo "ATTRIBUTE=$a"
        cat "$u/$a"
    done
    readlink -f "$u/device/driver"
done
'''),
    ('rmtfs_metadata', r'''
for u in /sys/class/rmtfs/*; do
    [ -d "$u" ] || continue
    echo "$u"
    for a in client_id phys_addr size; do
        [ -r "$u/$a" ] || continue
        echo "ATTRIBUTE=$a"
        cat "$u/$a"
    done
done
'''),
    ('init_order_and_matching_script_names', r'''
for d in /etc/rcS.d /etc/rc2.d /etc/rc3.d /etc/rc4.d /etc/rc5.d /etc/rc.d; do
    [ -d "$d" ] || continue
    ls -l "$d" | grep -Ei 'rmt|rfs|rfsa|efs|qmi|loc|gps|modem'
done
for f in /etc/init.d/*; do
    [ -f "$f" ] || continue
    grep -l -E 'rmt_storage|rmtfs|rfs_access|rfsa|loc_launcher|gpsd' "$f" 2>/dev/null
done
'''),
    ('driver_status_metadata', r'''
dmesg | grep -E "Device created for client|Shared mem alloc client|hyp_assign_phys.*(failed|not supported)|msm_sharedmem.*(failed|error)" | tail -50
'''),
    ('rf_mode_read_query', r'''
if command -v read_atcmd >/dev/null 2>&1; then
    read_atcmd 'AT+CFUN?' 2>/dev/null | grep -E '^\+CFUN:|^OK$|^ERROR$'
else
    echo 'read_atcmd unavailable; RF operating mode not queried'
fi
'''),
    ('numeric_gnss_config_flags', r'''
for f in /etc/gps.conf /etc/loc.conf /etc/izat.conf; do
    [ -f "$f" ] || continue
    echo "CONFIG=$f"
    grep -E '^[[:space:]]*(GPS_LOCK|CAPABILITIES|SUPL_MODE|NMEA_PROVIDER|LPP_PROFILE|EXTERNAL_DR_ENABLED)[[:space:]]*=[[:space:]]*(0[xX][0-9a-fA-F]+|[0-9]+)[[:space:]]*(#.*)?$' "$f" | sed 's/#.*//'
done
'''),
    ('storage_and_router_topology', r'''
cat /proc/mtd
grep -E 'efs|rmt' /proc/mounts
ls /sys/kernel/debug/ipc_router 2>/dev/null
ls /proc/net/qrtr 2>/dev/null
'''),
)


def request(adb, command, timeout):
    started = time.monotonic()
    try:
        result = subprocess.run([adb, '-s', SERIAL, 'shell', command],
                                capture_output=True, timeout=timeout, check=False)
        out, err = result.stdout, result.stderr
        return {'returncode': result.returncode,
                'elapsed_seconds': round(time.monotonic() - started, 3),
                'stdout': out[:MAX_OUTPUT_BYTES].decode(errors='replace'),
                'stderr': err[:2048].decode(errors='replace'),
                'output_truncated': len(out) > MAX_OUTPUT_BYTES}
    except subprocess.TimeoutExpired:
        # Drop partial output: it is incomplete and cannot prove command success.
        return {'timed_out': True, 'elapsed_seconds': round(time.monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', default=shutil.which('adb'))
    parser.add_argument('--output', type=Path, default=Path('analysis/stock-gnss-metadata.json'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({'serial': SERIAL, 'stock_release_guard': STOCK_RELEASE,
                          'maximum_total_seconds': TOTAL_SECONDS,
                          'per_command_timeout_seconds': PER_COMMAND_SECONDS,
                          'identity_command': 'uname -r',
                          'queries': [{'name': name, 'shell': command.strip()} for name, command in QUERIES]}, indent=2))
        return 0
    if not args.adb:
        parser.error('ADB executable unavailable; specify --adb with the trusted platform-tools executable')
    deadline = time.monotonic() + TOTAL_SECONDS
    identity = request(args.adb, 'uname -r', PER_COMMAND_SECONDS)
    capture = {'serial': SERIAL, 'device_mutations': False,
               'private_nv_or_memory_bytes_read': False,
               'identity': identity, 'queries': []}
    if identity.get('returncode') != 0 or identity.get('stdout', '').strip() != STOCK_RELEASE:
        capture['stopped_reason'] = 'Stock-release guard failed; no further device queries executed'
    else:
        for name, command in QUERIES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                capture['stopped_reason'] = 'Total time budget exhausted'
                break
            result = request(args.adb, command, min(PER_COMMAND_SECONDS, remaining))
            capture['queries'].append({'name': name, **result})
        capture['complete'] = len(capture['queries']) == len(QUERIES)
        capture['timeouts'] = sum(bool(result.get('timed_out')) for result in capture['queries'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(capture, indent=2) + '\n')
    print(f'Metadata capture saved to {args.output}; queries={len(capture["queries"])}; no NV/memory contents read')
    return 0 if capture.get('complete') and not capture.get('timeouts') else 1


if __name__ == '__main__':
    raise SystemExit(main())
