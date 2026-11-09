"""Verify R15's one-line LCD fix and otherwise unchanged R14 payload/ABI."""
from pathlib import Path
import hashlib
import importlib.util
import json
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
KERNEL = Path('/home/dell/m3100-openwrt/trials/full206-r15-linux-6.12.112')
sys.path.insert(0, str(ROOT / 'analysis/wlan148-integration'))
sys.path.append('/mnt/c/Users/Dell/AppData/Roaming/Python/Python313/site-packages')
import cpio148
spec = importlib.util.spec_from_file_location('r15_pack', ROOT / 'analysis/bt204-enable-config-bt/package/prepare_archive.py')
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)
sha = lambda data: hashlib.sha256(data).hexdigest()


def verify_lineage(cpio, composition):
    parent_cpio = (HERE.parent / 'r14/full206.cpio').read_bytes()
    assert sha(parent_cpio) == '4748443709a35b56f52af1c19fda3787fceaf1e852ba446b9ac0f56eaeaf1c23'
    before, after = cpio148.parse(parent_cpio), cpio148.parse(cpio)
    assert [e['name'] for e in before] == [e['name'] for e in after]
    target = 'usr/libexec/m3100-firmware-load'
    changed = []
    for old, new in zip(before, after):
        if old['raw'] == new['raw']:
            continue
        changed.append(old['name'])
        assert old['name'] == target
        assert old['payload'].count(b"printf 'bars\\n'") == 1
        assert new['payload'] == old['payload'].replace(b"printf 'bars\\n'", b"printf 'bars'")
        assert all(old['fields'][i] == new['fields'][i] for i in range(13) if i not in (6, 12))
    assert changed == [target]
    parent = json.loads((HERE.parent / 'r14/composition.json').read_text())
    assert composition['revision'] == 15 and composition['parent_revision'] == 14
    assert composition['bootstrap_sha256'] == sha(cpio)
    assert composition['parent_bootstrap_sha256'] == sha(parent_cpio)
    assert composition['changed_bootstrap_entries'] == [target] and composition['modules'] == parent['modules']
    for name in ('userspace.tar.xz', 'payload.cpio.xz'):
        assert (HERE / name).read_bytes() == (HERE.parent / 'r14' / name).read_bytes()
    initial = json.loads((HERE / 'kernel-pins.json').read_text())
    parent_pins = json.loads((HERE.parent / 'r14/kernel-pins.json').read_text())
    for name in ('.config', 'Module.symvers'):
        assert sha((KERNEL / name).read_bytes()) == initial['files'][name]['sha256'] == parent_pins['files'][name]['sha256']
    assert initial['native_modules'] == parent_pins['native_modules']
    for name, pin in initial['native_modules'].items():
        assert sha((KERNEL / name).read_bytes()) == pin['sha256'], name
    for name in ('drivers/firmware/qcom/qcom_scm-smc.c', 'drivers/firmware/qcom/qcom_scm.c', 'drivers/net/ipa/ipa_main.c', 'drivers/soc/qcom/mdt_loader.c'):
        assert sha((KERNEL / name).read_bytes()) == initial['files'][name]['sha256'], name
    entries = {e['name']: e for e in cpio148.parse(cpio)}
    for module in composition['modules'].values():
        assert sha(entries[module['archive_path']]['payload']) == module['sha256']
    symbols = pack.read_symvers((KERNEL / 'Module.symvers').read_bytes(), KERNEL)
    ipa = pack.elf_info(entries['usr/lib/m3100-ipa/ipa.ko']['payload'])
    assert ipa == pack.elf_info((KERNEL / 'drivers/net/ipa/ipa.ko').read_bytes())
    assert ipa['modinfo']['depends'] == [''] and len(ipa['imports']) == 179
    assert all(symbols[name]['provider_module'] == 'vmlinux' and not symbols[name]['namespace'] for name in ipa['imports'])
    for name in ('etc/full206-ipa-module.sha256', 'etc/full206-modules.sha256'):
        for line in entries[name]['payload'].decode().splitlines():
            digest, path = line.split('  ')
            assert sha(entries[path.lstrip('/')]['payload']) == digest
    return {'one_bootstrap_LCD_printf_fix_only': True, 'all_other_CPIO_entries_raw_bytes_and_metadata_preserved': True,
            'R14_firmware_userspace_and_all64_packaged_modules_byte_identical': True,
            'all53_native_module_pins_match_R14_and_fresh_R15_build': True,
            'IPA_179_imports_and_strip_semantics_match': True, 'config_symvers_unchanged': True}
