"""Summarize a private GNSS capture without emitting coordinates or timestamps."""
import argparse
from collections import Counter
import json
import re
from pathlib import Path


def summarize(text):
    types = Counter()
    checksums = Counter()
    quality = Counter()
    rmc = Counter()
    gsv_visible = None
    sentences = 0
    for line in text.splitlines():
        line = line.removeprefix('NMEA ').strip()
        if not line.startswith('$'):
            continue
        sentences += 1
        body, star, checksum = line[1:].partition('*')
        calculated = 0
        for char in body:
            calculated ^= ord(char)
        if star and re.match(r'^[0-9a-fA-F]{2}', checksum):
            good = calculated == int(checksum[:2], 16)
            checksums['valid' if good else 'invalid'] += 1
        else:
            checksums['missing'] += 1
            good = False
        fields = body.split(',')
        kind = fields[0][-3:]
        types[kind] += 1
        if not good:
            continue
        if kind == 'GGA' and len(fields) > 6:
            quality[fields[6] or 'empty'] += 1
        elif kind == 'RMC' and len(fields) > 2:
            rmc[fields[2] or 'empty'] += 1
        elif kind == 'GSV' and len(fields) > 3 and fields[3].isdigit():
            gsv_visible = max(gsv_visible or 0, int(fields[3]))
    ack = [(int(m, 16), int(result), int(error)) for m, result, error in
           re.findall(r'QMI ACK msg=0x([0-9a-fA-F]+) result=(\d+) error=(\d+)', text)]
    restored = {(int(m, 16), int(value)) for m, value in
                re.findall(r'RESTORE VERIFIED msg=0x([0-9a-fA-F]+) value=(\d+)', text)}
    start_ok = (0x22, 0, 0) in ack
    stop_ok = (0x23, 0, 0) in ack
    valid_gga = sum(n for key, n in quality.items() if key.isdigit() and int(key) > 0)
    return {
        'nmea_sentences': sentences,
        'sentence_types': dict(sorted(types.items())),
        'checksums': dict(checksums),
        'gga_quality_counts': dict(quality),
        'rmc_status_counts': dict(rmc),
        'checksum_valid_gga_with_fix': valid_gga,
        'checksum_valid_rmc_active': rmc['A'],
        'maximum_gsv_satellites_visible': gsv_visible,
        'satellite_reports': len(re.findall(r'^SATELLITE_REPORT ', text, re.M)),
        'qmi_valid_position_reports': len(re.findall(r'^POSITION .*valid_fix=1\b', text, re.M)),
        'loc_start_acknowledged': start_ok,
        'loc_stop_acknowledged': stop_ok,
        'mode4_readback_verified': (0x4b, 4) in restored,
        'lock3_readback_verified': (0x3b, 3) in restored,
        'bounded_session_cleanup_verified': start_ok and stop_ok and (0x4b, 4) in restored and (0x3b, 3) in restored,
        'qmi_failures': [{'message': f'0x{msg:04x}', 'error': error} for msg, result, error in ack if result],
        'coordinates_or_timestamps_in_summary': False,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    output = json.dumps(summarize(args.capture.read_text(errors='replace')), indent=2) + '\n'
    if args.report:
        args.report.write_text(output)
    print(output, end='')
