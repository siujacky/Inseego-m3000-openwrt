"""Prepare a board-scoped early PHY experiment; never boot or flash hardware.

The stock M3100 PHY sequence and live register snapshot both contain
PHY+0x1420=0x40. The SDX65 RC tables omit this value. Its effect is unproven.
Write after all tables, before pipe clocks, SW_RESET release and START.
"""
from pathlib import Path
import difflib
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BUILD = '/home/dell/m3100-openwrt/openwrt'
KERNEL = BUILD + '/build_dir/target-arm_cortex-a7+neon-vfpv4_musl_eabi/linux-sdx_generic/linux-6.12.112'
SOURCE = 'drivers/phy/qualcomm/phy-qcom-qmp-pcie.c'
PATCH = '122-m3100-pcie-early-refclk-trial.patch'
MARKER = 'M3100 early PHY PCS_MISC+0x20='


def main():
    original = subprocess.check_output(
        ['wsl', '-d', 'Ubuntu-24.04', '-e', 'cat', KERNEL + '/' + SOURCE]).decode()
    if MARKER in original:
        raise SystemExit('Experiment is already present; do not apply twice')
    anchor = '\tqmp_pcie_init_registers(qmp, mode_tbls);\n'
    assert original.count(anchor) == 1
    addition = '''
	/* Diagnostic M3100-only restoration of its stock PHY sequence.
	 * The V5.20 header does not name this offset; do not infer semantics
	 * from another PHY revision. Keep the RC and board checks explicit.
	 */
	if (cfg == &sdx65_qmp_pciephy_cfg &&
	    qmp->mode == PHY_MODE_PCIE_RC &&
	    of_machine_is_compatible("inseego,m3100")) {
		writel(0x40, qmp->pcs_misc + 0x20);
		dev_info(qmp->dev, "M3100 early PHY PCS_MISC+0x20=%#x\\n",
			 readl(qmp->pcs_misc + 0x20));
	}
'''
    revised = original.replace(anchor, anchor + addition)
    patch = ''.join(difflib.unified_diff(original.splitlines(True),
                                       revised.splitlines(True),
                                       fromfile='a/' + SOURCE, tofile='b/' + SOURCE))
    output = ROOT / 'overlay/target/linux/sdx/patches-6.12' / PATCH
    output.write_text(patch, newline='\n')
    work = ROOT / 'analysis/pcie-early-refclk'
    work.mkdir(exist_ok=True)
    (work / 'phy-before.c').write_text(original, newline='\n')
    (work / 'phy-after.c').write_text(revised, newline='\n')
    print(output)


if __name__ == '__main__':
    main()
