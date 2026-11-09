"""Focused read-only validation of the M3100 manual HCI candidate rootfs."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'target-arm_cortex-a7+neon-vfpv4_musl_eabi'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--openwrt-tree', type=Path, default=Path('//wsl.localhost/Ubuntu-24.04/home/dell/m3100-openwrt/openwrt'))
parser.add_argument('--report', type=Path, default=ROOT / 'analysis/bt-hci-build-validation.json')
args = parser.parse_args()
tree = args.openwrt_tree
rootfs = tree / 'build_dir' / TARGET / 'root-sdx'
kernel = tree / 'build_dir' / TARGET / 'linux-sdx_generic/linux-6.12.112'
config = (kernel / '.config').read_text()
required = ['CONFIG_BT=m', 'CONFIG_BT_QCA=m', 'CONFIG_BT_HCIUART=m',
            'CONFIG_BT_HCIUART_QCA=y', 'CONFIG_BT_HCIUART_H4=y',
            'CONFIG_BT_HCIUART_SERDEV=y', 'CONFIG_SERIAL_DEV_BUS=y',
            'CONFIG_SERIAL_DEV_CTRL_TTYPORT=y', 'CONFIG_REGMAP_SPMI=y',
            'CONFIG_PINCTRL_QCOM_SPMI_PMIC=y', 'CONFIG_REGULATOR_QCOM_RPMH=y']
for line in required:
    assert line in config.splitlines(), line
files = []
for name in ('hci_uart.ko', 'btqca.ko'):
    path = rootfs / 'usr/lib/m3100-bt-manual' / name
    data = path.read_bytes()
    assert data[:4] == b'\x7fELF' and int.from_bytes(data[18:20], 'little') == 40
    assert not list((rootfs / 'lib/modules').rglob(name)), 'transport in automatic module path'
    if name == 'hci_uart.ko':
        assert b'inseego,m3100-qca6490-bt' in data, 'missing custom serdev match'
    files.append({'path': str(path.relative_to(rootfs)), 'bytes': len(data),
                  'sha256': hashlib.sha256(data).hexdigest()})
for directory in ('etc/modules.d', 'etc/modules-boot.d'):
    for path in (rootfs / directory).glob('*'):
        if path.is_file():
            assert 'hci_uart' not in path.read_text(), 'HCI autoload entry: ' + str(path)
manifest = json.loads((ROOT / 'analysis/bt-firmware-candidates/manifest.json').read_text())
for name in ('wcnhpbtfw21.tlv', 'wcnhpnv21g.bin'):
    entry = next(e for e in manifest if e['file'] == name)
    path = rootfs / 'lib/firmware/qca' / name
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == entry['sha256'], 'firmware hash mismatch: ' + name
    files.append({'path': str(path.relative_to(rootfs)), 'bytes': path.stat().st_size,
                  'sha256': digest})
for name in ('LICENSE.QualcommAtheros_ath10k', 'NOTICE.qca'):
    assert (rootfs / 'usr/share/licenses/m3100-bt-firmware' / name).is_file()
for name in ('btmgmt', 'btmon'):
    path = rootfs / 'usr/bin' / name
    data = path.read_bytes()
    assert data[:4] == b'\x7fELF' and int.from_bytes(data[18:20], 'little') == 40
    files.append({'path': str(path.relative_to(rootfs)), 'bytes': len(data),
                  'sha256': hashlib.sha256(data).hexdigest()})
before = (ROOT / 'analysis/bt-hci-build-config.before').read_text()
current = (tree / '.config').read_text()
assert '# CONFIG_PACKAGE_bluez-daemon is not set' in current.splitlines()
for directory in ('etc/init.d', 'etc/rc.d'):
    for path in (rootfs / directory).glob('*'):
        assert 'bluetooth' not in path.name.lower() and 'bluez' not in path.name.lower(), 'BT daemon startup path'
for name in ('etc/config/bluetooth', 'usr/bin/bluetoothd', 'usr/lib/bluetooth/bluetoothd'):
    assert not (rootfs / name).exists(), 'unexpected daemon or profile: ' + name
for directory in ('var/lib/bluetooth', 'etc/bluetooth/keys'):
    assert not any(p.is_file() for p in (rootfs / directory).rglob('*')), 'unexpected saved BT pairing/profile'
selected = lambda text: {line for line in text.splitlines()
                         if line.startswith('CONFIG_') and line.endswith(('=y', '=m'))}
removed = sorted(selected(before) - selected(current))
assert not removed, 'previous selections removed: ' + repr(removed)
result = {'focused_checks_passed': True, 'required_kernel_config': required,
          'manual_transport_location': True, 'hci_autoload_entries': [],
          'previous_selected_options_removed': removed, 'files': files,
          'bluetooth_daemon_selected': False, 'bluetooth_startup_scripts': [],
          'saved_bluetooth_profiles': [],
          'hardware_tested': False, 'serdev_fragment_requires_explicit_repack_include': True}
args.report.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
