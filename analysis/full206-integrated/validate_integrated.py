"""Independent integrated RAM-profile validation; diagnostic profiles stay unchanged."""
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from check_ram_image import check_pcie_configuration, check_reboot_configuration

POOL_START = 0xa1000000
POOL_SIZE = 0x0a000000
DOWNLOAD_BASE = 0xa5733000
EXPECTED_DOWNLOAD_LIMIT = 445435904


def validate_download_placement(image_bytes, bootloader_limit):
    require(bootloader_limit == EXPECTED_DOWNLOAD_LIMIT, 'Changed bootloader allocation limit; old measured base no longer accepted')
    require(DOWNLOAD_BASE + bootloader_limit == 0xc0000000, 'Measured buffer and limit disagree')
    require(4096 < image_bytes <= bootloader_limit and
            POOL_START <= DOWNLOAD_BASE < DOWNLOAD_BASE + image_bytes <= POOL_START + POOL_SIZE,
            'Download exceeds integrated staging reservation')
    return {'fits': True, 'measured_download_base': hex(DOWNLOAD_BASE),
            'download_end': hex(DOWNLOAD_BASE + image_bytes), 'staging_pool_start': hex(POOL_START),
            'staging_pool_size': POOL_SIZE, 'staging_pool_end': hex(POOL_START + POOL_SIZE),
            'headroom_bytes': POOL_START + POOL_SIZE - DOWNLOAD_BASE - image_bytes,
            'base_evidence': 'analysis/ram-scm-bandwidth-serial.txt:81 and ram-full-trailer-fixed-attempt.json'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_nodes(draft, baseline, stock):
    allowed = {
        '': {'model'},
        '/firmware/scm': {'interconnects'},
        '/soc/remoteproc@4080000': {'status', 'firmware-name', 'memory-region'},
        '/soc/ipa@3f40000': {'status', 'firmware-name', 'memory-region', 'qcom,gsi-loader'},
        '/reserved-memory/firmware-staging@a1000000': {'reg', 'no-map'},
        '/soc/i2c@837000': {'status'},
        '/soc/i2c@837000/touchscreen@24': {'compatible', 'reg', 'interrupt-parent', 'interrupts',
                                       'reset-gpios', 'pinctrl-names', 'pinctrl-0'},
        '/soc/pinctrl@f100000/full206-touch-active': {'phandle', 'pins', 'function', 'drive-strength', 'bias-disable'},
    }
    for path in set(draft) | set(baseline):
        require(path in draft, 'Removed baseline node: ' + path)
        if path not in baseline:
            require(path in allowed, 'Unexpected new node: ' + path)
        for name in set(draft.get(path, {})) | set(baseline.get(path, {})):
            if draft.get(path, {}).get(name) != baseline.get(path, {}).get(name):
                require(name in allowed.get(path, set()), 'Unexpected resource delta: ' + path + ':' + name)
    fixed = {p['reg'] for path, p in stock.items() if path.startswith('/reserved-memory/') and 'reg' in p}
    actual = {p['reg'] for path, p in draft.items() if path.startswith('/reserved-memory/') and 'reg' in p}
    require(fixed <= actual, 'Missing fixed stock reservation')
    require(draft['']['compatible'].split(b'\0')[0] == b'inseego,m3100', 'Wrong board')
    for path in ('/soc/nand-controller@1b30000', '/soc/dma-controller@1b04000', '/soc/pcie-ep@1c00000'):
        require(draft[path].get('status') == b'disabled\0', 'Enabled stock storage or endpoint path')
    check_pcie_configuration(draft, stock)
    check_reboot_configuration(draft, stock)
    pool = draft.get('/reserved-memory/firmware-staging@a1000000', {})
    require(pool.get('reg') == struct.pack('>II', POOL_START, POOL_SIZE) and pool.get('no-map') == b'',
            'Firmware staging reservation must match reader')
    require(draft['/memory']['reg'] == struct.pack('>IIII', 0x80000000, 0x20000000, 0xa0000000, 0x20000000),
            'Changed physical RAM banks')
    for region in fixed:
        require(len(region) == 8, 'Unexpected stock reservation cell geometry')
        start, size = struct.unpack('>II', region)
        require(start + size <= POOL_START or start >= POOL_START + POOL_SIZE, 'Staging overlaps stock reservation')

    def phandle(path):
        return draft[path]['phandle']

    scm = draft['/firmware/scm'].get('interconnects')
    require(scm == phandle('/soc/interconnect@1620000') + struct.pack('>I', 10) +
            phandle('/interconnect-mc-virt') + struct.pack('>I', 1), 'Wrong SCM bandwidth path')
    modem = draft['/soc/remoteproc@4080000']
    require(modem.get('status') == b'okay\0' and modem.get('firmware-name') == b'm3100/modem.mdt\0',
            'Modem profile is incomplete')
    require(modem.get('memory-region') == phandle('/reserved-memory/memory@90800000'), 'Wrong modem carveout')
    require(draft['/reserved-memory/memory@90800000']['reg'] == struct.pack('>II', 0x90800000, 0x10000000),
            'Wrong stock modem memory geometry')
    ipa = draft['/soc/ipa@3f40000']
    require(ipa.get('status') == b'okay\0' and ipa.get('firmware-name') == b'm3100/ipa_fws.mdt\0',
            'IPA profile is incomplete')
    require(ipa.get('qcom,gsi-loader') == b'self\0' and 'modem-init' not in ipa, 'Conflicting IPA loaders')
    require(ipa.get('memory-region') == phandle('/reserved-memory/memory@8fced000'), 'Wrong IPA carveout')
    require(draft['/reserved-memory/memory@8fced000']['reg'] == struct.pack('>II', 0x8fced000, 0x10000),
            'Wrong stock IPA memory geometry')
    touch = draft['/soc/i2c@837000/touchscreen@24']
    pins = draft['/soc/pinctrl@f100000/full206-touch-active']
    require(touch.get('compatible') == b'cypress,tt21000\0' and touch.get('reg') == struct.pack('>I', 0x24),
            'Unexpected touch controller')
    require(touch.get('interrupt-parent') == phandle('/soc/pinctrl@f100000') and
            touch.get('interrupts') == struct.pack('>II', 93, 2) and
            touch.get('reset-gpios') == phandle('/soc/pinctrl@f100000') + struct.pack('>II', 92, 1),
            'Unexpected touch pin or polarity')
    require(touch.get('pinctrl-0') == pins['phandle'] and pins.get('pins') == b'gpio92\0gpio93\0' and
            pins.get('function') == b'gpio\0' and pins.get('drive-strength') == struct.pack('>I', 2) and
            pins.get('bias-disable') == b'', 'Unexpected touch pin state')
    return {'passed': True, 'profile': 'full206-integrated-RAM',
            'fixed_stock_reservations_preserved': len(fixed), 'NAND_disabled': True,
            'PCIe_policy_preserved': True, 'MPSS_IPA_staging_SCM_touch_resources_validated': True,
            'hardware_runtime_proven': False}
