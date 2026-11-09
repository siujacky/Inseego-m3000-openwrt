"""Generate a diagnostic patch only; never modify the kernel or run a device.

The low-level PAS4 auth enter/exit printk is synchronous with ramoops console.
It differentiates a non-returning TZ call from loss of the USB console after
a successful call. Existing trace_scm_smc_request remains unchanged.
"""
from pathlib import Path
import difflib

root = Path(__file__).resolve().parents[1]
kernel = Path(r"\\wsl.localhost\Ubuntu-24.04\home\dell\m3100-openwrt\openwrt\build_dir\target-arm_cortex-a7+neon-vfpv4_musl_eabi\linux-sdx_generic\linux-6.12.112")
patches = []

name = 'drivers/firmware/qcom/qcom_scm-smc.c'
before = (kernel / name).read_text()
after = before.replace('#include <linux/mutex.h>', '#include <linux/mutex.h>\n#include <linux/of.h>', 1)
needle = '\tquirk.state.a6 = 0;\n\n\tdo {'
assert after.count(needle) == 1
after = after.replace(needle, '''\tbool m3100_auth = of_machine_is_compatible("inseego,m3100") &&
\t\t(smc->args[0] & 0xffff) == 0x0205 && smc->args[2] == 4;
\tunsigned int attempt = 0;

\tquirk.state.a6 = 0;

\tdo {''', 1)
needle = '\t\ttrace_scm_smc_request(a0, smc);'
assert after.count(needle) == 1
after = after.replace(needle, '''\t\tif (m3100_auth && attempt < 16)
\t\t\tpr_emerg("M3100 MPSS SMCCC enter attempt=%u r0=%08lx r1=%08lx r2=%08lx r3=%08lx a6=%08lx\\n",
\t\t\t\tattempt, a0, smc->args[1], smc->args[2],
\t\t\t\tsmc->args[3], quirk.state.a6);
''' + needle, 1)
needle = '\n\t\tif (res->a0 == QCOM_SCM_INTERRUPTED)'
assert after.count(needle) == 1
after = after.replace(needle, '''
\t\tif (m3100_auth && (attempt < 16 || res->a0 != QCOM_SCM_INTERRUPTED))
\t\t\tpr_emerg("M3100 MPSS SMCCC exit attempt=%u r0=%08lx r1=%08lx r2=%08lx r3=%08lx a6=%08lx\\n",
\t\t\t\tattempt, res->a0, res->a1, res->a2, res->a3,
\t\t\t\tquirk.state.a6);
\t\tattempt++;
''' + needle, 1)
patches.append(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile='a/' + name, tofile='b/' + name)))

name = 'drivers/firmware/qcom/qcom_scm.c'
before = (kernel / name).read_text()
after = before
needle = '\tdesc.args[1] = mdata_phys;'
assert after.count(needle) == 1
after = after.replace(needle, needle + '''
\tif (pas_id == 4 && of_machine_is_compatible("inseego,m3100"))
\t\tpr_emerg("M3100 MPSS INIT metadata pa=%pad bytes=%zu retained=%u\\n",
\t\t\t &mdata_phys, size, !!ctx);
''', 1)
start = after.index('int qcom_scm_pas_mem_setup(')
end = after.index('EXPORT_SYMBOL_GPL(qcom_scm_pas_mem_setup);', start)
section = after[start:end]
needle = '\tret = qcom_scm_call(__scm->dev, &desc, &res);'
assert section.count(needle) == 1
section = section.replace(needle, '''\tif (pas_id == 4 && of_machine_is_compatible("inseego,m3100"))
\t\tpr_emerg("M3100 MPSS MEM_SETUP base=%pa bytes=%pa\\n", &addr, &size);
''' + needle + '''
\tif (pas_id == 4 && of_machine_is_compatible("inseego,m3100"))
\t\tpr_emerg("M3100 MPSS MEM_SETUP transport=%d secure=%llu\\n",
\t\t\t ret, (unsigned long long)(ret ? 0 : res.result[0]));
''', 1)
after = after[:start] + section + after[end:]
patches.append(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile='a/' + name, tofile='b/' + name)))
output = root / 'analysis/mpss-smc-diagnostics.patch'
output.write_text(''.join(patches), newline='\n')
print(output)
