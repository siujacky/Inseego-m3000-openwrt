"""Setter specs for the router-network area (LAN, DNS, IP passthrough, Ethernet, WAN, USB, routes, time).

Every prompt list below was established by static analysis of the vendor binaries in
reference/stock-fs/opt/nvtl/bin (objdump -M force-thumb + PC-relative string resolution; scripts in
%TEMP%/claude/area-network).  Addresses are link-time VAs of the handler (from the CLI's dispatch
table) and of the prompt strings in .rodata.  Every spec is written with source="static"; the sandbox
run of tools/device/verify_specs.py on 2026-10-31 (fixtures/sandbox/verified.json) upgrades the ones whose
prompts it saw to "sandbox".  Static-only specs are refused on a real unit (the UI shows a friendly note).

Conventions found in these CLIs:
- mifi_qcmap_cli / wan_mgr_cli: after a set that returns "a device reset is required" (router2 rc 6,
  printed as `returned 6`), several handlers print "... requires reset of device:" and ask
  "Do you want to reset the device Y/N:"; only a first character of 'Y'/'y' runs system("sys_reboot").
  That prompt is NOT part of the specs (see EOF_RESET): stdin is closed after the listed answers, the
  handler's answer buffer is zeroed before fgets(), so EOF means "no".  Services report
  restart_required and the UI offers a restart instead.
- Prompts are read with fgets() + CR/LF strip, so values may contain spaces (validated by services).
- Some handlers read fewer answers for certain values (noted per spec).  Answers that are not read
  stay unread in the pipe and are discarded when the process exits (none of those handlers has a
  later prompt that could consume them).
"""
from ..cli_specs import Spec

RESET_PROMPT = "Do you want to reset the device Y/N:"
EOF_RESET = ("The conditional reset prompt (only after rc 6) is deliberately left unanswered: the handler zeroes the "
             "answer buffer before fgets() and reboots only on a leading 'Y', so EOF never reboots; listing it would make "
             "the spec unverifiable in the sandbox (no daemon, so no rc 6).")


class RecordSpec(Spec):
    """A Spec whose repeated part is a *record* of several prompts.

    The core Spec repeats a single prompt; vendor list setters (static routes, DHCP reservations) ask
    for N records of k prompts each.  `record=(count_index, first_item_index, item_len)`.
    expected answers = len(prompts) - item_len + N * item_len, with min_items <= N <= max_items.
    """

    def __init__(self, prompts, record, max_items=None, **kw):
        super().__init__(prompts, **kw)
        self.record = record
        self.max_items = max_items

    def expected_count(self, values):
        ci, _first, item_len = self.record
        try:
            n = int(str(values[ci]), 0)
        except (IndexError, ValueError):
            raise ValueError("count answer missing or not a number")
        if n < self.min_items:
            raise ValueError("at least %d item(s) required" % self.min_items)
        if self.max_items is not None and n > self.max_items:
            raise ValueError("at most %d item(s) allowed" % self.max_items)
        return len(self.prompts) - item_len + n * item_len


SPECS = {
    # ------------------------------------------------------------------ mifi_qcmap_cli (router2)
    ("mifi_qcmap_cli", "set_ippt"): Spec(
        ["Enter active (0,1): ", "Enter Interface (1=USB, 2=Ethernet): ", "Enter hostname of the client:"],
        tier=2, source="static", sample=["1", "1", "test-pc"],
        evidence="mifi_qcmap_cli handler 0x27d0 (table 0x1b020): fgets 'Enter active (0,1): '@0x7d80; "
                 "dsm_is_orval()==0 -> 'Enter Interface (%d=USB, %d=Ethernet): '@0x7d98 printed with 1,2 "
                 "(Orval-only variant @0x7dc0 adds 4=Ethernet1); iface 1 -> 'Enter hostname of the client:'@0x7df8, "
                 "iface 2 -> 'Enter client MAC address : '@0x7e18; router2_set_ip_passthrough; rc==6 -> "
                 "'IPPT settings Change requires reset of device:'@0x7e34 + reset prompt @0x7e64. argc==5 argv form exists.",
        notes="answers: [active 0/1, interface 1|2, client hostname (USB) or MAC (Ethernet)]; the third prompt "
              "text depends on the interface; isOrval=0 on M3100 (dsm_cli is_orval). " + EOF_RESET),
    ("mifi_qcmap_cli", "set_ippt_feature_mode"): Spec(
        ["Enter IPPT Feature Mode value (0=Disable, 1=Enabled): "], tier=2, source="static", sample=["0"],
        evidence="handler 0x5864 (table 0x1b2cc): fgets prompt @0x8f6c -> router2_set_ippt_feature_mode; rc==6 -> "
                 "@0x8fa4 + reset prompt @0x7e64 (buffer memset(100) @0x5906 before fgets). argc==3 argv form exists.",
        notes=EOF_RESET),
    ("mifi_qcmap_cli", "set_dns_rebinding_status"): Spec(
        ["Enter DNS Rebinding status (0=Disable, 1=Enabled): "], tier=1, source="static", sample=["0"],
        evidence="handler 0x56c4 (table 0x1b2b4): fgets prompt @0x8ee4 -> router2_set_dns_rebinding_protection_enabled; "
                 "rc==6 -> @0x8f18 + reset prompt @0x7e64 (buffer memset(100) @0x5766 before fgets). argc==3 argv form.",
        notes=EOF_RESET),
    ("mifi_qcmap_cli", "set_dns_filter"): Spec(
        ["Enter DNS Filter Operation (0=Delete, 1=Add): ", "Enter DNS Filter status (0=Disable, 1=Enable): ",
         "Enter DNS Filter for Black or White List (0=White, 1=Black): ", "Enter URLs(separated by ','): "],
        tier=1, source="static",
        evidence="handler 0x5f5c (table 0x1b314): op prompt @0x927c; op==0 -> router2_set_dns_filter immediately "
                 "(delete); op!=0 -> status @0x92ac, list @0x92dc, URLs @0x931c (fgets 511 -> strncpy 512). "
                 "mifi_qcmapd runs dns_filter.sh: 'Add' replaces the whole list in /etc/data/dnsmasq.conf "
                 "(white list: server=/url/# + server=/#/, black list: server=/url/), 'Delete' removes it.",
        notes="answers: [op, status, list 0=white/1=black, comma-separated domains]; op=0 reads only the first "
              "answer. Domains are passed to a shell script by the daemon: services allow [A-Za-z0-9.-] only."),
    ("mifi_qcmap_cli", "set_device_name"): Spec(
        ["Enter DEVICE name: "], tier=1, source="static",
        evidence="handler 0x5118 (table 0x1b1d0): fgets(265) prompt @0x8d14, strlen<=255 -> router2_set_device_name; "
                 "rc==6 only prints 'Please reboot the device to take effect in all cases.'@0x8d28 (no prompt)."),
    ("mifi_qcmap_cli", "set_dns_name"): Spec(
        ["Enter DNS name: "], tier=1, source="static",
        evidence="handler 0x2a94 (table 0x1b044): fgets(265) prompt @0x7edc -> router2_set_dns_name(name, strlen); "
                 "no reset prompt. argc==3 argv form exists."),
    ("mifi_qcmap_cli", "set_iface_type"): Spec(
        ["Enter Ethernet eth0 interface type(2=LAN, 3=DHCP WAN): "], tier=1, source="static", sample=["2"],
        evidence="handler 0x62e0 (table 0x1b200): no argv form; fgets prompt @0x9b6c; Orval-only second prompt "
                 "@0x9ba4 (eth1; isOrval=0 on M3100); mifi_qcmap_emd_set_ethernet_iface_type; rc==6 -> @0x9bdc + "
                 "reset prompt @0x7e64 (memset(100) @0x63d8; fgets NULL skips the strip).",
        notes="answers: [2 (LAN) or 3 (DHCP WAN)]. " + EOF_RESET),
    ("mifi_qcmap_cli", "set_static_routes"): Spec(
        ["Enter static routes feature status (0=Disable, 1=Enable): "], tier=1, source="static",
        evidence="handler 0x5a18 (table 0x1b2e4): fgets prompt @0x9004 -> router2_set_static_routes_enabled; "
                 "argc==3 argv form exists; no reset prompt."),
    ("mifi_qcmap_cli", "set_static_route_list"): RecordSpec(
        ["Enter the number of records: ", "Enter Route Type (IPV4 = 4  IPV6 = 6): ",
         "Enter the Network/Host [x.x.x.x/x] : ", "Enter Gateway [x.x.x.x] : ", "Enter WAN Type Index:",
         "Enter Metric: "], record=(0, 1, 5), max_items=25, tier=1, source="static",
        evidence="handler 0x5bb4 (table 0x1b2fc): count prompt @0x7fdc (N<=0 sets an empty list); per record "
                 "type @0x90f8, network @0x9120 (strcpy 51), gateway @0x9148 (strcpy 46), prints 'Interface Type "
                 "Index: %d Type:[%s]' for 0..9 (router2_utils_get_wan_type_str: 0 No WAN, 1 WWAN, 2 WiFi WAN, "
                 "3 Ethernet WAN, 4 USB WAN, 5 APPs WAN, 6 DM WAN, 7-9 WGVPN_0..2), WAN index @0x9188, metric "
                 "@0x91a0; router2_set_static_route_list. argv form: argc-3 == 5*N, N<=25.",
        notes="answers: [N, (family 4|6, network/prefix, gateway, wan index, metric) x N]; the whole list is replaced."),
    ("mifi_qcmap_cli", "wan_set_prio"): Spec(
        ["Enter the first priority WAN: WWAN(1)/Etherenet(3) :", "Enter the second priority WAN: WWAN(1)/Etherenet(3) :"],
        tier=1, source="static", sample=["1", "3"],
        evidence="handler 0x6c60 (table 0x1b29c): prompts @0xa06c/@0xa0a4 printed with 1,3 -> "
                 "mifi_qcmap_set_wan_priorities; rc==0x320006 -> @0xa0dc + reset prompt @0x7e64 (memset(80) @0x6d84). "
                 "argc==4 argv form.", notes=EOF_RESET),

    # ------------------------------------------------------------------ wan_mgr_cli
    ("wan_mgr_cli", "set_auto_switch"): Spec(
        ["Enter WAN Auto Switch Value(0/1): "], tier=1, source="static",
        evidence="wan_mgr_cli handler 0x1d74 (table 0x17050): fgets prompt @0x47e4 -> wan_mgr_set_auto_switch; "
                 "argc==3 argv form."),
    ("wan_mgr_cli", "set_manual_wan"): Spec(
        ["Enter the manual WAN Type (1=WWAN, 2=Wifi, 3=Ethernet 4=USB): "], tier=1, source="static",
        evidence="handler 0x359c (table 0x17038): reads priorities + supported count first (returns with "
                 "'Failed to get ...' before prompting when wan_mgrd is down), prints @0x5214 hint, prompt @0x5258 -> "
                 "wan_mgr_set_manual_wan; argc==3 argv form.",
        notes="auto switch must be off first"),
    ("wan_mgr_cli", "set_wan_priority_list"): Spec(
        ["Enter the WAN Type (1=WWAN, 2=Wifi, 3=Ethernet 4=USB): ", "Enter WAN priority for WAN[1]:",
         "Enter the WAN Type (1=WWAN, 2=Wifi, 3=Ethernet 4=USB): ", "Enter WAN priority for WAN[2]:"],
        tier=1, source="static", sample=["1", "1", "3", "2"],
        evidence="handler 0x37b4 (table 0x17068): no argv form; N = wan_mgr_get_supported_wan_count() (2 on this "
                 "unit); per WAN type @0x4c18 (fgets 9) + priority @0x52d4 (fgets 10, index 1-based); "
                 "wan_mgr_set_wan_priorities; reset required -> @0x5330 + reset prompt @0x5384 (buffer sp+80 zeroed "
                 "by vst @0x39dc/@0x39e2 before fgets(9) @0x3a02).",
        notes="fixed to 2 WANs: the service checks 'Supported WAN count' == 2 before calling. Needs wan_mgrd: the "
              "sandbox cannot show these prompts (BLOCKED). " + EOF_RESET),
    ("wan_mgr_cli", "set_wan_failback"): Spec(
        ["Enter the failback feature enable or disable (0:disable, 1:enable): ",
         "Enter the failback timer Range(1-1440 min): ",
         "Enter the WWAN data traffic check timer Range(1-1440 min): ",
         "Enter the WWAN data traffic check limit Range(1-10240 KB): "], tier=1, source="static",
        evidence="handler 0x3ae8 (table 0x17080): argc==2 -> prompts @0x5464 @0x54ac @0x54dc @0x5518; argc==6 argv "
                 "form; other argc -> usage @0x53dc; wan_mgr_set_failback_settings."),
    ("wan_mgr_cli", "set_wan_monitor_properties"): Spec(
        ["Enter the WAN monitor feature enable or disable (0:disable, 1:enable): ",
         "Enter the use connected device state feature enable or disable (0:disable, 1:enable): ",
         "Enter the interface retry count, Range(0-10): ",
         "Enter the routed monitor interval, Range(30-86400 sec) : ",
         "Enter the online monitor interval, Range(1-1440 min): ",
         "Enter the failed monitor interval, Range(10-600 sec): ",
         "Enter the first DNS URL: ", "Enter the second dns url: ", "Enter the third dns url: "], tier=1,
        source="static",
        evidence="handler 0x3de8 (table 0x17098): argc==2 -> prompts @0x5670 @0x56b8 @0x5710 @0x5740 @0x577c @0x57b4 "
                 "@0x57ec @0x5808 @0x5824 (URLs fgets 255); argc==11 argv form uses a DIFFERENT order (usage @0x55bc, "
                 "9th arg = auto-switch) so stdin is used; wan_mgr_set_wan_monitor_settings."),
    ("wan_mgr_cli", "set_self_recovery_status"): Spec(
        ["Enter WAN Self Recovery Status(0/1): "], tier=1, source="static",
        evidence="handler 0x1ec8 (table 0x17110): fgets prompt @0x4808 -> wan_mgr_set_self_recovery_status; argc==3."),
    ("wan_mgr_cli", "set_waiting_timeout"): Spec(
        ["Enter the no connectivity timeout value"], tier=1, source="static",
        evidence="handler 0x218c (table 0x170e0): fgets prompt @0x48e8 -> wan_mgr_set_no_con_timeout; argc==3."),

    # ------------------------------------------------------------------ usb_cli
    ("usb_cli", "set_tether_enabled"): Spec(
        ["What is tether_enabled (1=on, 0=off): "], tier=1, source="static",
        evidence="usb_cli handler 0x16c0 (table 0x13044): fgets prompt @0x2100, strtol with full-string check -> "
                 "usb_set_tether_enabled; argc==3 argv form."),
    ("usb_cli", "mode_switch"): Spec(
        ["What is switch mode (0=EUM, 1=DBG, 2=ENT 3=P_ENT): ", "What is reboot (0 | 1): "], tier=2, source="static",
        evidence="handler 0x1a5c (table 0x13098): prompt @0x2274 printed with 0,1,2,3, fgets+strtol; prompt @0x22ac; "
                 "usb_mode_switch(mode, reboot!=0); argc==4 argv form takes EUM/DBG/ENT/P_ENT names. The 8800L mod "
                 "ROM runs '(echo 1; echo 0) | usb_cli mode_switch' (debug, no reboot).",
        notes="only 0 (standard) and 1 (debug/ADB) are offered; reboot always 0"),
    ("usb_cli", "ippt_settings"): Spec(
        ["What is rndis_disabled (1=on, 0=off): "], tier=2, source="static",
        evidence="handler 0x1790 (table 0x130a4): fgets prompt @0x2154 -> usb_control_ippt_settings; argc==3."),

    # ------------------------------------------------------------------ dsm_cli
    ("dsm_cli", "set_default_usb_mode"): Spec(
        ["Enter 0 for Debug, and 1 for End user mode:"], tier=2, source="static", mode="argv", argc=1,
        evidence="dsm_cli handler 0x1928 (table 0x15134): argc==3 -> strtol(argv[2]) -> dsm_set_usb_default_mode; "
                 "otherwise prompt @0x2e90 + scanf('%d'). The M2000 mod ROM runs 'dsm_cli set_default_usb_mode 0'.",
        notes="argv: [0 debug | 1 end user]"),

    # ------------------------------------------------------------------ ccm2_cli
    ("ccm2_cli", "set_wifi_while_usbteth"): Spec(
        ["What is enabled status [0|1]:  "], tier=1, source="static",
        evidence="ccm2_cli handler 0x289c (table 0x170d4): fgets(60) prompt @0x4b40 -> "
                 "ccm2_set_wifi_enabled_while_usbtethered; argc==3 argv form."),
    ("ccm2_cli", "set_static_ip"): Spec(
        ["What is ip address [x.x.x.x]:  "], tier=1, source="static",
        evidence="handler 0x265c (table 0x170f8): fgets prompt @0x4a40, inet_pton(AF_INET) -> ccm2_set_static_ip; "
                 "argc==3 argv form."),
    ("ccm2_cli", "set_ntp_url"): Spec(
        ["What is ntp min delay (seconds):  ", "What is ntp retry count:  ", "What is ntp url1:  ",
         "\nWhat is ntp url2:  ", "\nWhat is ntp url3:  "], tier=1, source="static",
        evidence="handler 0x384c (table 0x17110): prompts @0x50e8 @0x510c (fgets 60 + strtol) @0x5128 @0x513c @0x5154 "
                 "(fgets 128) -> ccm2_set_ntp_url; argc==7 argv form."),
    ("ccm2_cli", "set_wan_prio"): Spec(
        ["What is WAN Priority 1 Index:  ", "What is WAN Priority 2 Index:  ", "What is WAN Priority 3 Index:  "],
        tier=1, source="static",
        evidence="handler 0x2540 (table 0x1714c): prints 'Index:[%d] Wan:[%s]' for 1..3, then prompt @0x49f8 x3 "
                 "(fgets 60 + strtol) -> ccm2_wan_set_wan_prio; argc==5 argv form.",
        notes="answers: WAN index (1=WWAN, 2=WiFi WAN, 3=Ethernet WAN) for priority 1, 2, 3"),
    ("ccm2_cli", "wan_connect"): Spec(
        [], tier=1, source="static", mode="argv", argc=0,
        evidence="handler 0x251c (table 0x17158): no input; ccm2_wan_connect()."),
    ("ccm2_cli", "wan_disconnect"): Spec(
        [], tier=1, source="static", mode="argv", argc=0,
        evidence="handler 0x24f8 (table 0x17164): no input; ccm2_wan_disconnect()."),

    # ------------------------------------------------------------------ sysintcli (sys_* layer used by the stock UI)
    # Command records are {char name[100]; handler} in .data; one-shot `sysintcli <cmd>` (main @0xad10:
    # qsort + strcmp lookup, sys_sdkInitialize, handler, sys_sdkShutdown).  Its camelCase reads
    # (getReservedIPList 0xc948, getLanMTU 0xc824, getGatewaySettings 0xcf5c: no input) are classified as reads
    # by cli_specs (get[A-Z]*), so only the setters need specs.
    ("sysintcli", "setReservedIPList"): RecordSpec(
        ["Enter number of reserved IP addresses you want to set:", "Enter host name:",
         "Enter type (0 - 10 (see sys_device_type_e sys_interface_types.h) :", "Enter mac (xx:xx:xx:xx:xx:xx):",
         "Enter reserved ip address (x.x.x.x):"], record=(0, 1, 4), min_items=1, max_items=10, tier=1,
        source="static",
        evidence="handler 0x16c94 (record 0x4cdf0): count @0x2f9f4 via scanf('%d') + getc (eats the newline); per "
                 "item fgets(79): host name @0x2fa2c (strncpy 64), type @0x2fa40 (strtol base 10), mac @0x2fa84 "
                 "(18), ip @0x2faa4 (16); sys_setDHCPReservedAddrList (libsysintclient 0x24589) validates only the "
                 "list pointer, count 0..25, MAC and type 0..13 (IsValid @0x245be..0x2464a), then per item "
                 "dmdb_get_device/strncpy(ip,15)/dmdb_set_device: only the listed devices change and an empty ip "
                 "clears that device's reservation.",
        notes="answers: [N, (name, type 0-10, mac, ip or '' to remove) x N]"),
    ("sysintcli", "setLanMTU"): Spec(
        ["Enter MTU: "], tier=1, source="static",
        evidence="handler 0xc890 (record 0x4cd20): fgets prompt @0x2a538 + strtol -> sys_setLanMTU."),
    ("sysintcli", "setGatewaySettings"): Spec(
        ["Enter Gateway Setting Mode 0 (Use default gateway and netmask), 1 (Use gateway and netmask from modem), "
         "2 (Configure gateway and netmask manually)", "Enter Gateway Address:", "Enter Netmask:"], tier=2,
        source="static", sample=["2", "192.168.1.1", "255.255.255.0"],
        evidence="handler 0x1fd24 (record 0x4d470): mode prompt @0x34470 (fgets 79 + strtol); mode==2 -> "
                 "'Enter Gateway Address:'@0x34504 and 'Enter Netmask:'@0x3451c; sys_setGatewaysettings.",
        notes="mode 0/1 read only the first answer"),
}

# Realistic answers for sandbox re-verification (tools/device/verify_specs.py): current values of the
# reference unit, never "reboot"/"RNDIS off"/"end-user mode" style values.
_SAMPLES = {
    ("mifi_qcmap_cli", "set_device_name"): ["Router"],
    ("mifi_qcmap_cli", "set_dns_name"): ["router.home"],
    ("mifi_qcmap_cli", "set_static_routes"): ["0"],
    ("mifi_qcmap_cli", "set_static_route_list"): ["1", "4", "10.0.0.0/8", "192.168.1.254", "1", "0"],
    ("mifi_qcmap_cli", "set_dns_filter"): ["1", "0", "1", "example.com"],
    ("wan_mgr_cli", "set_auto_switch"): ["1"],
    ("wan_mgr_cli", "set_manual_wan"): ["1"],
    ("wan_mgr_cli", "set_wan_failback"): ["1", "120", "1", "10"],
    ("wan_mgr_cli", "set_wan_monitor_properties"): ["1", "0", "3", "30", "3", "10", "www.google.com", "www.yahoo.com",
                                                    "www.example.com"],
    ("wan_mgr_cli", "set_self_recovery_status"): ["1"],
    ("wan_mgr_cli", "set_waiting_timeout"): ["30"],
    ("usb_cli", "set_tether_enabled"): ["1"],
    ("usb_cli", "mode_switch"): ["1", "0"],
    ("usb_cli", "ippt_settings"): ["0"],
    ("dsm_cli", "set_default_usb_mode"): ["0"],
    ("ccm2_cli", "set_wifi_while_usbteth"): ["1"],
    ("ccm2_cli", "set_static_ip"): ["0.0.0.0"],
    ("ccm2_cli", "set_ntp_url"): ["604800", "60", "time.google.com", "pool.ntp.org", "time.nist.gov"],
    ("ccm2_cli", "set_wan_prio"): ["3", "2", "1"],
    ("sysintcli", "setReservedIPList"): ["1", "test", "0", "02:00:00:00:00:99", "192.168.1.99"],
    ("sysintcli", "setLanMTU"): ["1428"],
}
for _key, _sample in _SAMPLES.items():
    if not SPECS[_key].sample:
        SPECS[_key].sample = _sample
