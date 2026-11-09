"""Inventory the pulled stock modules and extract built-in driver analysis.

Run with pyelftools and capstone. This reads binaries; it never executes or
loads them. Inferred function slices are analysis artifacts, not loadable .ko.
"""
from pathlib import Path
import bisect
import hashlib
import json
import re
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
from capstone.arm import ARM_OP_IMM
from elftools.elf.elffile import ELFFile

root = Path(__file__).resolve().parents[1]
dest = root / 'stock/drivers'
modules = []
for path in sorted((dest / 'modules').rglob('*.ko')):
    with path.open('rb') as f:
        elf = ELFFile(f)
        section = elf.get_section_by_name('.modinfo')
        info = {}
        for item in section.data().split(b'\0') if section else []:
            if b'=' not in item:
                continue
            key, value = item.decode(errors='replace').split('=', 1)
            info.setdefault(key, []).append(value)
    modules.append({'file': path.relative_to(dest).as_posix(),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                    'modinfo': info})
(dest / 'module-inventory.json').write_text(json.dumps(modules, indent=2) + '\n')

groups = {'modem-secure': re.compile(r'^(?:__)?qcom_scm_pas_|^pil_auth_and_reset$|^scm_pas_(?:enable|disable)_bw$|^pil_tz_(?:auth|proxy|make|init|mem|shutdown)'),
          'lcd': re.compile(r'nvtl_lcd|^lcd_|^ebi2_|get_nvtl_lcd|^st7789_|^mdss_qpic_|^frame_buffer_free$|^mmiocpy$|^mmioset$'),
          'charging': re.compile(r'^(smb5_|smblib_|qpnp_qg_|qg_)')}
summary = {}
with (root / 'analysis/stock-kernel.elf').open('rb') as f:
    elf = ELFFile(f)
    symbols = list(elf.get_section_by_name('.symtab').iter_symbols())
    funcs = [s for s in symbols if s['st_info']['type'] == 'STT_FUNC' and s['st_value']]
    addresses = sorted(set(s['st_value'] & ~1 for s in funcs))
    names = {s['st_value'] & ~1: s.name for s in funcs}
    sections = {}
    for group, pattern in groups.items():
        folder = dest / 'built-in-analysis' / group
        folder.mkdir(parents=True, exist_ok=True)
        records, assembly = [], []
        for symbol in funcs:
            if not pattern.search(symbol.name):
                continue
            index = symbol['st_shndx']
            if not isinstance(index, int):
                continue
            section = elf.get_section(index)
            if index not in sections:
                sections[index] = section.data()
            start = symbol['st_value'] & ~1
            offset = start - section['sh_addr']
            following = bisect.bisect_right(addresses, start)
            estimated = not symbol['st_size']
            size = symbol['st_size'] or (addresses[following] - start if following < len(addresses) else 0)
            size = min(size, 65536, len(sections[index]) - offset)
            if offset < 0 or size <= 0:
                continue
            data = sections[index][offset:offset + size]
            filename = re.sub(r'[^A-Za-z0-9_.-]', '_', symbol.name) + '.bin'
            (folder / filename).write_bytes(data)
            records.append({'symbol': symbol.name, 'virtual_address': hex(start),
                            'bytes': len(data), 'inferred_size': estimated,
                            'sha256': hashlib.sha256(data).hexdigest(), 'file': filename})
            dis = Cs(CS_ARCH_ARM, CS_MODE_THUMB if symbol['st_value'] & 1 else CS_MODE_ARM)
            dis.detail = True
            dis.skipdata = True
            assembly.append('\n' + hex(start) + ' <' + symbol.name + '>:')
            for ins in dis.disasm(data, start):
                note = ''
                if ins.id and ins.mnemonic in ('bl', 'blx', 'b') and ins.operands and ins.operands[0].type == ARM_OP_IMM:
                    target = ins.operands[0].imm & 0xfffffffe
                    if target in names:
                        note = ' ; ' + names[target]
                assembly.append(f'{ins.address:08x}: {ins.bytes.hex():12} {ins.mnemonic:9} {ins.op_str}{note}')
        (folder / 'functions.json').write_text(json.dumps(records, indent=2) + '\n')
        (folder / 'disassembly.txt').write_text('\n'.join(assembly) + '\n')
        summary[group] = {'functions': len(records), 'code_bytes': sum(r['bytes'] for r in records)}
(dest / 'extraction-summary.json').write_text(json.dumps({'kernel_modules': len(modules),
    'built_in_analysis': summary, 'recovered_original_c_source': False,
    'modules_executed_or_loaded': False}, indent=2) + '\n')
print(json.dumps({'kernel_modules': len(modules), 'built_in_analysis': summary}, indent=2))
