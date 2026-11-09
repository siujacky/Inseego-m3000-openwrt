"""Setter specs for the SIM / APN / SMS / data-usage area (services/sim.py, apn.py, sms.py, usage.py).

All prompts were established by static analysis of the stock binaries in reference/stock-fs/opt/nvtl/bin
(objdump -d -M force-thumb + PC-relative string resolution; scripts copied from modem2-analysis into
%TEMP%/claude/area-sim: cli_scan.py, annotate.py).  Handler addresses are the dispatch-table entries
{name, handler, help}; string addresses are .rodata VAs.  Prompt order = execution order of the stdin path.

Secrets (PIN, PUK, carrier unlock code, APN passwords) always go through stdin, never argv, so they do not
show up in /proc/<pid>/cmdline.  Non-secret modem2_cli setters use the argv form where modem2-cli.md lists a
working argc test (exact value counts below).
"""
from ..cli_specs import Spec

_M2 = "modem2_cli (sha256 5e88fc07...) "
_MULTI_APN_FIELDS = [
    "Enter Name: ", "Enter IMSI: ", "Enter ICCID: ", "Enter Active [0 or 1]: ", "Enter Default [0 or 1]: ",
    "Enter APN: ", "Enter username: ", "Enter password: ", "Enter Auth [0 none, 1 pap, 2 chap, 3 both]: ",
    "Enter PDP [0 ipv4, 2 ipv6, 3 ipv4v6]: ",
]
_PROFILE_FIELDS = [  # shared tail of prof_set_tech / prof_set_cust_tech (manual-DNS path, see notes)
    "Enter apn name: ", "Enter username: ", "Enter password: ", "Enter name: ",
    "Enter locked status, none=0, enabled=1, auto=2: ", "Enter auth value, none=0, pap=1, chap=2: ",
    "Enter manual dns, disabled=0, enabled=1: ", "Enter dns1: ", "Enter dns2: ",
    "Enter pdp type, v4=0, ppp=1, v6=2, v4v6=3: ", "Enter apn disabled, apn disabled=1, enabled=0: ",
    "Enter pdn inactivity timeout: ", "Enter ipv4 pref: ", "Enter ipv6 pref: ",
    "Enter data mode, cdma_hdr=0, cdma=1, hdr=2: ", "Enter 3gpp2 pdn type, v4=0, v6=1, v4v6=2, nopref=3: ",
]
_PROFILE_NOTE = ("The handler only prints/reads 'Enter dns1/dns2' when manual dns != 0 (cmp r0,#0; bne to the "
                 "out-of-line DNS block). A Spec has a fixed prompt count, so the service always answers manual dns=1 "
                 "and sends 0.0.0.0 for 'automatic' - the same values the carrier-provisioned PRI and custom profiles "
                 "carry on this unit (fixtures prof_get_pri_tech / prof_get_cust_tech: Manual DNS:[1], DNS1:[0.0.0.0]).")

SPECS = {
    # ------------------------------------------------------------------ SIM PIN / PUK (tier 3)
    ("modem2_cli", "sim_unlock_pin"): Spec(
        ["What is SIM PIN:  "], tier=3, source="static", no_spaces=[0], sample=["1234"],
        evidence=_M2 + "handler 0xa7a8: argc==3 test @0xa7aa; stdin path printf 'What is SIM PIN:  ' @0xa7c8, "
                       "scanf('%s') @0xa7d2 -> modem2_sim_unlock_pin(sp+12) @0xa7d8",
        notes="stdin form used so the PIN never appears in argv"),
    ("modem2_cli", "sim_unlock_puk"): Spec(
        ["What is SIM PUK:  ", "What is SIM PIN:  "], tier=3, source="static", no_spaces=[0, 1],
        sample=["12345678", "1234"],
        evidence=_M2 + "handler 0xa6e8: argc==4 test @0xa6ea; printf PUK prompt @0xa70e scanf %s @0xa716, printf PIN "
                       "prompt @0xa720 scanf %s @0xa728 -> modem2_sim_unlock_puk(puk, new pin) @0xa730",
        notes="answers: [PUK, new PIN]; each wrong PUK uses one of the (usually 10) PUK attempts"),
    ("modem2_cli", "sim_enable_pin"): Spec(
        ["enable sim pin lock [1/0]: ", "What is SIM PIN:  "], tier=3, source="static", no_spaces=[1],
        sample=["0", "1234"],
        evidence=_M2 + "handler 0xa83c: argc==4 test @0xa83e; printf 'enable sim pin lock [1/0]: ' @0xa85c scanf %d "
                       "@0xa866; printf PIN prompt @0xa870 scanf %s @0xa87a -> modem2_sim_enable_pin @0xa884",
        notes="answers: [1 to turn the lock on | 0 to turn it off, current PIN]"),
    ("modem2_cli", "sim_change_pin"): Spec(
        ["What is old SIM PIN:  ", "What is new SIM PIN:  "], tier=3, source="static", no_spaces=[0, 1],
        sample=["1234", "4321"],
        evidence=_M2 + "handler 0xa628: argc==4 test @0xa62a; printf old prompt @0xa64e scanf %s @0xa656, printf new "
                       "prompt @0xa660 scanf %s @0xa668 -> modem2_sim_change_pin(old, new) @0xa670"),
    ("modem2_cli", "unlock_carrier"): Spec(
        ["\npin:"], tier=3, source="static", no_spaces=[0], sample=["00000000"],
        evidence=_M2 + "handler 0xd84c: argc==3 test @0xd84e; puts('\\npin:') @0xd86a, scanf('%s') @0xd874 -> "
                       "modem2_carrier_unlock(sp+12) @0xd87a; RC line 'cmd_unlock_carrier_lock'",
        notes="carrier (network) unlock code; libmal_qct compares it with NV 60004 and writes NV 60002 on success. "
              "Limited attempts: the service enforces lock-state checks, last-attempt confirmation and a rate limit"),

    # ------------------------------------------------------------------ SIM slots (dual-SIM units)
    ("modem2_cli", "sim_mgr_switch_slot"): Spec(
        ["Enter the slot to switch"], tier=2, source="static", mode="argv", argc=1, sample=["1"],
        evidence=_M2 + "handler 0xc3ac: cmp r0,#3 / beq @0xc3ae,0xc3c8 -> strtol(argv[2]) @0xc426 -> "
                       "modem2_uim_switch_slot; stdin path puts('Enter the slot to switch') @0xc3ce + scanf %d",
        notes="argv: <slot 1|2>"),
    ("modem2_cli", "sim_mgr_set_imsi_priority"): Spec(
        ["\nEnter the imsi: ", "\nEnter priority for this imsi"], tier=1, source="static", mode="argv", argc=2,
        sample=["001010000000001", "1"],
        evidence=_M2 + "handler 0xf7e4: ARGC_CMP #4 @0xf7e6; argv path strtol(argv[3]) @0xf894; stdin path "
                       "printf imsi prompt @0xf808 __fgets_chk(32) @0xf818, puts priority prompt @0xf826 scanf %d",
        notes="argv: <imsi> <priority>"),

    # ------------------------------------------------------------------ multi-APN (stdin only, see quirks)
    ("modem2_cli", "multi_apn_add_profile"): Spec(
        list(_MULTI_APN_FIELDS), tier=1, source="static",
        sample=["Test", "001010000000001", "8900100000000000001", "0", "0", "internet", "", "", "0", "3"],
        evidence=_M2 + "handler 0xedb4: any argv -> 'command line arguments not supported.' (ARGC_CMP #2 @0xeddc); "
                       "printf/fgets(254) pairs @0xedec,0xee14,0xee38,0xee5c(strtol),0xee84(strtol),0xeeac,0xeed0,"
                       "0xeef6,0xef1c(strtol),0xef46(strtol) -> modem2_multi_apn_add_profile @0xef6c",
        notes="Auth 0 none/1 PAP/2 CHAP/3 PAP or CHAP; PDP 0 IPv4/2 IPv6/3 IPv4v6"),
    ("modem2_cli", "multi_apn_update_profile"): Spec(
        ["Enter index: "] + list(_MULTI_APN_FIELDS), tier=1, source="static",
        sample=["1", "Test", "001010000000001", "8900100000000000001", "0", "0", "internet", "", "", "0", "3"],
        evidence=_M2 + "handler 0xeb40: 'Enter index: ' @0xeb76 fgets+strtol(10), then the add-profile sequence "
                       "@0xeba2..0xecf8 -> modem2_multi_apn_update_profile(id, profile) @0xed20",
        notes="first answer = profile id; activation (sys_activateAPNProfile in libsysintclient @0x3296c) is this call "
              "with Active=1, so the service uses it for 'activate' too (tier 2 then)"),
    ("modem2_cli", "multi_apn_delete_profile"): Spec(
        ["Enter profile id: "], tier=1, source="static", sample=["99"],
        evidence=_M2 + "handler 0xea78: printf 'Enter profile id: ' @0xeaac fgets(254)+strtol(10) @0xeaca -> "
                       "modem2_multi_apn_delete_profile @0xeace"),
    ("modem2_cli", "multi_apn_restore_default_profile"): Spec(
        ["Enter profile id: "], tier=1, source="static", sample=["1"],
        evidence=_M2 + "handler 0xe9b0: printf 'Enter profile id: ' @0xe9e4 fgets+strtol(10) @0xea02 -> "
                       "modem2_multi_apn_restore_default_profile @0xea06"),

    # ------------------------------------------------------------------ APN failback
    ("modem2_cli", "set_apn_failback_enabled"): Spec(
        ["Enter mode (0-Disable, 1-Enable):"], tier=1, source="static", mode="argv", argc=1, sample=["0"],
        evidence=_M2 + "handler 0xe18c: ARGC_CMP #2 @0xe18e -> strtol(argv[2]) @0xe1ae -> "
                       "modem2_set_apn_failback_enabled; stdin path printf @0xe1ee fgets(254) strtol(10)",
        notes="argv: <0|1>"),
    ("modem2_cli", "failback_switch_apn"): Spec(
        [], tier=2, source="static", mode="argv", argc=0,
        evidence=_M2 + "handler 0x7acc: no input, modem2_failback_switch_apn @0x7ad0, RC 'cmd_failback_apn_switch'",
        notes="switches the data APN to the next failback APN (the data session restarts)"),

    # ------------------------------------------------------------------ modem (tech) profiles, expert
    ("modem2_cli", "prof_set_act_tech"): Spec(
        ["Enter profile type (3GPP=0, 3GPP2=1): ", "Enter profile index: "], tier=2, source="static",
        mode="argv", argc=2, sample=["0", "3"],
        evidence=_M2 + "handler 0x148c0: cmp r0,#4 @0x148c2 -> strtol(argv[2]) @0x14976, strtol(argv[3]) @0x14982 "
                       "-> modem2_set_active_tech_profile; stdin prompts @0x148e8/0x14912",
        notes="argv: <type 0=3GPP|1=3GPP2> <index 1..254>"),
    ("modem2_cli", "prof_set_tech"): Spec(
        ["Enter profile type (3GPP=0, 3GPP2=1): ", "Enter profile index: "] + list(_PROFILE_FIELDS), tier=2,
        source="static",
        sample=["0", "3", "internet", "", "", "", "0", "0", "1", "0.0.0.0", "0.0.0.0", "3", "0", "0", "0.0.0.0", "::",
                "0", "0"],
        evidence=_M2 + "handler 0x1536c: ARGC_CMP #19 @0x1539c (argv form not used: empty strings/passwords); stdin "
                       "printf/fgets @0x154e2,0x15518,0x1553e(65),0x15552(65),0x15570(65),0x15590(65),0x155b2,"
                       "0x155e4,0x1560e, dns block @0x15778/0x15792 (fgets 17) taken when manual dns != 0 "
                       "(cmp r0,#0 bne @0x15632), then 0x15656,0x15680,0x156aa,0x156d4(17),0x156f2(47),0x15714,0x15746 "
                       "-> modem2_set_tech_profile @0x1549c",
        notes=_PROFILE_NOTE),
    ("modem2_cli", "prof_set_cust_tech"): Spec(
        ["Enter APN Technology (LTE=0, UMTS=1, eHRPD=2): "] + list(_PROFILE_FIELDS), tier=2, source="static",
        sample=["0", "ims", "", "", "", "0", "0", "1", "0.0.0.0", "0.0.0.0", "3", "0", "0", "0.0.0.0", "::", "0", "0"],
        evidence=_M2 + "handler 0x14a9c: ARGC_CMP #18 @0x14ac8; stdin printf/fgets @0x14c06,0x14c38,0x14c4c,0x14c6a,"
                       "0x14c8a,0x14cac,0x14cde,0x14d08, dns block @0x14e70/0x14e8a, 0x14d4e,0x14d78,0x14da2,0x14dcc,"
                       "0x14dea,0x14e0c,0x14e3e -> modem2_set_custom_tech_profile @0x14bb8",
        notes=_PROFILE_NOTE),
    ("modem2_cli", "pdn_set_override_param"): Spec(
        ["Enter profile number: ", "Enter profile type (0-3GPP, 1-3GPP2): ",
         "Enter override pdp type (0-IPV4, 1-invalid, 2-IPV6, 3-IPV4V6): ",
         "Enter clat enabled (0 - disabled, 1 - enabled): "], tier=2, source="static", mode="argv", argc=4,
        sample=["3", "0", "3", "0"],
        evidence=_M2 + "handler 0x15f54: cmp r0,#6 @0x15f56 -> strtol(argv[2..5]) @0x1605c..0x1608c -> "
                       "modem2_set_pdn_override_param; stdin prompts @0x15f78,0x15fa6,0x15fd2,0x15ffa",
        notes="argv: <profile> <type> <pdp 0|2|3> <clat 0|1>"),
    ("modem2_cli", "pdn_set_ext_params"): Spec(
        ["Enter profile number: ", "Enter profile type (0-3GPP, 1-3GPP2): ", "Enter maximum pdn connections: ",
         "Enter wait timer: ", "Enter connection timer: "], tier=2, source="static", mode="argv", argc=5,
        sample=["3", "0", "1", "0", "0"],
        evidence=_M2 + "handler 0x15c48: cmp r0,#7 @0x15c4a -> strtol(argv[2..6]) @0x15d7c..0x15dbe -> "
                       "modem2_set_pdn_ext_params; stdin prompts @0x15c6e..0x15d1a",
        notes="argv: <profile> <type> <max connections> <wait timer> <connection timer>"),

    # ------------------------------------------------------------------ sim_mgr_cli (dual-SIM manager)
    ("sim_mgr_cli", "set_switch_mode"): Spec(
        ["\nswitch mode:"], tier=2, source="static", sample=["0"],
        evidence="sim_mgr_cli handler 0x43a8: no argc test; printf '\\nswitch mode:' (.rodata 0x5574) @0x43d8, "
                 "fgets(60) @0x43e8, strtol @0x43f8 -> sim_mgr_api_set_switch_mode @0x440c",
        notes="0 manual, 1 automatic SIM switching"),
    ("sim_mgr_cli", "set_conn_monitor_mode"): Spec(
        ["\nconn mode:"], tier=1, source="static", sample=["1"],
        evidence="sim_mgr_cli handler 0x3628: printf '\\nconn mode:' (0x53ac) @0x3658, fgets @0x3668, strtol "
                 "-> sim_mgr_api_set_conn_monitor_mode @0x368c"),
    ("sim_mgr_cli", "set_sim_monitor_mode"): Spec(
        ["\nsim mode:"], tier=1, source="static", sample=["0"],
        evidence="sim_mgr_cli handler 0x3558: printf '\\nsim mode:' (0x5380) @0x3588, fgets @0x3598 -> "
                 "sim_mgr_api_set_sim_monitor_mode @0x35bc"),
    ("sim_mgr_cli", "set_conn_recovery_mode"): Spec(
        ["\n[recovery] conn recovery mode:"], tier=1, source="static", sample=["1"],
        evidence="sim_mgr_cli handler 0x3488: printf (0x5348) @0x34b8, fgets @0x34c8 -> "
                 "sim_mgr_api_set_conn_recovery_mode @0x34ec; RC line '...: [recovery] returned %d (%s)'"),
    ("sim_mgr_cli", "set_conn_configs"): Spec(
        ["connection interval:", "connection retriies:"], tier=1, source="static", sample=["30", "5"],
        evidence="sim_mgr_cli handler 0x3350: printf 'connection interval:' (0x52e8) @0x3386 fgets @0x339a, "
                 "printf 'connection retriies:' (0x5300) @0x33c0 fgets @0x33ce"),
    ("sim_mgr_cli", "set_sim_configs"): Spec(
        ["sim interval:"], tier=1, source="static", sample=["1800"],
        evidence="sim_mgr_cli handler 0x326c: printf 'sim interval:' (0x52b8) @0x329c, fgets @0x32ac"),
    ("sim_mgr_cli", "set_recovery_period"): Spec(
        ["\n[recovery] conn recovery period:"], tier=1, source="static", sample=["120"],
        evidence="sim_mgr_cli handler 0x319c: printf (0x527c) @0x31cc, fgets @0x31dc -> "
                 "sim_mgr_api_set_conn_recovery_period @0x3200"),
    ("sim_mgr_cli", "set_recovery_max_sim_switches"): Spec(
        ["\n[recovery] conn recovery max sim switch cnt:"], tier=1, source="static", sample=["6"],
        evidence="sim_mgr_cli handler 0x30cc: printf (0x521c) @0x30fc, fgets @0x310c -> "
                 "sim_mgr_api_set_recovery_max_sim_switches @0x3130"),
    ("sim_mgr_cli", "set_recovery_max_DNS_fail_cnt"): Spec(
        ["\n[recovery] conn recovery max DNS fail cnt:"], tier=1, source="static", sample=["3"],
        evidence="sim_mgr_cli handler 0x2ffc: printf (0x51b4) @0x302c, fgets @0x303c -> "
                 "sim_mgr_api_set_conn_recovery_max_DNS_fail_cnt @0x3060"),
    ("sim_mgr_cli", "set_recovery_max_mdm_disconnect_timeout"): Spec(
        ["\n[recovery] conn recovery max mdm disconnect timeout:"], tier=1, source="static", sample=["2"],
        evidence="sim_mgr_cli handler 0x2f2c: printf (0x5140) @0x2f5c, fgets @0x2f6c -> "
                 "sim_mgr_api_set_recovery_mdm_disconnect_timeout @0x2f90",
        notes="minutes; set_all_recovery_configs prompt gives the range [1-15]"),
    ("sim_mgr_cli", "set_recovery_max_mdm_reset_cnt"): Spec(
        ["\n[recovery] conn recovery max mdm reset cnt:"], tier=1, source="static", sample=["3"],
        evidence="sim_mgr_cli handler 0x2e5c: printf (0x50cc) @0x2e8c, fgets @0x2e9c -> "
                 "sim_mgr_api_set_recovery_max_mdm_reset_cnt @0x2ec0"),
    ("sim_mgr_cli", "set_recovery_max_device_reset_cnt"): Spec(
        ["\n[recovery] conn recovery max device reset cnt:"], tier=1, source="static", sample=["1"],
        evidence="sim_mgr_cli handler 0x2d8c: printf (0x5060) @0x2dbc, fgets @0x2dcc -> "
                 "sim_mgr_api_set_recovery_max_device_reset_cnt @0x2df0"),
    ("sim_mgr_cli", "set_recovery_max_system_reset_cnt"): Spec(
        ["\n[recovery] conn recovery max system_reset_cnt:"], tier=1, source="static", sample=["3"],
        evidence="sim_mgr_cli handler 0x2cbc: printf (0x4ff0) @0x2cec, fgets @0x2cfc -> "
                 "sim_mgr_api_set_conn_recovery_max_system_reset_cnt @0x2d20"),

    # ------------------------------------------------------------------ PCO account notice
    ("ccm2_cli", "accept_pco_event"): Spec(
        [], tier=1, source="static", mode="argv", argc=0,
        evidence="ccm2_cli handler 0x2034: no input; ccm2_accept_pco_event @0x2036, RC 'ccm_accept_pco_event returned'",
        notes="restores limited data access after a PCO 3/5 notice (stock 'pco' app 'Yes' button)"),

    # ------------------------------------------------------------------ SMS (sms_cli, stdin forms)
    ("sms_cli", "send"): Spec(
        ["Enter phone number: ", "\nEnter message: "], tier=1, source="static", sample=["5550100", "test"],
        evidence="sms_cli handler 0x1ea0: cmp r0,#3 / ble stdin @0x1ea2,0x1ebe; printf 'Enter phone number: ' "
                 "(0x2458) @0x1f3e fgets(512) @0x1f4e, printf '\\nEnter message: ' (0x2470) @0x1f7a fgets(512) "
                 "@0x1f86 (TAB/LF/CR replaced by NUL) -> strncpy(51)/strncpy(1261) -> sms_api_send @0x1efa",
        notes="fallback only: the service prefers REST POST SMSList/Outbox (sys_sendSMS). The CLI leaves the first 12 "
              "bytes of the sms_api_send request uninitialised, so it stays a static, device-unverified path"),
    ("sms_cli", "delete"): Spec(
        ["Enter mailbox id 0-3: PreInbox(0), Inbox(1), Outbox(2), Sentbox(3): ", "\nEnter message index 0-120: "],
        tier=1, source="static", sample=["1", "120"],
        evidence="sms_cli handler 0x1428: cmp r0,#3 / ble stdin @0x142a,0x1446; printf mailbox prompt (0x224c) "
                 "@0x14a8 scanf %d @0x14b0, printf index prompt (0x2294, r2=120) @0x14bc scanf %d @0x14c4 -> "
                 "sms_api_delete @0x1468"),
    ("sms_cli", "set_state"): Spec(
        ["Enter mailbox id 0-3: PreInbox(0), Inbox(1), Outbox(2), Sentbox(3): ", "\nEnter message index 0-120: ",
         "\nEnter message state Unread(0), Read(1): "], tier=1, source="static", sample=["1", "120", "1"],
        evidence="sms_cli handler 0x176c: cmp r0,#4 / ble stdin @0x176e,0x178c; printf+scanf %d @0x17fe/0x1806, "
                 "@0x1812/0x181a (index, r2=120), @0x1824/0x182c -> sms_api_set_state @0x17be",
        notes="fallback only: the service prefers REST PUT SMSList/Inbox (sys_setSMSReadState)"),
    ("sms_cli", "ab_add_entry"): Spec(
        ["\nEnter contact name: ", "\nEnter contact address: "], tier=1, source="static", sample=["Test", "5550100"],
        evidence="sms_cli handler 0x1a44: cmp r0,#3; printf name prompt (0x240c) @0x1abe fgets(81) @0x1acc, printf "
                 "address prompt (0x2424) @0x1af6 fgets(51) @0x1b00 -> sms_api_add_ab_entry @0x1a7a"),
    ("sms_cli", "ab_edit_entry"): Spec(
        ["\nEnter contact id 0-100: ", "\nEnter contact name: ", "\nEnter contact address: "], tier=1,
        source="static", sample=["100", "Test", "5550100"],
        evidence="sms_cli handler 0x1b50: cmp r0,#4; printf id prompt (0x21c0, r2=100) @0x1be0 fgets(20)+sscanf %d, "
                 "name prompt @0x1c08 fgets(81), address prompt @0x1c3a fgets(51) -> sms_api_edit_ab_entry @0x1b9c"),
    ("sms_cli", "ab_del_entry"): Spec(
        ["\nEnter contact id 0-100: "], tier=1, source="static", sample=["100"],
        evidence="sms_cli handler 0x1110: cmp r0,#2; printf id prompt (0x21c0) @0x1174 scanf %d @0x117e -> "
                 "sms_api_delete_ab_entry @0x1138"),

    # ------------------------------------------------------------------ data usage plan (dua_vzw_cli)
    ("dua_vzw_cli", "set_data_usage_settings_info"): Spec(
        ["Enter iface_type[0=INTERFACE_SIM,1=INTERFACE_ETH]: ", "Enter imsi : ", "Enter iccid:",
         "What is cycle_day(1 <> 31): ",
         "What is usage_limit [ 0(NONE), 1(20 MB), 2(25 MB), 3(100 MB), 4(250 MB), 5(500 MB), 6(1 GB), 7(2 GB), "
         "8(3 GB), 9(5 GB), 10(8 GB), 11(10 GB), 12(12 GB), 13(15 GB) 14(20 GB) ]:  ",
         "What is level1_threshold(1 <> 100): "], tier=1, source="static",
        sample=["0", "001010000000001", "8900100000000000001", "1", "0", "80"],
        evidence="dua_vzw_cli handler 0x19c4: subs r0,#7; cmp r0,#1; bls argv @0x19cc..0x19ee (argc 7/8 = argv); "
                 "stdin: printf iface prompt (0x1ed0) @0x19fa fgets(10)+strtol; iface 0 -> 'Enter imsi : ' (0x22cc) "
                 "@0x1b28 fgets(33), 'Enter iccid:' (0x22dc) @0x1b5c fgets(33); then cycle_day (0x22ec) @0x1a4e, "
                 "usage_limit (0x2310) @0x1a60, level1_threshold (0x23c4) @0x1a72 (scanf %d each); range checks "
                 "@0x1a80..0x1a92, limit index -> '0 GB'..'20 GB' -> dua_set_data_usage_settings_info @0x1c82",
        notes="SIM interface only (iface 0); iface 1 asks 'Enter mac or leave empty to skip: ' instead of imsi/iccid, "
              "which this spec does not cover. usage_limit is an index into the 15 fixed sizes"),
}
