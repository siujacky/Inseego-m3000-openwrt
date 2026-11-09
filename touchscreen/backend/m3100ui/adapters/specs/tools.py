"""Setter / action specs for the Tools area (diagnostics, AT console, lab-test settings, PCO, sessions).

All established by static analysis of the stock binaries in reference/stock-fs/opt/nvtl/bin/ (objdump -M force-thumb,
string addresses are virtual addresses in the binary).  Commands without any stdin input use mode="argv", argc=0:
the runner then passes no answers and no arguments at all.

Shared keys: settings_cli set_modem_logging_settings / clear_modem_logs are also specified by the System area and
ccm2_cli accept_pco_event by the SIM area (same prompt lists).  A key defined in two spec modules is dropped from
both, so this module only contributes keys that no spec module loaded before it (alphabetically) already defines.
"""
import importlib
import pkgutil

from ..cli_specs import Spec

_OWN = {
    # AT console transport (adapters/at.py).  Answers: [AT command, "" (no prompt), "" (no reply), timeout].
    ("modem2_cli", "run_raw_command"): Spec(
        ["Enter the command string: ", "Enter the prompt string(enter for none): ",
         "Enter the reply string(enter for none): ", "Enter the timeout value: "],
        tier=3, source="static", mode="stdin", sample=["AT", "", "", "5"],
        evidence="modem2_cli handler @0x11ea8 (dispatch idx 194, table .data 0x39920): argc==6 -> argv[2..5]; otherwise "
                 "printf '\\nEnter the command string: ' (0x20160) fgets(2048), '\\nEnter the prompt string(enter for "
                 "none): ' (0x2017c) fgets(2048), '\\nEnter the reply string(enter for none): ' (0x201a8) fgets(2048), "
                 "'\\nEnter the timeout value: ' (0x201d4) fgets(60) + strtol(base 0) & 0xffff, each stripped by 0xe16c; "
                 "then modem2_run_raw_command(resp, 2048, cmd, prompt, reply, timeout); RC name 'cmd_run_raw_command' "
                 "(0x27674); 'response:[%s]' (0x201f0) only when rc == 0xC0000. modem2d 0xc440 forwards to "
                 "libmal_qct modem2_modem_run_raw_command @0x1cd50 = movs r0,#1; movt r0,#12; bx lr (always 0xC0001).",
        notes="stdin mode: argv cannot carry the empty prompt/reply strings (the runner refuses empty argv values). "
              "On this firmware the QMI back end is a stub, so a real unit answers 'general error' (rc 1)."),

    # lab_test_info: modem (QXDM-style) logging.  scanf("%d") x3 into {enabled, log_files_size, number_of_files}.
    ("settings_cli", "set_modem_logging_settings"): Spec(
        ["Enter logging enabled (0/1):", "Enter log files size:", "Enter number of files (1-3):"],
        tier=1, source="static", mode="stdin", sample=["0", "25", "3"],
        evidence="settings_cli handler @0xf00 (dispatch .data 0x13074, no argc test): printf 0x17a4 'Enter logging "
                 "enabled (0/1):' + scanf('%d') -> sp+0; printf 0x17c8 'Enter log files size:' + scanf('%d') -> sp+4; "
                 "printf 0x17e0 'Enter number of files (1-3):' + scanf('%d') -> sp+8; settings_set_modem_logging_settings(sp); "
                 "'settings_set_modem_logging_settings returned %d (%s)' (0x1800).",
        notes="answers: [0|1, size in MB per file, file count 1-3]; stock UI limits: 1-3 files, total 10-75 MB"),
    ("settings_cli", "clear_modem_logs"): Spec(
        [], tier=2, source="static", mode="argv", argc=0,
        evidence="settings_cli handler @0xd18 (dispatch .data 0x13084): no input at all; settings_clear_modem_logs(); "
                 "'settings_clear_modem_logs returned %d (%s)' (0x16ec).",
        notes="stock lab_test_info 'delete modem logs' (opr=clearModemLogs -> sys_clearModemLogs)"),

    # pco: "allow limited access" (stock POST /pco/pcoEvent/ -> sys_cellularDataUnblockAccess -> ccm2_accept_pco_event)
    ("ccm2_cli", "accept_pco_event"): Spec(
        [], tier=1, source="static", mode="argv", argc=0,
        evidence="ccm2_cli handler @0x2034 (dispatch .data 0x17200): no input; ccm2_accept_pco_event(); "
                 "'ccm_accept_pco_event returned %d (%s)' (0x46c4). libsysintclient sys_cellularDataUnblockAccess "
                 "@0x2b531 calls the same ccm2_accept_pco_event.",
        notes="temporarily restores data so the account can be topped up / activated (PCO 3 / PCO 5)"),
}
# Not a setter, so no spec: `sysintcli getSessionLogStats` (session_log_info) is classified read-only by the core's
# camelCase rule (get[A-Z].*).  Static analysis for the record: command table entry .data file-off 0x3e0a0
# (name[0x64] + handler) -> handler @0xd878, no fgets/scanf; prints 'sys_getSessionLogStats returned %d, count:[%d]'
# (0x2ae44) and list[%d].ipv4/ipv6/roam_status/time/duration/rx/tx/rxv6/txv6 (0x2ae74..0x2af18).


def _defined_earlier(keys):
    """Keys already specified by spec modules that cli_specs loads before this one."""
    from .. import specs as pkg
    me = __name__.rsplit(".", 1)[-1]
    taken = set()
    for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if m.name >= me:
            continue
        try:
            mod = importlib.import_module(pkg.__name__ + "." + m.name)
        except Exception:  # a broken sibling contributes nothing (cli_specs reports it)
            continue
        taken.update(k for k in getattr(mod, "SPECS", {}) if k in keys)
    return taken


SHARED = _defined_earlier(set(_OWN))
SPECS = {k: v for k, v in _OWN.items() if k not in SHARED}
