"""Prove clone-path differences are debug/build-id only, then preserve R15 vDSO."""
from pathlib import Path
import hashlib
import io
import json
import shutil
import sys
sys.path.append('/mnt/c/Users/Dell/AppData/Roaming/Python/Python313/site-packages')
from elftools.elf.elffile import ELFFile

HERE = Path(__file__).resolve().parent
OLD = Path('/home/dell/m3100-openwrt/trials/full206-r15-linux-6.12.112')
NEW = Path('/home/dell/m3100-openwrt/trials/full206-public-v1-linux-6.12.112')
FILES = ['arch/arm/vdso/' + n for n in ['vgettimeofday.o', 'vdso.o', 'vdso.so', 'vdso.so.dbg']]


def main():
    report = []
    for name in FILES:
        a, b = (OLD / name).read_bytes(), (NEW / name).read_bytes()
        ae, be = ELFFile(io.BytesIO(a)), ELFFile(io.BytesIO(b))
        aa, bb = ({s.name: s for s in e.iter_sections()} for e in [ae, be])
        if set(aa) != set(bb):
            raise RuntimeError('ELF section set changed')
        changed = []
        for n in aa:
            if aa[n]['sh_flags'] != bb[n]['sh_flags'] or aa[n]['sh_type'] != bb[n]['sh_type']:
                raise RuntimeError('ELF flags/type changed')
            if aa[n].data() == bb[n].data():
                continue
            changed.append(n)
            if n.startswith(('.debug', '.rel.debug', '.rela.debug')) and not aa[n]['sh_flags'] & 2:
                continue
            if n == '.note' and name.endswith(('.so', '.so.dbg')):
                old_notes, new_notes = list(aa[n].iter_notes()), list(bb[n].iter_notes())
                if len(old_notes) != len(new_notes):
                    raise RuntimeError('ELF note count changed')
                for x, y in zip(old_notes, new_notes):
                    if x['n_type'] == y['n_type'] == 'NT_GNU_BUILD_ID' and x['n_name'] == y['n_name'] == 'GNU':
                        continue
                    if dict(x) != dict(y):
                        raise RuntimeError('Non-build-id ELF note changed')
                continue
            if n == '.data..ro_after_init' and name.endswith('/vdso.o'):
                for tree, sec in [(OLD, aa[n]), (NEW, bb[n])]:
                    so = (tree / 'arch/arm/vdso/vdso.so').read_bytes()
                    if sec.data() != so + bytes(sec['sh_size'] - len(so)):
                        raise RuntimeError('vDSO object is not the checked embedded ELF')
                continue
            raise RuntimeError('Non-debug change cannot be preserved: ' + name + ':' + n)
        report.append({'file': name, 'only_differing_sections': changed,
                       'accepted_baseline_sha256': hashlib.sha256(a).hexdigest(),
                       'clone_sha256_before_preservation': hashlib.sha256(b).hexdigest()})
    # Mutation occurs only after every section of all four files is verified.
    for name in FILES:
        shutil.copyfile(OLD / name, NEW / name)
    (HERE / 'vdso-preservation.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Verified and preserved baseline vDSO: four generated files; no executable code change')


if __name__ == '__main__':
    main()
