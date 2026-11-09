"""Generate board-scoped remoteproc diagnostics against the prepared kernel."""
from pathlib import Path
import difflib

project = Path(__file__).resolve().parents[1]
tree = Path(r'\\wsl.localhost\Ubuntu-24.04\home\dell\m3100-openwrt\openwrt')
kernel = tree / 'build_dir/target-arm_cortex-a7+neon-vfpv4_musl_eabi/linux-sdx_generic/linux-6.12.112'
name = 'drivers/remoteproc/qcom_q6v5_pas.c'
original = (kernel / name).read_text()
helper = '''static void m3100_stage(const char *stage)
{
	if (!of_machine_is_compatible("inseego,m3100"))
		return;
	pr_info("M3100 modem stage: %s\\n", stage);
	/* Allow the asynchronous USB console to transmit before a secure call. */
	msleep(250);
}

'''
modified = original.replace('static int adsp_load(', helper + 'static int adsp_load(', 1)
modified = modified.replace('\t/* Store firmware handle to be used in adsp_start() */', '\tm3100_stage("load entered");\n\n\t/* Store firmware handle to be used in adsp_start() */', 1)
start = modified.index('static int adsp_start(')
end = modified.index('\nstatic void qcom_pas_handover', start)
section = modified[start:end]
for call, label in [
    ('qcom_q6v5_prepare', 'q6v5 prepare'),
    ('adsp_pds_enable', 'power domains'),
    ('clk_prepare_enable(adsp->xo)', 'XO clock'),
    ('clk_prepare_enable(adsp->aggre2_clk)', 'aggregate clock'),
    ('regulator_enable(adsp->cx_supply)', 'CX regulator'),
    ('regulator_enable(adsp->px_supply)', 'PX regulator'),
    ('qcom_mdt_pas_init', 'PAS metadata init'),
    ('qcom_mdt_load_no_init', 'firmware segment load'),
    ('qcom_scm_pas_auth_and_reset(adsp->pas_id)', 'PAS authenticate and reset'),
    ('qcom_q6v5_wait_for_start', 'ready interrupt wait'),
]:
    lines = section.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if 'ret = ' in line and call in line:
            indent = line[:len(line) - len(line.lstrip())]
            j = i
            while ';' not in lines[j]:
                j += 1
            lines.insert(j + 1, indent + f'm3100_stage("after {label}");\n')
            lines.insert(i, indent + f'm3100_stage("before {label}");\n')
            break
    else:
        raise RuntimeError('Missing call: ' + call)
    section = ''.join(lines)
modified = modified[:start] + section + modified[end:]
assert modified != original
patch = ''.join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True), fromfile='a/' + name, tofile='b/' + name))
destination = project / 'overlay/target/linux/sdx/patches-6.12/119-m3100-modem-stage-trace.patch'
destination.write_text(patch, newline='\n')
print(destination)
