"""Generate M3100-only tracing inside the modem PAS authentication wrapper."""
from pathlib import Path
import difflib

project = Path(__file__).resolve().parents[1]
tree = Path(r'\\wsl.localhost\Ubuntu-24.04\home\dell\m3100-openwrt\openwrt')
kernel = tree / 'build_dir/target-arm_cortex-a7+neon-vfpv4_musl_eabi/linux-sdx_generic/linux-6.12.112'
name = 'drivers/firmware/qcom/qcom_scm.c'
original = (kernel / name).read_text()
assert 'm3100_pas_trace' not in original
helper = '''static void m3100_pas_trace(u32 pas_id, const char *stage, int ret)
{
	if (pas_id != 4 || !of_machine_is_compatible("inseego,m3100"))
		return;
	pr_info("M3100 PAS stage: %s ret=%d\\n", stage, ret);
	msleep(250);
}

'''
modified = original.replace('#include <linux/dma-mapping.h>', '#include <linux/delay.h>\n#include <linux/dma-mapping.h>', 1)
modified = modified.replace('int qcom_scm_pas_auth_and_reset(u32 pas_id)', helper + 'int qcom_scm_pas_auth_and_reset(u32 pas_id)', 1)
start = modified.index('int qcom_scm_pas_auth_and_reset(u32 pas_id)')
end = modified.index('EXPORT_SYMBOL_GPL(qcom_scm_pas_auth_and_reset);', start)
section = modified[start:end]
for call, label in [('qcom_scm_clk_enable()', 'SCM clocks'), ('qcom_scm_bw_enable()', 'SCM bandwidth'), ('qcom_scm_call(__scm->dev, &desc, &res)', 'secure call')]:
    statement = '\tret = ' + call + ';'
    assert section.count(statement) == 1
    section = section.replace(statement, '\tm3100_pas_trace(pas_id, "before ' + label + '", 0);\n' + statement + '\n\tm3100_pas_trace(pas_id, "after ' + label + '", ret);')
modified = modified[:start] + section + modified[end:]
patch = ''.join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True), fromfile='a/' + name, tofile='b/' + name))
destination = project / 'overlay/target/linux/sdx/patches-6.12/119b-m3100-scm-stage-trace.patch'
destination.write_text(patch, newline='\n')
print(destination)
