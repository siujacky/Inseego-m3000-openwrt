#!/usr/bin/env python3
"""Read-only structural checks for the experimental M3100 RAM image.

Passing does not prove a bootable kernel, functional hardware or bootloader
acceptance. Never use this checker to authorize a flash operation.
"""
import argparse
import hashlib
import json
import pathlib
import struct


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_image(image):
    require(4096 <= len(image) <= 16 * 1024 * 1024, 'image size outside RAM profile limit')
    require(image[:8] == b'ANDROID!', 'invalid Android magic')
    fields = struct.unpack_from('<10I', image, 8)
    size, address, ramdisk, _, second, _, tags, page, version, _ = fields
    require(page == 4096 and not version, 'expected Android v0 / 4096-byte pages')
    require(address == 0x80008000 and tags == 0x82000000, 'unexpected load addresses')
    require(not ramdisk and not second, 'unexpected external ramdisk or second payload')
    require(size >= 104 and len(image) == page + ((size + page - 1) // page) * page,
            'payload size or padding mismatch')
    board = image[48:64].rstrip(b'\0').decode('ascii')
    require(board == 'M3100-RAM', 'wrong board identity')
    command = (image[64:576].split(b'\0', 1)[0] +
               image[608:1632].split(b'\0', 1)[0]).decode('ascii')
    tokens = command.split()
    require(tokens.count('root=/dev/ram0') == 1 and
            sum(t.startswith('root=') for t in tokens) == 1 and
            not any(t.startswith(('ubi.', 'rootfstype=', 'mtdparts=')) for t in tokens),
            'expected RAM root without NAND root arguments')
    require('console=ttyMSM0,115200n8' in tokens, 'missing verified UART console selection')
    payload = image[page:page + size]
    digest = hashlib.sha1(payload + struct.pack('<I', size) + bytes(8)).digest()
    require(image[576:596] == digest, 'Android payload hash mismatch')
    require(not any(image[page + size:]), 'nonzero data after declared payload')
    require(struct.unpack_from('<I', payload, 36)[0] == 0x016f2818, 'missing ARM zImage magic')
    zsize = struct.unpack_from('<I', payload, 44)[0]
    require(64 <= zsize <= size - 40, 'invalid zImage size')
    dtb = payload[zsize:]
    dt = struct.unpack_from('>10I', dtb)
    require(dt[0] == 0xd00dfeed and dt[1] == len(dtb) and dt[5] == 17,
            'missing or malformed appended DTB')
    return {'sha256': hashlib.sha256(image).hexdigest(), 'size': len(image),
            'board_name': board, 'command_line': command, 'kernel_payload_size': size,
            'dtb_size': len(dtb), 'envelope_checks_passed': True,
            'hardware_boot_tested': False}, dtb


def check_boot_image(image):
    """Accept the original small image or the separately addressed firmware form."""
    if len(image) >= 20 and struct.unpack_from('<I', image, 16)[0]:
        from firmware_ramdisk import check_combined_image
        return check_combined_image(image)
    if len(image) >= 4096:
        size = struct.unpack_from('<I', image, 8)[0]
        end = 4096 + ((size + 4095) & ~4095)
        from firmware_trailer import MAGIC, check_trailer
        if image[end:end + 32] == MAGIC:
            return check_trailer(image)
    return check_image(image)


def parse_dtb(blob):
    h = struct.unpack_from('>10I', blob)
    require(h[0] == 0xd00dfeed and h[1] == len(blob), 'invalid DTB length/magic')
    pos, strings = h[2], blob[h[3]:h[3] + h[8]]
    stack, nodes = [], {}
    while True:
        token = struct.unpack_from('>I', blob, pos)[0]
        pos += 4
        if token == 1:
            end = blob.index(0, pos)
            stack.append(blob[pos:end].decode())
            pos = (end + 4) & ~3
            nodes['/'.join(stack)] = {}
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, offset = struct.unpack_from('>II', blob, pos)
            pos += 8
            key = strings[offset:strings.index(0, offset)].decode()
            nodes['/'.join(stack)][key] = blob[pos:pos + length]
            pos = (pos + length + 3) & ~3
        elif token == 9:
            require(not stack, 'unclosed DT nodes')
            return nodes
        else:
            require(token == 4, 'invalid DT token')


def check_reboot_configuration(draft, stock):
    def mapping(nodes):
        matches = [v['qcom,dload-mode'] for v in nodes.values() if 'qcom,dload-mode' in v]
        require(len(matches) == 1 and len(matches[0]) == 8,
                'missing or ambiguous download-mode register mapping')
        phandle, offset = struct.unpack('>II', matches[0])
        controllers = [v for v in nodes.values()
                       if v.get('phandle') == struct.pack('>I', phandle)]
        require(len(controllers) == 1 and len(controllers[0].get('reg', b'')) == 8,
                'invalid download-mode syscon reference')
        address, size = struct.unpack('>II', controllers[0]['reg'])
        require(offset + 4 <= size, 'download-mode register exceeds syscon mapping')
        return address, offset
    require(mapping(draft) == mapping(stock), 'download-mode register differs from stock')
    mode = draft.get('/soc/sram@1468f000/reboot-mode', {})
    require(mode.get('offset') == struct.pack('>I', 0x65c) and
            mode.get('mode-normal') == struct.pack('>I', 0x77665501) and
            mode.get('compatible') == b'syscon-reboot-mode\0',
            'missing stock normal restart reason')


def check_pcie_configuration(draft, stock):
    enabled = [p for p, v in draft.items() if p.rsplit('/', 1)[-1].startswith('pcie@') and
               v.get('status', b'okay\0') != b'disabled\0']
    require(enabled == ['/soc/pcie@1c00000'], 'unexpected PCIe controller')
    root = draft[enabled[0]]
    original = stock['/soc/qcom,pcie@1c00000']
    require(root.get('compatible') == b'qcom,pcie-sdx65\0', 'unexpected PCIe driver')
    require(root.get('num-lanes') == struct.pack('>I', 2),
            'M3100 requires stock-verified two-lane switch link')
    reset = root.get('perst-gpios', b'')
    require(len(reset) == 12 and struct.unpack('>III', reset)[1:] == (57, 1) and
            struct.unpack('>III', original['perst-gpio'])[1] == 57,
            'unexpected PCIe reset GPIO or polarity')
    def sid_map(value):
        require(value and len(value) % 16 == 0, 'invalid PCIe SID map')
        entries = list(struct.iter_unpack('>IIII', value))
        require(len({e[0] for e in entries}) == len(entries), 'duplicate PCIe SID entry')
        return entries
    entries = sid_map(root.get('iommu-map', b''))
    require([(b, s, n) for b, _, s, n in entries] ==
            [(b, s, n) for b, _, s, n in sid_map(original['iommu-map'])],
            'PCIe SID map differs from stock switch topology')
    for _, phandle, _, _ in entries:
        matches = [v for v in draft.values() if
                   v.get('phandle') == struct.pack('>I', phandle)]
        require(len(matches) == 1 and matches[0].get('status', b'okay\0') != b'disabled\0',
                'PCIe SID mapping targets a disabled or missing SMMU')


def check_board(dtb, stock_dtb, *, allow_modem=False, require_staging=False, allow_pcie=False):
    draft, stock = parse_dtb(dtb), parse_dtb(stock_dtb)
    require(draft['']['compatible'].split(b'\0')[0] == b'inseego,m3100', 'wrong DT board')
    for key in ('qcom,board-id', 'qcom,msm-id'):
        require(draft[''][key] == stock[''][key], 'stock ' + key + ' mismatch')
    embedded = draft.get('/chosen', {}).get('bootargs', b'').rstrip(b'\0').decode('ascii')
    arguments = embedded.split()
    require(not any(t.startswith(('ubi.', 'rootfstype=', 'mtdparts=')) or
                    (t.startswith('root=') and t != 'root=/dev/ram0')
                    for t in arguments), 'unsafe bootargs embedded in DT')
    require(draft['/memory']['reg'] == stock['/memory']['reg'], 'stock RAM map mismatch')
    fixed = {v['reg'] for p, v in stock.items()
             if p.startswith('/reserved-memory/') and 'reg' in v}
    actual = {v['reg'] for p, v in draft.items()
              if p.startswith('/reserved-memory/') and 'reg' in v}
    require(fixed <= actual, 'missing fixed stock memory reservation')
    blocked = ('/soc/nand-controller@1b30000', '/soc/dma-controller@1b04000',
               '/soc/remoteproc@4080000', '/soc/ipa@3f40000', '/soc/pcie-ep@1c00000')
    for node in blocked:
        if allow_modem and node == '/soc/remoteproc@4080000':
            modem = draft[node]
            require(modem.get('compatible') == b'qcom,sdx55-mpss-pas\0', 'unexpected modem driver')
            require(modem.get('firmware-name') == b'm3100/modem.mdt\0', 'unexpected modem firmware path')
            region = modem.get('memory-region')
            matches = [v for v in draft.values() if region and v.get('phandle') == region]
            require(len(matches) == 1 and matches[0].get('reg') ==
                    struct.pack('>II', 0x90800000, 0x10000000), 'unexpected modem memory region')
            continue
        require(draft[node].get('status') == b'disabled\0', 'RAM profile enables ' + node)
    if allow_pcie:
        require(not allow_modem, 'peripheral trial must keep modem disabled')
        check_pcie_configuration(draft, stock)
    else:
        require(not any(p.rsplit('/', 1)[-1].startswith('pcie@') and v.get('status', b'okay\0') != b'disabled\0'
                        for p, v in draft.items()), 'RAM profile enables PCIe RC')
    check_reboot_configuration(draft, stock)
    if require_staging:
        pool = draft.get('/reserved-memory/firmware-staging@a1000000', {})
        require(pool.get('reg') == struct.pack('>II', 0xa1000000, 0x09000000) and
                'no-map' in pool, 'missing exact firmware staging reservation')
    return {'board_checks_passed': True, 'fixed_stock_reservations_preserved': len(fixed)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=pathlib.Path)
    parser.add_argument('--stock-dtb', required=True, type=pathlib.Path)
    parser.add_argument('--report', type=pathlib.Path)
    parser.add_argument('--peripherals', action='store_true', help='Validate the modem-disabled M3100 PCIe trial')
    args = parser.parse_args()
    result, appended = check_boot_image(args.image.read_bytes())
    result.update(check_board(appended, args.stock_dtb.read_bytes(),
                              allow_modem=bool(result.get('modem_load_segments')),
                              require_staging=bool(result.get('ram_payload')),
                              allow_pcie=args.peripherals))
    text = json.dumps(result, indent=2) + '\n'
    if args.report:
        args.report.write_text(text)
    print(text, end='')
