"""Setter / action specs for System (admin, logs, backup, power) and Software update.

Every spec below was established by static analysis of the stock binaries in
reference/stock-fs/opt/nvtl/bin (arm-linux-gnueabihf-objdump -d -M force-thumb, plus a dump of
each CLI's {name, handler, help} dispatch table in .data).  "handler @0x…" is the Thumb entry
point from that table, string offsets are .rodata virtual addresses.  Prompt texts are copied
byte-exact (trailing spaces included).  All are declared source="static"; the sandbox run of
tools/device/verify_specs.py on 2026-10-31 23:36 (unit 76fe1623, fixtures/sandbox/verified.json)
upgrades those whose prompts it confirmed.  The service layer prefers the vendor REST
resources where one exists (DeviceState, DisplayFormatSettings, SoftwareUpdate/*).
Modem diagnostic logging (settings_cli set_modem_logging_settings / clear_modem_logs) belongs to the
Tools area (stock lab_test_info page) and is specified there.
"""
from ..cli_specs import Spec

_WEBUI = "webui_cli (39096 B): "
_SETT = "settings_cli (13840 B): "

SPECS = {
    # -- admin password + security question ------------------------------------------------
    ("webui_cli", "set_login_settings"): Spec(
        ["Choose the security question (0: Birth-date, 1: Mothers-maiden-name, 2: Grownup-street): ",
         "Answer: ",
         "Do you want to reset password [0/1]: ",
         "Enter new password: "],
        tier=3, source="static", repeat=(2, 3), min_items=0, no_spaces=[3], sample=["0", "sample answer", "0"],
        evidence=_WEBUI + "dispatch 0x19044 -> handler 0x5048. memset(struct,780); "
                 "webui_get_modified_login_settings(struct) first, so retries / auto-logout / default password "
                 "are kept. stdin path: printf 0x714c + scanf('%u') question; printf 0x71a8 'Answer(MM/DD/YYYY): ' "
                 "when question==0 else 0x71c0 'Answer: ', scanf(' %[^\\n]s') into a 256-byte field (unbounded!); "
                 "printf 0x71d4 + scanf('%d') reset; only when reset==1: printf 0x71fc + scanf('%255s') password "
                 "(whitespace ends it). reset>1 -> rc 0xF0002, nothing saved; else webui_set_login_settings(struct). "
                 "argv path (argc 5/6) exists but is not used (argv values go through adb shell unquoted). "
                 "Sandbox 2026-10-31 23:36 (fixtures/sandbox/webui_cli/set_login_settings.txt): 'webui_get_modified_"
                 "login_settings returned 1' and no prompt - the handler returns before prompting when the WEBUI "
                 "daemon is unreachable, exactly as disassembled; prompts need a roundtrip check with the daemon up.",
        notes="answers [question 0-2, answer, reset 0|1, (password when reset=1)]; repeat=(2,3) makes the "
              "password prompt appear only when answers[2]==1. The answer prompt reads 'Answer(MM/DD/YYYY): ' for "
              "question 0. Service enforces: answer 1-255 bytes, not blank (a blank line would be skipped by "
              "' %[^\\n]' and eat the next answer), password printable ASCII without spaces, 8-32 chars."),

    # -- vendor event-log settings (stock status_logs page) ---------------------------------
    ("webui_cli", "set_log_settings"): Spec(
        ["Is log enabled [0/1]: ", "What is log life time ([1 - 4] days): "],
        tier=1, source="static", sample=["0", "3"],
        evidence=_WEBUI + "dispatch 0x1905c -> handler 0x4df8: argc==4 -> strtol(argv[2..3]); else printf 0x6f74 + "
                 "scanf('%hhu'), printf 0x6f8c + scanf('%u'); webui_set_log_settings(struct). No range check in "
                 "the CLI (service limits life time to 1-4).",
        notes="answers [enabled 0|1, life time days 1-4]"),

    # -- language / display formats (fallback when REST DisplayFormatSettings fails) ------
    ("webui_cli", "set_i18n_settings"): Spec(
        ["What is the language [en_US]: ",
         "What is the date-format [0(mm/dd/yyyy), 1(dd.mm.yyyy), 2(dd/mm/yyyy), 3(yyyy/mm/dd)]: ",
         "What is the hour-format [0(12hr), 1(24hr)]: ",
         "What is the temparature-format [0(Fahrenheit), 1(Celsius)]: ",
         "What is the distance-format [0(US), 1(Metric)]]: ",
         "What is the numeric-format [0(period, 1(comma)]: "],
        tier=1, source="static", no_spaces=[0], sample=["en_US", "0", "0", "0", "0", "0"],
        evidence=_WEBUI + "dispatch 0x19074 -> handler 0x4bd0: argc==8 -> argv; else printf 0x6cbc + scanf('%s'), "
                 "then 0x6cdc/0x6d34/0x6d64/0x6da4/0x6dd8 each scanf('%u'); webui_set_i18n_settings(struct).",
        notes="answers [locale, date 0-3, hour 0-1, temperature 0-1, distance 0-1, numeric 0-1]"),

    # -- automatic software update (opt-out) ------------------------------------------------
    ("webui_cli", "set_software_update_settings"): Spec(
        ["FUMO (0: Disable, 1: Enable): ", "Auto Polling (0: Disable, 1: Enable): ", "Low Battery Threshold (1-99): "],
        tier=1, source="static", sample=["1", "1", "20"],
        evidence=_WEBUI + "dispatch 0x19164 -> handler 0x4468: argc==5 -> strtol(argv[2..4]); else printf "
                 "0x68c0/0x68e4/0x690c each + scanf('%u'); webui_set_software_update_settings(struct). The success "
                 "line reuses the 'webui_get_software_update_settings returned' string (stock quirk).",
        notes="answers [fumo 0|1, auto poll 0|1, low battery threshold 1-99]"),

    # -- backup / restore (settingsd -> libmifi_config -> /usr/sbin/appconfig create|upload) -
    ("settings_cli", "backup"): Spec(
        ["(no prompt)"], tier=1, source="static", mode="argv", argc=0,
        evidence=_SETT + "dispatch 0x13044 -> handler 0xe24: settings_backup(NULL,&len) -> rc 0x20003 (buffer size), "
                 "malloc(len), settings_backup(buf,&len) -> printf 'backup_file:[%s]' (0x1768). No stdin/argv read. "
                 "libmifi_config: '/usr/sbin/appconfig %s %s' with 'create' and '/tmp' + 'nvtlconfig.bin' "
                 "(encrypted archive; 'encryption failed' error string).",
        notes="no values; prints backup_file:[/tmp/nvtlconfig.bin]"),
    ("settings_cli", "restore"): Spec(
        ["What is the path:  "], tier=3, source="static", sample=["/tmp/m3ui-sandbox-none.bin"],
        evidence=_SETT + "dispatch 0x13050 -> handler 0x151c: argc==3 -> strncpy(argv[2]); else printf 0x1ef8 + "
                 "fgets() (CR/LF stripped); settings_restore(path) -> libmifi_config 'appconfig upload <path>'. "
                 "Device reboot is done by the caller (stock page redirects to /restarting/).",
        notes="answers [absolute path of the staged backup file]"),

    # -- factory reset + hardware reset button ---------------------------------------------
    ("factory_reset_cli", "do_factory_reset"): Spec(
        ["(no prompt)"], tier=3, source="static", mode="argv", argc=0,
        evidence="factory_reset_cli (9700 B): dispatch 0x1202c -> handler 0xbd8: factory_reset_do_reset() only "
                 "(same vendor call as sys_restoreFactoryDefaults used by the stock backup_restore page). "
                 "Fallback only: the service uses REST DeviceState first.",
        notes="no values. do_factory_reset_wipe_data is deliberately not specified (DataWipe disabled in "
              "/opt/nvtl/etc/factory_reset/config.xml; could wipe /data)."),
    ("factory_reset_cli", "set_hw_reset_sw_enabled"): Spec(
        ["Enter HW reset status (0=Disable, 1=Enable): "], tier=2, source="static", sample=["1"],
        evidence="factory_reset_cli: dispatch 0x12050 -> handler 0xc38: argc==3 -> strtol(argv[2],10); argc==2 -> "
                 "printf 0xf04 + fgets(buf,4) + strtol; other argc -> 'Invalid number of command line parameters.'; "
                 "factory_reset_set_hw_reset_enabled(v); success prints 'HW reset SW status Complete.'",
        notes="answers [0|1]; takes effect after a restart (stock log text)"),

    # -- restart / power off (fallbacks for REST DeviceState) -------------------------------
    ("dsm_cli", "reset_device"): Spec(
        ["What is forced (1-forced, 0-wait for ack):  "], tier=2, source="static", sample=["0"],
        evidence="dsm_cli (22336 B): dispatch 0x15050 -> handler 0x2148: argc==3 -> strtol(argv[2]); else printf "
                 "0x3104 + scanf('%d'); dsm_reset_device(forced). Same vendor call as sys_rebootSystem.",
        notes="answers ['0'] (graceful: wait for subsystem acks)"),
    ("dsm_cli", "shutdown_device"): Spec(
        ["What is forced (1-forced, 0-wait for ack):  "], tier=3, source="static", sample=["0"],
        evidence="dsm_cli: dispatch 0x15044 -> handler 0x21dc: argc==3 -> strtol(argv[2]); else printf 0x3104 + "
                 "scanf('%d'); dsm_shutdown_device(forced). Same vendor call as sys_shutdownSystem.",
        notes="answers ['0'] (graceful)"),

    # -- NUA update agent (generic firmware; not the active agent on the carrier build) -----
    ("nua_cli", "set_settings"): Spec(
        ["-f", "<fumo 0|1>", "-p", "<poll 0|1>", "-s", "<FOTA opt-out disabled 0|1>", "-l", "<low battery %>",
         "-w", "<low battery % with charger>"],
        tier=1, source="static", mode="argv", argc=10,
        sample=["-f", "1", "-p", "1", "-s", "1", "-l", "25", "-w", "10"],
        evidence="nua_cli (43068 B): dispatch 0x1a21c -> handler 0x2704: getopt_long(optstring 'f:p:s:l:w:' @0x6344), "
                 "each value strtoul; then su_api_get_settings, merge, printf 0x63e4, su_api_set_settings. No stdin. "
                 "Usage string @0x6360. Sandbox 2026-10-31 printed the usage because the generic sample "
                 "('1' x10) has no option flags; this sample passes the five -x options.",
        notes="values ['-f',F,'-p',P,'-s',S,'-l',L,'-w',W]; all five options are always passed"),
    ("nua_cli", "set_poll_interval"): Spec(
        ["<seconds>"], tier=1, source="static", mode="argv", argc=1, sample=["86400"],
        evidence="nua_cli: dispatch 0x1a1ec -> handler 0x3000: argc==3 -> strtol(argv[2]) -> "
                 "su_api_test_set_poll_interval; else usage 0x68e4. Prints 'restart nua or device to take effect'.",
        notes="values [seconds >= 60]"),
}
